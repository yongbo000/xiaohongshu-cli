"""Tests for --compact / --no-media / --fields structured-output trimming."""

import json

import pytest
import yaml
from click.testing import CliRunner

from xhs_cli.cli import cli
from xhs_cli.formatter_normalizers import compact_search_results, pick_note_fields, strip_note_media
from xhs_cli.formatter_utils import success_payload

runner = CliRunner()

ITEM_WHITELIST = {"note_id", "xsec_token", "title", "author", "liked", "note_type"}
MEDIA_KEYS = {"image_list", "cover", "stream", "live_photo"}

_LONG = "x" * 120


def _image(index: int) -> dict:
    return {
        "file_id": f"file-{index}-{_LONG}",
        "live_photo": False,
        "url_default": f"https://sns-img.example.com/{index}/default.jpg?{_LONG}",
        "url_pre": f"https://sns-img.example.com/{index}/pre.jpg?{_LONG}",
        "info_list": [
            {"image_scene": "FD_PRV_WEBP", "url": f"https://sns-img.example.com/{index}/prv.webp?{_LONG}"},
            {"image_scene": "FD_WM_WEBP", "url": f"https://sns-img.example.com/{index}/wm.webp?{_LONG}"},
        ],
    }


def _stream() -> dict:
    def entry(codec: str) -> list[dict]:
        return [{
            "master_url": f"https://sns-video.example.com/{codec}/master.mp4?{_LONG}",
            "backup_urls": [f"https://sns-video.example.com/{codec}/backup1.mp4?{_LONG}"],
            "duration": 15000,
        }]

    return {"h264": entry("h264"), "h265": entry("h265"), "av1": entry("av1")}


SEARCH_RESPONSE = {
    "items": [
        {
            "id": "note-1",
            "xsec_token": "token-1",
            "model_type": "note",
            "note_card": {
                "type": "normal",
                "display_title": "第一篇笔记标题",
                "user": {"user_id": "u-1", "nickname": "作者甲"},
                "interact_info": {"liked_count": "123", "collected_count": "45", "comment_count": "6"},
                "cover": {"url_default": f"https://sns-img.example.com/cover.jpg?{_LONG}"},
                "image_list": [_image(0), _image(1), _image(2)],
            },
        },
        {
            "id": "note-2",
            "xsec_token": "token-2",
            "model_type": "note",
            "note_card": {
                "type": "video",
                "display_title": "第二条视频笔记",
                "user": {"user_id": "u-2", "nickname": "作者乙"},
                "interact_info": {"liked_count": "9"},
                "video": {"media": {"stream": _stream()}},
            },
        },
    ],
    "has_more": True,
    "hot_words": [{"word": "大家都在搜的词", "hot_value": 12345}],
}

FEED_RESPONSE = {
    "items": [
        {
            "id": "feed-1",
            "xsec_token": "feed-token-1",
            "note_card": {
                "type": "normal",
                "display_title": "推荐笔记",
                "user": {"nickname": "推荐作者"},
                "interact_info": {"liked_count": "77"},
                "image_list": [_image(3)],
            },
        }
    ],
}

PAGED_RESPONSE = {
    "notes": [
        {
            "note_id": "paged-1",
            "xsec_token": "paged-token-1",
            "display_title": "分页笔记一",
            "type": "normal",
            "user": {"nickname": "分页作者"},
            "interact_info": {"liked_count": "7"},
            "cover": {"url_default": f"https://sns-img.example.com/paged.jpg?{_LONG}"},
            "images_list": [_image(4), _image(5)],
        },
        {
            "note_id": "paged-2",
            "xsec_token": "paged-token-2",
            "display_title": "分页笔记二",
            "type": "video",
            "user": {"nickname": "分页作者"},
            "interact_info": {"liked_count": "8"},
        },
    ],
    "has_more": True,
    "cursor": "cursor-page-2",
}

CREATOR_RESPONSE = {
    "note_list": [
        {
            "note_id": "mine-1",
            "title": "我自己的笔记",
            "type": "video",
            "liked_count": "3",
            "comment_count": "1",
            "status": 0,
            "cover": {"url": f"https://sns-img.example.com/mine.jpg?{_LONG}"},
        }
    ],
    "has_more": False,
    "cursor": "0",
}

