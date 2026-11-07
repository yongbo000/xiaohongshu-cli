"""Tests for the stable `xhs read --projection` transport projection."""

import json

import pytest
import yaml
from click.testing import CliRunner

from xhs_cli.cli import cli
from xhs_cli.formatter_normalizers import project_note_read

runner = CliRunner()
_LONG = "x" * 120
_ALL_KEYS = [
    "note_id",
    "title",
    "desc",
    "like_count",
    "collected_count",
    "comment_count",
    "image_count",
    "image_list",
]
_IMAGE_KEYS = ["index", "file_id", "width", "height"]
_BANNED_KEYS = {
    "url",
    "url_pre",
    "url_default",
    "info_list",
    "cover",
    "stream",
    "live_photo",
    "video",
    "media_v2",
}


def _image(index: int, *, width=1080, height=1440) -> dict:
    return {
        "file_id": f"file-{index}-{_LONG}",
        "width": width,
        "height": height,
        "live_photo": False,
        "url_default": f"https://sns-img.example.com/{index}/default.jpg?sign={_LONG}",
        "url_pre": f"https://sns-img.example.com/{index}/pre.jpg?sign={_LONG}",
        "info_list": [
            {"image_scene": "FD_PRV_WEBP", "url": f"https://sns-img.example.com/{index}/prv.webp?sign={_LONG}"},
        ],
    }


NOTE_RESPONSE = {
    "items": [
        {
            "id": "note-1",
            "xsec_token": "token-1",
            "note_card": {
                "type": "normal",
                "title": "精读笔记标题",
                "desc": "正文内容",
                "user": {"user_id": "u-1", "nickname": "作者甲"},
                "interact_info": {"liked_count": "100", "collected_count": "50", "comment_count": "10"},
                "image_list": [_image(0), _image(1), _image(2)],
            },
        }
    ]
}

FLAT_NOTE_RESPONSE = {
    "note_id": "flat-note-1",
    "type": "normal",
    "title": "HTML 笔记标题",
    "desc": "HTML 正文",
    "interact_info": {"liked_count": "7", "collected_count": "4", "comment_count": "2"},
    "image_list": [_image(0, width=720, height=960)],
}


class _ReadClient:
    def __init__(self, response):
        self._response = response

    def get_note_detail(self, note_id, **kwargs):
        return self._response


@pytest.fixture(autouse=True)
def _no_note_index_writes(monkeypatch):
    noop = lambda *args, **kwargs: None  # noqa: E731
    monkeypatch.setattr("xhs_cli.commands.reading.cache_note_context", noop)
    monkeypatch.setattr("xhs_cli.commands.reading.save_index_from_items", noop)
    monkeypatch.setattr("xhs_cli.commands.reading.save_index_from_notes", noop)


def _invoke(monkeypatch, client, args):
    monkeypatch.setattr(
        "xhs_cli.commands._common.run_client_action",
        lambda ctx, action: action(client),
    )
    monkeypatch.setattr(
        "xhs_cli.commands.reading.run_client_action",
        lambda ctx, action: action(client),
    )
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return result


def _json_data(result):
    return json.loads(result.output)["data"]


def _yaml_data(result):
    return yaml.safe_load(result.output)["data"]


def _contains_any_key(value, keys) -> bool:
    if isinstance(value, dict):
        return any(key in keys or _contains_any_key(item, keys) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_any_key(item, keys) for item in value)
    return False


def _assert_no_signed_media(result, data) -> None:
    lowered = result.output.lower()
    for leaked in ("http://", "https://", "sign=", "url_pre", "url_default", "xhscdn.com", "imageview2"):
        assert leaked not in lowered
    assert not _contains_any_key(data, _BANNED_KEYS)


