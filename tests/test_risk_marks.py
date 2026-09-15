"""Tests for resource-level risk marks (D2, CLI read side + self-healing)."""

from __future__ import annotations

import time

import pytest
import yaml
from click.testing import CliRunner

from xhs_cli import risk_marks
from xhs_cli.cli import cli
from xhs_cli.client import XhsClient
from xhs_cli.exceptions import NeedVerifyError

runner = CliRunner()


def _active_mark(kind: str, value: str, **kwargs) -> dict:
    mark = {
        "kind": kind,
        "value": value,
        "marked_at": time.time() * 1000,
        "ttl_seconds": 3600,
        "reason": "http_461",
    }
    mark.update(kwargs)
    return mark


@pytest.fixture(autouse=True)
def _clear_resource_context():
    yield
    risk_marks.clear_current_resource()


class TestMarksFile:
    def test_missing_file_is_no_marks(self):
        assert risk_marks.load_marks() == []
        risk_marks.guard([("note", "n1")])  # must not raise

    def test_corrupt_file_silently_degrades(self):
        risk_marks.get_risk_marks_path().write_text("{broken")
        assert risk_marks.load_marks() == []
        risk_marks.guard([("note", "n1")])  # must not raise

    def test_malformed_entries_are_skipped(self):
        risk_marks.save_marks([_active_mark("note", "n1")])
        path = risk_marks.get_risk_marks_path()
        path.write_text(
            '{"version": 1, "marks": [{"kind": "bogus"}, {"kind": "note", "value": "n2",'
            ' "marked_at": 1, "ttl_seconds": 0}, null]}'
        )
        assert risk_marks.load_marks() == []

    def test_add_mark_persists_0600_and_dedupes(self):
        risk_marks.add_mark("note", "n1", reason="http_461")
        path = risk_marks.get_risk_marks_path()
        assert (path.stat().st_mode & 0o777) == 0o600

        first = risk_marks.load_marks()
        assert len(first) == 1
        assert first[0]["kind"] == "note"
        assert first[0]["value"] == "n1"
        assert first[0]["reason"] == "http_461"

        risk_marks.add_mark("note", "n1", reason="http_471")
        marks = risk_marks.load_marks()
        assert len(marks) == 1
        assert marks[0]["reason"] == "http_471"

    def test_add_mark_rejects_invalid_kind(self):
        risk_marks.add_mark("bogus", "n1")
        assert risk_marks.load_marks() == []


class TestFindAndGuard:
    def test_active_mark_blocks(self):
        risk_marks.save_marks([_active_mark("keyword", "猫粮")])
        mark = risk_marks.find_active_mark([("keyword", "猫粮")])
        assert mark is not None
        with pytest.raises(NeedVerifyError):
            risk_marks.guard([("keyword", "猫粮")])

    def test_expired_mark_does_not_block(self):
        mark = _active_mark("note", "n1")
        mark["marked_at"] = (time.time() - 7200) * 1000  # 2h ago, ttl 1h
        risk_marks.save_marks([mark])
        assert risk_marks.find_active_mark([("note", "n1")]) is None
        risk_marks.guard([("note", "n1")])  # must not raise

    def test_other_resource_not_blocked(self):
        risk_marks.save_marks([_active_mark("note", "n1")])
        assert risk_marks.find_active_mark([("note", "n2")]) is None

    def test_sub_comments_matches_comment_or_note_mark(self):
        risk_marks.save_marks([_active_mark("note", "note-9")])
        pairs = [("comment", "c1"), ("note", "note-9")]
        mark = risk_marks.find_active_mark(pairs)
        assert mark is not None and mark["kind"] == "note"

    def test_error_maps_to_verification_required(self):
        from xhs_cli.error_codes import error_code_for_exception

        risk_marks.save_marks([_active_mark("note", "n1")])
        with pytest.raises(NeedVerifyError) as exc_info:
            risk_marks.guard([("note", "n1")])
        assert error_code_for_exception(exc_info.value) == "verification_required"