NOTE_DETAIL_RESPONSE = {
    "items": [
        {
            "id": "note-1",
            "xsec_token": "token-1",
            "note_card": {
                "type": "video",
                "title": "精读笔记标题",
                "desc": "正文内容",
                "time": 1757700000000,
                "ip_location": "上海",
                "user": {"user_id": "u-1", "nickname": "作者甲"},
                "interact_info": {"liked_count": "100", "collected_count": "50", "comment_count": "10"},
                "tag_list": [{"id": "tag-1", "name": "话题一", "type": "topic"}],
                "image_list": [_image(0), _image(1), _image(2)],
                "cover": {"url_default": f"https://sns-img.example.com/cover.jpg?{_LONG}", "file_id": "cover-file"},
                "video": {
                    "consumer": {"origin_video_key": "origin-key"},
                    "media": {"stream": _stream()},
                },
            },
        }
    ]
}

FLAT_NOTE_RESPONSE = {
    "note_id": "note-1",
    "type": "normal",
    "title": "HTML 笔记标题",
    "desc": "HTML 正文",
    "time": 1757700000000,
    "last_update_time": 1757800000000,
    "user": {"user_id": "u-1", "nickname": "作者甲"},
    "interact_info": {"liked_count": "100"},
    "tag_list": [{"id": "tag-1", "name": "话题一"}],
    "image_list": [_image(0)],
    "cover": {"url_default": f"https://sns-img.example.com/cover.jpg?{_LONG}"},
}


@pytest.fixture(autouse=True)
def _no_note_index_writes(monkeypatch):
    noop = lambda *args, **kwargs: None  # noqa: E731
    monkeypatch.setattr("xhs_cli.commands.reading.cache_note_context", noop)
    monkeypatch.setattr("xhs_cli.commands.reading.save_index_from_items", noop)
    monkeypatch.setattr("xhs_cli.commands.reading.save_index_from_notes", noop)
    monkeypatch.setattr("xhs_cli.commands.social.save_index_from_notes", noop)
    monkeypatch.setattr("xhs_cli.commands.creator.save_index_from_notes", noop)


def _invoke(monkeypatch, client, args):
    monkeypatch.setattr(
        "xhs_cli.commands._common.run_client_action",
        lambda ctx, action: action(client),
    )
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return result


def _json_payload(result):
    return json.loads(result.output)


def _yaml_payload(result):
    return yaml.safe_load(result.output)


def _contains_any_key(value, keys) -> bool:
    if isinstance(value, dict):
        return any(key in keys or _contains_any_key(item, keys) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_any_key(item, keys) for item in value)
    return False


class _SearchClient:
    def search_notes(self, **kwargs):
        return SEARCH_RESPONSE


class _FeedClient:
    def get_home_feed(self):
        return FEED_RESPONSE

    def get_hot_feed(self, category):
        return FEED_RESPONSE


class _UserPostsClient:
    def get_user_notes(self, user_id, cursor=""):
        return PAGED_RESPONSE


class _FavoritesClient:
    def get_user_favorites(self, user_id, cursor=""):
        return PAGED_RESPONSE


class _LikesClient:
    def get_user_likes(self, user_id, cursor=""):
        return PAGED_RESPONSE


class _CreatorClient:
    def get_creator_note_list(self, page=0):
        return CREATOR_RESPONSE


class _ReadClient:
    def __init__(self, response):
        self._response = response

    def get_note_detail(self, note_id, **kwargs):
        return self._response