class TestReadProjectionDefault:
    def test_default_read_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _ReadClient(NOTE_RESPONSE), ["read", "note-1", "--json"])
        assert _json_data(result) == NOTE_RESPONSE

    def test_existing_switches_unchanged(self, monkeypatch):
        no_media = _invoke(monkeypatch, _ReadClient(NOTE_RESPONSE), ["read", "note-1", "--no-media", "--json"])
        fields = _invoke(
            monkeypatch,
            _ReadClient(NOTE_RESPONSE),
            ["read", "note-1", "--fields", "note_id,title,desc", "--json"],
        )
        assert _json_data(no_media)["items"][0]["note_card"]["desc"] == "正文内容"
        assert _json_data(fields)["items"][0]["note_card"] == {
            "note_id": "note-1",
            "title": "精读笔记标题",
            "desc": "正文内容",
        }

    def test_projection_conflicts_with_fields_and_no_media(self, monkeypatch):
        for args in (
            ["read", "note-1", "--projection", "all", "--fields", "title"],
            ["read", "note-1", "--projection", "all", "--no-media"],
        ):
            result = runner.invoke(cli, args)
            assert result.exit_code != 0
            assert "cannot be combined" in result.output

    def test_help_documents_projection(self):
        result = runner.invoke(cli, ["read", "--help"])
        assert result.exit_code == 0, result.output
        help_text = " ".join(result.output.split()).lower()
        assert "--projection" in help_text
        assert "default: off" in help_text
        assert "never emits signed media urls" in help_text


class TestReadProjectionAll:
    def test_all_contract_and_stable_order(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _ReadClient(NOTE_RESPONSE),
            ["read", "note-1", "--projection", "all", "--json"],
        )
        data = _json_data(result)

        assert list(data) == _ALL_KEYS
        assert data["note_id"] == "note-1"
        assert data["title"] == "精读笔记标题"
        assert data["desc"] == "正文内容"
        assert data["like_count"] == 100
        assert data["collected_count"] == 50
        assert data["comment_count"] == 10
        assert data["image_count"] == 3
        assert [image["index"] for image in data["image_list"]] == [0, 1, 2]
        assert [image["file_id"] for image in data["image_list"]] == [
            f"file-{index}-{_LONG}" for index in range(3)
        ]
        assert [(image["width"], image["height"]) for image in data["image_list"]] == [
            (1080, 1440),
            (1080, 1440),
            (1080, 1440),
        ]
        for image in data["image_list"]:
            assert list(image) == _IMAGE_KEYS
        _assert_no_signed_media(result, data)

    def test_all_yaml_matches_json(self, monkeypatch):
        client = _ReadClient(NOTE_RESPONSE)
        as_json = _invoke(
            monkeypatch, client, ["read", "note-1", "--projection", "all", "--json"]
        )
        as_yaml = _invoke(
            monkeypatch, client, ["read", "note-1", "--projection", "all", "--yaml"]
        )
        assert _json_data(as_json) == _yaml_data(as_yaml)

    def test_image_subset(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _ReadClient(NOTE_RESPONSE),
            ["read", "note-1", "--projection", "images", "--json"],
        )
        data = _json_data(result)

        assert list(data) == ["note_id", "title", "image_count", "image_list"]
        assert data["image_count"] == 3
        assert len(data["image_list"]) == 3
        _assert_no_signed_media(result, data)

    def test_metrics_subset(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _ReadClient(NOTE_RESPONSE),
            ["read", "note-1", "--projection", "metrics", "--json"],
        )
        data = _json_data(result)

        assert list(data) == [
            "note_id",
            "title",
            "like_count",
            "collected_count",
            "comment_count",
        ]
        assert data["like_count"] == 100
        assert data["collected_count"] == 50
        assert data["comment_count"] == 10
        assert "image_list" not in data
        _assert_no_signed_media(result, data)

    def test_flat_html_note_shape(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _ReadClient(FLAT_NOTE_RESPONSE),
            ["read", "flat-note-1", "--projection", "all", "--json"],
        )
        data = _json_data(result)

        assert data["note_id"] == "flat-note-1"
        assert data["title"] == "HTML 笔记标题"
        assert data["desc"] == "HTML 正文"
        assert (data["like_count"], data["collected_count"], data["comment_count"]) == (7, 4, 2)
        assert data["image_count"] == 1
        assert data["image_list"] == [
            {
                "index": 0,
                "file_id": f"file-0-{_LONG}",
                "width": 720,
                "height": 960,
            }
        ]
        _assert_no_signed_media(result, data)

    def test_image_order_is_source_order(self):
        order = ["third", "first", "second"]
        projected = project_note_read(
            {
                "note_id": "n",
                "image_list": [
                    {"file_id": file_id, "width": index + 1, "height": index + 2}
                    for index, file_id in enumerate(order)
                ],
            },
            projection="images",
        )
        assert [image["file_id"] for image in projected["image_list"]] == order
        assert [image["index"] for image in projected["image_list"]] == [0, 1, 2]