class TestSelfHealing:
    def test_mark_current_resource(self):
        risk_marks.set_current_resource("note", "n1")
        risk_marks.mark_current_resource(reason="http_461")
        marks = risk_marks.load_marks()
        assert len(marks) == 1
        assert marks[0]["kind"] == "note"
        assert marks[0]["value"] == "n1"
        assert marks[0]["reason"] == "http_461"

    def test_mark_current_resource_without_context_is_noop(self):
        risk_marks.clear_current_resource()
        risk_marks.mark_current_resource(reason="http_461")
        assert risk_marks.load_marks() == []

    def test_client_captcha_self_marks_current_resource(self, monkeypatch):
        import httpx

        monkeypatch.setattr("xhs_cli.client.time.sleep", lambda _: None)
        risk_marks.set_current_resource("keyword", "猫粮")
        request = httpx.Request("POST", "https://edith.xiaohongshu.com/api/test")
        response = httpx.Response(471, headers={}, request=request)

        client = XhsClient({"a1": "cookie"}, request_delay=0)
        try:
            with pytest.raises(NeedVerifyError):
                client._handle_response(response)
        finally:
            client.close()

        mark = risk_marks.find_active_mark([("keyword", "猫粮")])
        assert mark is not None
        assert mark["reason"] == "http_471"


class TestCommandEntryGuards:
    """Acceptance: a marked resource fast-fails with verification_required and
    zero upstream requests."""

    def _mock_cookies(self, monkeypatch):
        monkeypatch.setattr(
            "xhs_cli.commands._common.get_cookies",
            lambda source, force_refresh=False: (None, {"a1": "cookie"}),
        )

    def _spy_transport(self, monkeypatch):
        calls = []

        def _fail_transport(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("transport must not be called for a marked resource")

        monkeypatch.setattr(XhsClient, "_request_with_retry", _fail_transport)
        return calls

    def test_search_marked_keyword_fast_fails(self, monkeypatch):
        self._mock_cookies(monkeypatch)
        calls = self._spy_transport(monkeypatch)
        risk_marks.save_marks([_active_mark("keyword", "猫粮")])

        result = runner.invoke(cli, ["search", "猫粮", "--yaml"])

        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "verification_required"
        assert calls == []

    def test_read_marked_note_fast_fails(self, monkeypatch):
        self._mock_cookies(monkeypatch)
        calls = self._spy_transport(monkeypatch)
        risk_marks.save_marks([_active_mark("note", "abc123def456abc123def456")])

        result = runner.invoke(cli, ["read", "abc123def456abc123def456", "--yaml"])

        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "verification_required"
        assert calls == []

    def test_comments_marked_note_fast_fails(self, monkeypatch):
        self._mock_cookies(monkeypatch)
        calls = self._spy_transport(monkeypatch)
        risk_marks.save_marks([_active_mark("note", "abc123def456abc123def456")])

        result = runner.invoke(cli, ["comments", "abc123def456abc123def456", "--yaml"])

        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "verification_required"
        assert calls == []

    def test_sub_comments_marked_comment_fast_fails(self, monkeypatch):
        self._mock_cookies(monkeypatch)
        calls = self._spy_transport(monkeypatch)
        risk_marks.save_marks([_active_mark("comment", "comment-1", note_id="note-1")])

        result = runner.invoke(cli, ["sub-comments", "note-1", "comment-1", "--yaml"])

        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "verification_required"
        assert calls == []

    def test_unmarked_search_proceeds_to_client(self, monkeypatch):
        self._mock_cookies(monkeypatch)
        seen = {}

        def _fake_search_notes(self, **kwargs):
            seen["called"] = True
            return {"items": []}

        monkeypatch.setattr(XhsClient, "search_notes", _fake_search_notes)

        result = runner.invoke(cli, ["search", "猫粮", "--yaml"])

        assert result.exit_code == 0, result.output
        assert seen.get("called") is True