class TestSearchCompact:
    def test_compact_json_whitelist(self, monkeypatch):
        result = _invoke(monkeypatch, _SearchClient(), ["search", "kw", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert data["has_more"] is True
        assert data["hot_words"] == SEARCH_RESPONSE["hot_words"]
        assert len(data["items"]) == 2
        for item in data["items"]:
            assert set(item) == ITEM_WHITELIST
        first, second = data["items"]
        assert first["note_id"] == "note-1"
        assert first["xsec_token"] == "token-1"
        assert first["note_type"] == "image"
        assert second["note_type"] == "video"

    def test_compact_yaml_whitelist(self, monkeypatch):
        result = _invoke(monkeypatch, _SearchClient(), ["search", "kw", "--compact", "--yaml"])
        data = _yaml_payload(result)["data"]

        assert set(data) == {"items", "has_more", "hot_words"}
        assert {key for item in data["items"] for key in item} == ITEM_WHITELIST

    def test_default_output_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _SearchClient(), ["search", "kw", "--json"])
        payload = _json_payload(result)

        assert payload["ok"] is True
        assert payload["schema_version"] == "1"
        assert payload["data"] == SEARCH_RESPONSE


class TestFeedCompact:
    def test_feed_compact(self, monkeypatch):
        result = _invoke(monkeypatch, _FeedClient(), ["feed", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert set(data) == {"items", "has_more"}
        assert data["has_more"] is False
        (item,) = data["items"]
        assert set(item) == ITEM_WHITELIST
        assert item["note_id"] == "feed-1"
        assert item["xsec_token"] == "feed-token-1"

    def test_hot_compact_yaml(self, monkeypatch):
        result = _invoke(monkeypatch, _FeedClient(), ["hot", "--compact", "--yaml"])
        data = _yaml_payload(result)["data"]

        (item,) = data["items"]
        assert set(item) == ITEM_WHITELIST

    def test_feed_default_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _FeedClient(), ["feed", "--json"])
        assert _json_payload(result)["data"] == FEED_RESPONSE

    def test_compact_does_not_affect_rich_render(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "rich")
        result = _invoke(monkeypatch, _FeedClient(), ["feed", "--compact"])
        assert "推荐页" in result.output
        assert "推荐笔记" in result.output


class TestPagedCompact:
    def test_user_posts_compact(self, monkeypatch):
        result = _invoke(monkeypatch, _UserPostsClient(), ["user-posts", "user-1", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert set(data) == {"items", "has_more", "cursor"}
        assert data["has_more"] is True
        assert data["cursor"] == "cursor-page-2"
        first, second = data["items"]
        assert set(first) == ITEM_WHITELIST
        assert first["xsec_token"] == "paged-token-1"
        assert first["author"] == "分页作者"
        assert first["note_type"] == "image"
        assert second["note_type"] == "video"

    def test_favorites_compact_yaml(self, monkeypatch):
        result = _invoke(monkeypatch, _FavoritesClient(), ["favorites", "user-1", "--compact", "--yaml"])
        data = _yaml_payload(result)["data"]

        assert data["cursor"] == "cursor-page-2"
        assert {key for item in data["items"] for key in item} == ITEM_WHITELIST

    def test_likes_compact(self, monkeypatch):
        result = _invoke(monkeypatch, _LikesClient(), ["likes", "user-1", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert data["has_more"] is True
        assert len(data["items"]) == 2

    def test_my_notes_compact(self, monkeypatch):
        result = _invoke(monkeypatch, _CreatorClient(), ["my-notes", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert set(data) == {"items", "has_more", "cursor"}
        (item,) = data["items"]
        assert set(item) == ITEM_WHITELIST
        assert item["note_id"] == "mine-1"
        assert item["liked"] == "3"
        assert item["note_type"] == "video"

    def test_user_posts_default_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _UserPostsClient(), ["user-posts", "user-1", "--json"])
        assert _json_payload(result)["data"] == PAGED_RESPONSE

    def test_my_notes_default_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _CreatorClient(), ["my-notes", "--yaml"])
        assert _yaml_payload(result)["data"] == CREATOR_RESPONSE


class TestReadTrimming:
    READ_WHITELIST = ["note_id", "title", "desc", "user", "time", "interact_info", "tag_list"]

    def test_no_media_strips_only_media_keys(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        result = _invoke(monkeypatch, client, ["read", "note-1", "--no-media", "--json"])
        data = _json_payload(result)["data"]

        assert not _contains_any_key(data, MEDIA_KEYS)
        note = data["items"][0]["note_card"]
        assert note["title"] == "精读笔记标题"
        assert note["desc"] == "正文内容"
        assert note["video"]["consumer"]["origin_video_key"] == "origin-key"
        assert data["items"][0]["xsec_token"] == "token-1"

    def test_no_media_keeps_everything_else(self, monkeypatch):
        client = _ReadClient(FLAT_NOTE_RESPONSE)
        result = _invoke(monkeypatch, client, ["read", "note-1", "--no-media", "--yaml"])
        data = _yaml_payload(result)["data"]

        assert not _contains_any_key(data, MEDIA_KEYS)
        expected = {key: value for key, value in FLAT_NOTE_RESPONSE.items() if key not in MEDIA_KEYS}
        assert data == expected

    def test_fields_whitelist(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        result = _invoke(
            monkeypatch,
            client,
            ["read", "note-1", "--fields", ",".join(self.READ_WHITELIST), "--json"],
        )
        data = _json_payload(result)["data"]

        (item,) = data["items"]
        assert set(item) == {"note_card"}
        note = item["note_card"]
        assert set(note) == set(self.READ_WHITELIST)
        assert note["note_id"] == "note-1"
        assert note["user"] == NOTE_DETAIL_RESPONSE["items"][0]["note_card"]["user"]

    def test_fields_implies_no_media(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        result = _invoke(monkeypatch, client, ["read", "note-1", "--fields", "title,image_list", "--json"])
        note = _json_payload(result)["data"]["items"][0]["note_card"]

        assert note == {"title": "精读笔记标题"}

    def test_fields_on_flat_html_note(self, monkeypatch):
        client = _ReadClient(FLAT_NOTE_RESPONSE)
        result = _invoke(monkeypatch, client, ["read", "note-1", "--fields", "note_id,title,desc", "--yaml"])
        data = _yaml_payload(result)["data"]

        assert data == {
            "note_id": "note-1",
            "title": "HTML 笔记标题",
            "desc": "HTML 正文",
        }

    def test_fields_and_no_media_combined(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        result = _invoke(
            monkeypatch,
            client,
            ["read", "note-1", "--no-media", "--fields", "note_id,title", "--json"],
        )
        note = _json_payload(result)["data"]["items"][0]["note_card"]
        assert note == {"note_id": "note-1", "title": "精读笔记标题"}

    def test_empty_fields_rejected(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        monkeypatch.setattr(
            "xhs_cli.commands._common.run_client_action",
            lambda ctx, action: action(client),
        )
        result = runner.invoke(cli, ["read", "note-1", "--fields", " , ,"])
        assert result.exit_code != 0

    def test_read_default_unchanged(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        result = _invoke(monkeypatch, client, ["read", "note-1", "--json"])
        assert _json_payload(result)["data"] == NOTE_DETAIL_RESPONSE

    def test_no_media_both_formats(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        as_json = _invoke(monkeypatch, client, ["read", "note-1", "--no-media", "--json"])
        as_yaml = _invoke(monkeypatch, client, ["read", "note-1", "--no-media", "--yaml"])
        assert _json_payload(as_json)["data"] == _yaml_payload(as_yaml)["data"]


class TestProjectionUnits:
    def test_search_hot_word_candidates_pass_through(self):
        assert compact_search_results({"items": [], "query_revise": {"q": "x"}})["query_revise"] == {"q": "x"}
        assert "hot_words" not in compact_search_results({"items": []})

    def test_strip_note_media_does_not_mutate_input(self):
        payload = {"items": [{"note_card": {"image_list": [_image(0)], "title": "t"}}]}
        strip_note_media(payload)
        assert payload["items"][0]["note_card"]["image_list"]

    def test_pick_note_fields_passes_non_dict_items_through(self):
        data = {"items": ["not-a-dict"]}
        assert pick_note_fields(data, ["title"]) == {"items": ["not-a-dict"]}


class TestPayloadSizeReduction:
    @staticmethod
    def _size(data) -> int:
        return len(json.dumps(success_payload(data), ensure_ascii=False, indent=2).encode("utf-8"))

    def test_compact_shrinks_search_payload(self, capsys):
        full = self._size(SEARCH_RESPONSE)
        compact = self._size(compact_search_results(SEARCH_RESPONSE))
        with capsys.disabled():
            print(f"\nsearch structured payload: full={full}B compact={compact}B")
        assert compact < full // 2

    def test_no_media_shrinks_note_payload(self, capsys):
        full = self._size(NOTE_DETAIL_RESPONSE)
        trimmed = self._size(strip_note_media(NOTE_DETAIL_RESPONSE))
        with capsys.disabled():
            print(f"\nread structured payload: full={full}B no-media={trimmed}B")
        assert trimmed < full // 2