class TestReadProjectionTolerance:
    def test_missing_fields_become_null_or_empty(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _ReadClient({"items": [{"id": "partial-note", "note_card": {}}]}),
            ["read", "partial-note", "--projection", "all", "--json"],
        )
        data = _json_data(result)

        assert data == {
            "note_id": "partial-note",
            "title": "",
            "desc": "",
            "like_count": None,
            "collected_count": None,
            "comment_count": None,
            "image_count": None,
            "image_list": [],
        }

    def test_empty_image_list_is_zero_not_unknown(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _ReadClient({"note_id": "empty-media", "title": "t", "image_list": [], "interact_info": {}}),
            ["read", "empty-media", "--projection", "all", "--json"],
        )
        data = _json_data(result)

        assert data["image_count"] == 0
        assert data["image_list"] == []
        assert data["like_count"] is None

    def test_malformed_image_entries_do_not_crash(self, monkeypatch):
        payload = {
            "note_id": "bad-images",
            "title": "t",
            "image_list": [
                "not-a-dict",
                None,
                {"file_id": "only-id"},
                {"width": "1080", "height": 1920},
                {"file_id": "", "width": "1.2万", "height": True},
            ],
        }
        result = _invoke(
            monkeypatch,
            _ReadClient(payload),
            ["read", "bad-images", "--projection", "images", "--json"],
        )
        data = _json_data(result)

        assert data["image_count"] == 5
        assert data["image_list"] == [
            {"index": 0, "file_id": None, "width": None, "height": None},
            {"index": 1, "file_id": None, "width": None, "height": None},
            {"index": 2, "file_id": "only-id", "width": None, "height": None},
            {"index": 3, "file_id": None, "width": 1080, "height": 1920},
            {"index": 4, "file_id": None, "width": None, "height": None},
        ]

    def test_empty_or_invalid_payload_returns_stable_empty_projection(self):
        expected = {
            "note_id": "",
            "title": "",
            "desc": "",
            "like_count": None,
            "collected_count": None,
            "comment_count": None,
            "image_count": None,
            "image_list": [],
        }
        assert project_note_read({}, projection="all") == expected
        assert project_note_read(None, projection="all") == expected
        assert project_note_read({"items": []}, projection="all") == expected
        assert project_note_read({"items": ["not-a-dict"]}, projection="all") == expected

    def test_unrecognised_counts_stay_null(self):
        projected = project_note_read(
            {
                "note_id": "n",
                "interact_info": {
                    "liked_count": "1.2万",
                    "collected_count": "unknown",
                    "comment_count": True,
                },
            },
            projection="metrics",
        )
        assert projected["like_count"] is None
        assert projected["collected_count"] is None
        assert projected["comment_count"] is None

    def test_unknown_projection_rejected(self):
        with pytest.raises(ValueError, match="unknown read projection"):
            project_note_read({}, projection="everything")


class TestReadProjectionSize:
    @staticmethod
    def _large_note() -> dict:
        long_url = "https://sns-img-qc.xhscdn.com/" + "a" * 2800 + "?sign=" + "b" * 2800
        images = []
        for index in range(20):
            image = _image(index)
            image["url_default"] = long_url
            image["url_pre"] = long_url
            images.append(image)
        return {
            "items": [
                {
                    "id": "large-note",
                    "xsec_token": "token",
                    "note_card": {
                        "title": "20 图笔记",
                        "desc": "正文",
                        "interact_info": {
                            "liked_count": "12345",
                            "collected_count": "6789",
                            "comment_count": "101",
                        },
                        "image_list": images,
                    },
                }
            ]
        }

    def test_20_images_projection_is_far_below_64kb(self, monkeypatch, capsys):
        client = _ReadClient(self._large_note())
        full = _invoke(monkeypatch, client, ["read", "large-note", "--json"])
        projected = _invoke(
            monkeypatch,
            client,
            ["read", "large-note", "--projection", "all", "--json"],
        )

        full_bytes = len(full.output.encode("utf-8"))
        projected_bytes = len(projected.output.encode("utf-8"))
        with capsys.disabled():
            print(f"\nread 20-image payload: full={full_bytes}B projection={projected_bytes}B")

        assert full_bytes > 64 * 1024
        assert projected_bytes < 16 * 1024
        assert projected_bytes < 64 * 1024
        data = _json_data(projected)
        assert data["image_count"] == 20
        assert len(data["image_list"]) == 20
        _assert_no_signed_media(projected, data)
