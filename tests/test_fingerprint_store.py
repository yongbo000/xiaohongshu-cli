"""Tests for persistent device fingerprint and session counters (D1a)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from xhs_cli import fingerprint_store
from xhs_cli.cookies import save_cookies
from xhs_cli.signing import PersistentFingerprintGenerator, PersistentSessionManager, _config

REPO_ROOT = Path(__file__).resolve().parents[1]

DUMMY_FP = {key: f"v-{key}" for key in fingerprint_store.REQUIRED_FP_KEYS} | {"x1": "UA", "x7": "GPU"}


def run_py(script: str, home: Path) -> subprocess.CompletedProcess:
    """Run a Python snippet in a fresh process with HOME pointed at ``home``."""
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


class TestFingerprintStore:
    def test_roundtrip_and_permissions(self):
        fingerprint_store.save_fingerprint(dict(DUMMY_FP))
        path = fingerprint_store.get_fingerprint_path()
        assert (path.stat().st_mode & 0o777) == 0o600
        loaded = fingerprint_store.load_fingerprint()
        assert loaded is not None
        assert loaded["x7"] == "GPU"

    def test_missing_file_returns_none(self):
        assert fingerprint_store.load_fingerprint() is None
        assert fingerprint_store.load_session() is None

    def test_corrupt_file_returns_none(self):
        fingerprint_store.get_fingerprint_path().write_text("{broken")
        assert fingerprint_store.load_fingerprint() is None
        assert fingerprint_store.load_session() is None

    def test_incompatible_fingerprint_is_discarded(self):
        fingerprint_store.save_fingerprint({"x7": "GPU"})  # missing required keys
        assert fingerprint_store.load_fingerprint() is None

    def test_session_roundtrip(self):
        fingerprint_store.save_session(111, 222, 333)
        assert fingerprint_store.load_session() == {
            "page_load_timestamp": 111,
            "sequence_value": 222,
            "window_props_length": 333,
        }

    def test_invalid_session_returns_none(self):
        fingerprint_store.save_fingerprint(dict(DUMMY_FP))
        path = fingerprint_store.get_fingerprint_path()
        store = json.loads(path.read_text())
        store["session"] = {"sequence_value": "oops"}
        path.write_text(json.dumps(store))
        assert fingerprint_store.load_session() is None

    def test_reset_state_deletes_file(self):
        fingerprint_store.save_fingerprint(dict(DUMMY_FP))
        fingerprint_store.reset_state()
        assert not fingerprint_store.get_fingerprint_path().exists()

    def test_save_cookies_rotates_fingerprint(self, tmp_path, monkeypatch):
        monkeypatch.setattr("xhs_cli.cookies.get_config_dir", lambda: tmp_path)
        monkeypatch.setattr("xhs_cli.cookies.get_cookie_path", lambda: tmp_path / "cookies.json")
        fingerprint_store.save_fingerprint(dict(DUMMY_FP))
        assert fingerprint_store.load_fingerprint() is not None

        save_cookies({"a1": "cookie"})

        assert fingerprint_store.load_fingerprint() is None


class TestPersistentGenerators:
    def test_fingerprint_generated_once_then_reused(self):
        gen = PersistentFingerprintGenerator(_config)
        cookies = {"a1": "a" * 48}
        first = gen.generate(cookies, "UA-test")
        assert fingerprint_store.load_fingerprint() is not None

        second = PersistentFingerprintGenerator(_config).generate({"a1": "b" * 48}, "UA-test")
        # Hardware fields stay identical across generators/processes...
        for key in ("x4", "x5", "x7", "x8", "x9", "x43", "x53"):
            assert second[key] == first[key], key
        # ...while per-request dynamic fields are refreshed.
        assert second["x57"] == f"a1={'b' * 48}"
        assert second["x1"] == "UA-test"

    def test_session_manager_restores_and_advances(self):
        fingerprint_store.save_session(1_000_000, 50, 60)

        manager = PersistentSessionManager(_config)
        manager.update_state()

        assert manager.page_load_timestamp == 1_000_000
        # xhshow steps are randint(0, 1), so counters may stay equal but never regress
        assert manager.sequence_value >= 50
        assert manager.window_props_length >= 60

        saved = fingerprint_store.load_session()
        assert saved is not None
        assert saved["sequence_value"] == manager.sequence_value

    def test_session_manager_falls_back_to_fresh_state(self):
        manager = PersistentSessionManager(_config)
        manager.update_state()  # no persisted state: must not raise
        assert manager.sequence_value > 0


class TestCrossProcessStability:
    def test_fingerprint_and_session_stable_across_processes(self, tmp_path):
        """Acceptance: two separate process invocations share one fingerprint
        and one continuous signing session."""
        home = tmp_path / "home"
        home.mkdir()

        a1 = "a" * 48
        sign_snippet = (
            "from xhs_cli.signing import sign_main_api; "
            f"sign_main_api('GET', '/api/test', {{'a1': '{a1}'}})"
        )

        proc1 = run_py(sign_snippet, home)
        assert proc1.returncode == 0, proc1.stderr

        store_file = home / ".xiaohongshu-cli" / "fingerprint.json"
        assert store_file.exists()
        first_fp = json.loads(store_file.read_text())["fingerprint"]
        assert fingerprint_store.REQUIRED_FP_KEYS.issubset(first_fp.keys())
        first_session = fingerprint_store.load_session(store_file)
        assert first_session is not None

        proc2 = run_py(
            f"""
import json
from xhs_cli import fingerprint_store
from xhs_cli.signing import PersistentFingerprintGenerator, _config, sign_main_api

fp = fingerprint_store.load_fingerprint()
assert fp is not None, "fingerprint must survive process restart"
gen = PersistentFingerprintGenerator(_config)
fp2 = gen.generate({{"a1": "{'b' * 48}"}}, "UA")
for key in ("x4", "x5", "x7", "x8", "x9", "x43", "x53"):
    assert fp2[key] == fp[key], key

before = fingerprint_store.load_session()
sign_main_api("GET", "/api/test", {{"a1": "{a1}"}})
after = fingerprint_store.load_session()
assert after["page_load_timestamp"] == before["page_load_timestamp"]
# xhshow steps are randint(0, 1): counters advance or hold, never regress/reset
assert after["sequence_value"] >= before["sequence_value"]
print(json.dumps({{"seq_before": before["sequence_value"]}}))
""",
            home,
        )
        assert proc2.returncode == 0, proc2.stderr
        seq_before = json.loads(proc2.stdout.strip())["seq_before"]
        assert seq_before == first_session["sequence_value"]
