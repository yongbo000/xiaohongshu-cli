"""Tests for account-level risk state persistence (D3)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from xhs_cli import risk_state
from xhs_cli.client import XhsClient
from xhs_cli.exceptions import NeedVerifyError

REPO_ROOT = Path(__file__).resolve().parents[1]


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


class TestRiskStateFile:
    def test_missing_file_returns_defaults(self):
        state = risk_state.load_risk_state()
        assert state["captcha_count_24h"] == 0
        assert state["cooldown_until"] == 0.0
        assert state["delay_multiplier"] == 1.0
        assert risk_state.cooldown_remaining(state) == 0.0

    def test_corrupt_file_silently_degrades(self):
        risk_state.get_risk_state_path().write_text("{not json")
        state = risk_state.load_risk_state()
        assert state == risk_state.default_state()

    def test_non_dict_file_silently_degrades(self):
        risk_state.get_risk_state_path().write_text('["oops"]')
        assert risk_state.load_risk_state() == risk_state.default_state()

    def test_record_captcha_sets_first_tier_and_persists_0600(self):
        now = time.time()
        state = risk_state.record_captcha(now=now)
        assert state["captcha_count_24h"] == 1
        assert state["cooldown_until"] == pytest.approx(now + 45 * 60)
        assert state["delay_multiplier"] == 2.0
        assert state["last_captcha_at"] == pytest.approx(now)

        path = risk_state.get_risk_state_path()
        assert (path.stat().st_mode & 0o777) == 0o600
        reloaded = risk_state.load_risk_state()
        assert reloaded["captcha_count_24h"] == 1
        assert reloaded["cooldown_until"] == pytest.approx(now + 45 * 60)

    def test_captcha_tiers_escalate_within_24h(self):
        now = time.time()
        risk_state.record_captcha(now=now)
        state = risk_state.record_captcha(now=now + 10)
        assert state["captcha_count_24h"] == 2
        assert state["cooldown_until"] == pytest.approx(now + 10 + 4 * 3600)
        assert state["delay_multiplier"] == 4.0

        state = risk_state.record_captcha(now=now + 20)
        assert state["captcha_count_24h"] == 3
        assert state["cooldown_until"] == pytest.approx(now + 20 + 24 * 3600)
        assert state["delay_multiplier"] == 8.0

    def test_cooldown_until_takes_later_value(self):
        now = time.time()
        state = risk_state.default_state()
        state["cooldown_until"] = now + 10 * 3600  # gateway already set a later cooldown
        risk_state.save_risk_state(state)
        state = risk_state.record_captcha(now=now)
        assert state["cooldown_until"] == pytest.approx(now + 10 * 3600)

    def test_timestamps_outside_24h_window_are_pruned(self):
        now = time.time()
        state = risk_state.default_state()
        state["captcha_timestamps"] = [now - 25 * 3600]
        risk_state.save_risk_state(state)
        loaded = risk_state.load_risk_state()
        assert loaded["captcha_count_24h"] == 0

    def test_cooldown_remaining_rereads_disk(self):
        assert risk_state.cooldown_remaining() == 0.0
        state = risk_state.default_state()
        state["cooldown_until"] = time.time() + 600
        risk_state.save_risk_state(state)
        assert 590 < risk_state.cooldown_remaining() <= 600


class TestClientIntegration:
    def test_cooldown_fast_fail_makes_zero_upstream_requests(self):
        """Acceptance: during cooldown the transport is never invoked."""
        risk_state.record_captcha()

        client = XhsClient({"a1": "cookie"}, request_delay=0)
        calls = []

        class _FakeHttp:
            def request(self, *args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("transport must not be called during cooldown")

            def close(self):
                return None

        client._http = _FakeHttp()
        try:
            with pytest.raises(NeedVerifyError):
                client._request_with_retry("GET", "https://edith.xiaohongshu.com/api/test")
        finally:
            client.close()
        assert calls == []

    def test_expired_cooldown_allows_requests(self):
        state = risk_state.default_state()
        state["cooldown_until"] = time.time() - 1
        risk_state.save_risk_state(state)

        request = httpx.Request("GET", "https://edith.xiaohongshu.com/api/test")
        response = httpx.Response(200, json={"success": True, "data": {}}, request=request)

        client = XhsClient({"a1": "cookie"}, request_delay=0)

        class _FakeHttp:
            def request(self, *args, **kwargs):
                return response

            def close(self):
                return None

        client._http = _FakeHttp()
        try:
            resp = client._request_with_retry("GET", "https://edith.xiaohongshu.com/api/test")
        finally:
            client.close()
        assert resp is response

    def test_captcha_response_persists_state(self, monkeypatch):
        monkeypatch.setattr("xhs_cli.client.time.sleep", lambda _: None)
        request = httpx.Request("GET", "https://edith.xiaohongshu.com/api/test")
        response = httpx.Response(
            461,
            headers={"verifytype": "captcha", "verifyuuid": "uuid-1"},
            request=request,
        )

        client = XhsClient({"a1": "cookie"}, request_delay=0)
        try:
            with pytest.raises(NeedVerifyError):
                client._handle_response(response)
        finally:
            client.close()

        state = risk_state.load_risk_state()
        assert state["captcha_count_24h"] == 1
        assert state["cooldown_until"] > time.time()
        assert state["delay_multiplier"] == 2.0

    def test_persisted_delay_multiplier_raises_request_delay(self):
        state = risk_state.default_state()
        state["delay_multiplier"] = 4.0
        risk_state.save_risk_state(state)

        client = XhsClient({"a1": "cookie"})
        try:
            assert client._request_delay == pytest.approx(4.0)
            assert client._base_request_delay == pytest.approx(4.0)
        finally:
            client.close()

    def test_enforce_cooldown_can_be_disabled(self):
        risk_state.record_captcha()
        request = httpx.Request("GET", "https://edith.xiaohongshu.com/api/test")
        response = httpx.Response(200, json={"success": True}, request=request)

        client = XhsClient({"a1": "cookie"}, request_delay=0, enforce_cooldown=False)

        class _FakeHttp:
            def request(self, *args, **kwargs):
                return response

            def close(self):
                return None

        client._http = _FakeHttp()
        try:
            resp = client._request_with_retry("GET", "https://edith.xiaohongshu.com/api/test")
        finally:
            client.close()
        assert resp is response


class TestCrossProcessPersistence:
    def test_cooldown_and_count_survive_process_restart(self, tmp_path):
        """Acceptance: state recorded by one process blocks the next process's
        first request without any upstream call."""
        home = tmp_path / "home"
        home.mkdir()

        proc1 = run_py(
            "from xhs_cli import risk_state; risk_state.record_captcha()",
            home,
        )
        assert proc1.returncode == 0, proc1.stderr

        state_file = home / ".xiaohongshu-cli" / "risk_state.json"
        assert state_file.exists()
        saved = json.loads(state_file.read_text())
        assert saved["captcha_count_24h"] == 1
        assert saved["cooldown_until"] > time.time()

        proc2 = run_py(
            """
from xhs_cli import risk_state
from xhs_cli.client import XhsClient
from xhs_cli.exceptions import NeedVerifyError

state = risk_state.load_risk_state()
assert state["captcha_count_24h"] == 1, state
assert risk_state.cooldown_remaining() > 0

client = XhsClient({"a1": "cookie"}, request_delay=0)
calls = []
client._http.request = lambda *a, **k: calls.append(1)
try:
    client._request_with_retry("GET", "https://edith.xiaohongshu.com/api/test")
except NeedVerifyError:
    pass
else:
    raise SystemExit("expected NeedVerifyError during cooldown")
assert calls == [], "transport must not be called after restart"
print("ok")
""",
            home,
        )
        assert proc2.returncode == 0, proc2.stderr
        assert "ok" in proc2.stdout
