from __future__ import annotations

import os

import pytest

os.environ.setdefault("OUTPUT", "rich")


@pytest.fixture(autouse=True)
def _isolated_state_files(tmp_path, monkeypatch):
    """Redirect persisted risk/fingerprint state files to a per-test tmp dir.

    Keeps tests hermetic: risk_state.json / risk_marks.json / fingerprint.json
    under the real ~/.xiaohongshu-cli are never read or written by tests.
    """
    for module in (
        "xhs_cli.risk_state",
        "xhs_cli.risk_marks",
        "xhs_cli.fingerprint_store",
    ):
        monkeypatch.setattr(f"{module}.get_config_dir", lambda: tmp_path)
    return tmp_path
