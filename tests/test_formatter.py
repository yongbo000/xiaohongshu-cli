"""Unit tests for formatter (no network required)."""

import yaml

from xhs_cli.formatter import coerce_int, extract_note_id, format_count, parse_note_reference, print_yaml


class TestFormatCount:
    def test_small_number(self):
        assert format_count(123) == "123"

    def test_wan(self):
        assert format_count(12345) == "1.2万"

    def test_yi(self):
        assert format_count(123456789) == "1.2亿"

    def test_string_input(self):
        assert format_count("5678") == "5678"

    def test_string_large(self):
        assert format_count("50000") == "5.0万"


class TestCoerceInt:
    def test_int_input(self):
        assert coerce_int(3) == 3

    def test_string_input(self):
        assert coerce_int("42") == 42

    def test_invalid_string_falls_back(self):
        assert coerce_int("10+") == 0


class TestExtractNoteId:
    def test_plain_id(self):
        assert extract_note_id("abc123def") == "abc123def"

    def test_explore_url(self):
        result = extract_note_id("https://www.xiaohongshu.com/explore/abc123def")
        assert result == "abc123def"

    def test_url_with_params(self):
        result = extract_note_id("https://www.xiaohongshu.com/explore/abc123?xsec_token=xxx")
        assert result == "abc123"

    def test_discovery_url(self):
        result = extract_note_id("https://www.xiaohongshu.com/discovery/item/abc123")
        assert result == "abc123"

    def test_trailing_slash(self):
        result = extract_note_id("https://www.xiaohongshu.com/explore/abc123/")
        assert result == "abc123"


class TestParseNoteReference:
    def test_extracts_token_and_source(self):
        note_id, token, source = parse_note_reference(
            "https://www.xiaohongshu.com/explore/abc123?xsec_token=token-1&xsec_source=pc_search"
        )
        assert note_id == "abc123"
        assert token == "token-1"
        assert source == "pc_search"


class TestPrintYamlAstralScalars:
    """Regression tests for the PyYAML double-quoted astral-escape patch.

    Payloads containing \\n / \\t force PyYAML's double-quoted scalar style,
    where upstream write_double_quoted() escapes non-BMP characters (emoji)
    as \\Uxxxxxxxx sequences. The fork's _AstralSafeDumper must emit them
    verbatim instead, exactly like BMP CJK characters.
    """

    def _note_payload(self) -> dict:
        return {
            "ok": True,
            "schema_version": "1",
            "data": {
                "note_id": "66e3b2c1000000001e02a9f1",
                "xsec_token": "ABfR2pQwErTyUiOp1234567890==",
                "has_more": False,
                "cursor": "68c1f2a9000000002301d4b7",
                # \n and \t force the double-quoted scalar style.
                "desc": "凌晨三点🌙写完代码倒头就睡💤\n\t第二天继续搬砖🐄\n打卡✅",
            },
        }

    def test_emoji_written_verbatim_not_escaped(self, capsys):
        print_yaml(self._note_payload())
        out = capsys.readouterr().out
        assert "💤" in out
        assert "🐄" in out
        assert "\\U0001" not in out

    def test_roundtrip_preserves_payload(self, capsys):
        payload = self._note_payload()
        print_yaml(payload)
        loaded = yaml.safe_load(capsys.readouterr().out)
        assert loaded == payload
        assert loaded["data"]["desc"] == payload["data"]["desc"]

    def test_schema_fields_roundtrip_individually(self, capsys):
        print_yaml(self._note_payload())
        loaded = yaml.safe_load(capsys.readouterr().out)
        assert loaded["ok"] is True
        assert loaded["schema_version"] == "1"
        data = loaded["data"]
        assert data["note_id"] == "66e3b2c1000000001e02a9f1"
        assert data["xsec_token"] == "ABfR2pQwErTyUiOp1234567890=="
        assert data["has_more"] is False
        assert data["cursor"] == "68c1f2a9000000002301d4b7"

    def test_literal_escape_text_in_double_quoted_context(self, capsys):
        # The body literally contains the 10 characters \U0001F4A4 (not the
        # emoji); \n\t force double-quoted style, so the backslash itself
        # must be escaped and must survive the round-trip unchanged.
        text = "教程：直接输入 \\U0001F4A4 也能显示睡觉表情\n\t第二行"
        payload = {"desc": text}
        print_yaml(payload)
        out = capsys.readouterr().out
        assert "\\\\U0001F4A4" in out
        assert yaml.safe_load(out) == payload

    def test_literal_escape_text_in_plain_context(self, capsys):
        # No control characters, so PyYAML keeps the plain (unquoted) style;
        # the literal backslash sequence must survive round-trip untouched.
        text = "教程：直接输入 \\U0001F4A4 也能显示睡觉表情"
        payload = {"desc": text}
        print_yaml(payload)
        out = capsys.readouterr().out
        assert f"desc: {text}" in out
        assert yaml.safe_load(out) == payload

    def test_other_astral_codepoints_verbatim(self, capsys):
        # U+1F600 (emoji) and U+20000 (CJK Ext-B rare character) must both
        # pass through verbatim; \t forces the double-quoted scalar style.
        payload = {"desc": "表情\U0001F600与生僻字\U00020000\t制表"}
        print_yaml(payload)
        out = capsys.readouterr().out
        assert "\U0001F600" in out
        assert "\U00020000" in out
        assert "\\U" not in out
        assert yaml.safe_load(out) == payload
