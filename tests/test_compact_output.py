"""Tests for --compact / --no-media / --fields structured-output trimming."""

import json

import pytest
import yaml
from click.testing import CliRunner

from xhs_cli.cli import cli
from xhs_cli.client_mixins import ReadingEndpointsMixin
from xhs_cli.formatter_normalizers import (
    compact_comments,
    compact_feed,
    compact_hot_queries,
    compact_search_results,
    pick_note_fields,
    strip_note_media,
)
from xhs_cli.formatter_utils import success_payload

runner = CliRunner()

ITEM_WHITELIST = {"note_id", "xsec_token", "title", "author", "liked", "note_type"}
MEDIA_KEYS = {"image_list", "cover", "stream", "live_photo", "video"}

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


def _media_v2() -> dict:
    def bitrate_entry(codec: str, quality: str) -> dict:
        return {
            "master_url": f"https://sns-video.example.com/{codec}/{quality}/master.mp4?{_LONG}",
            "backup_urls": [f"https://sns-video.example.com/{codec}/{quality}/backup.mp4?{_LONG}"],
            "duration": 15000,
            "quality_type": quality,
            "opaque1": f"opaque-{codec}-{quality}-{_LONG}",
        }

    return {
        "h264": [bitrate_entry("h264", q) for q in ("hd", "sd")],
        "h265": [bitrate_entry("h265", q) for q in ("hd", "sd")],
        "av1": [bitrate_entry("av1", q) for q in ("hd", "sd")],
    }


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
        {
            "id": "hot-query-req-1",
            "model_type": "hot_query",
            "hot_query": {
                "title": "大家都在搜",
                "source": 2,
                "queries": [
                    {
                        "name": "热词一",
                        "search_word": "热词一",
                        "cover": f"https://sns-img.example.com/q1.jpg?{_LONG}",
                        "id": "query-1",
                    },
                    {
                        "name": "热词二",
                        "search_word": "热词二",
                        "cover": f"https://sns-img.example.com/q2.jpg?{_LONG}",
                        "id": "query-2",
                    },
                ],
            },
        },
    ],
    "has_more": True,
}

# Production regression fixture: two "大家都在搜" blocks as actually returned
# by the search API (2 groups × 4 terms here). Each term carries a signed
# xhscdn cover URL with ``sign=`` / ``t=`` parameters — the bulk that compact
# output must not carry. hb.1 projected these through the note whitelist and
# produced empty-shell note rows (empty title/author/liked, request-id-shaped
# note_id); hb.2 exposed them raw; hb.7 drops them unless explicitly requested.
HOT_QUERY_REGRESSION_RESPONSE = {
    "items": [
        {
            "id": "note-1",
            "xsec_token": "token-1",
            "model_type": "note",
            "note_card": {
                "type": "normal",
                "display_title": "正常笔记",
                "user": {"user_id": "u-1", "nickname": "作者甲"},
                "interact_info": {"liked_count": "12"},
            },
        },
        {
            "id": "c961794e-8583-4052-8a32-895782037650#1789296424807",
            "xsec_token": "ABPxn9gORegressionToken",
            "model_type": "hot_query",
            "hot_query": {
                "title": "大家都在搜",
                "source": 2,
                "queries": [
                    {
                        "name": "regression 热词",
                        "search_word": "regression 热词",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31regression0"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=6d1f0e2c9b8a7f6e5d4c3b2a1908f7e6&t=1789296424807"
                        ),
                        "id": "query-regression",
                    },
                    {
                        "name": "夏日穿搭",
                        "search_word": "夏日穿搭",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31summer01"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=7e2a1b3c4d5e6f708192a3b4c5d6e7f8&t=1789296424808"
                        ),
                        "id": "query-summer",
                    },
                    {
                        "name": "citywalk 路线",
                        "search_word": "citywalk 路线",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31citywalk2"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=8f3b2c4d5e6a7b8c9d0e1f2a3b4c5d6e&t=1789296424809"
                        ),
                        "id": "query-citywalk",
                    },
                    {
                        "name": "咖啡店探店",
                        "search_word": "咖啡店探店",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31coffee03"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=9a4c3d5e6f7a8b9c0d1e2f3a4b5c6d7e&t=1789296424810"
                        ),
                        "id": "query-coffee",
                    },
                ],
            },
        },
        {
            "id": "7b72f1ae-9194-4163-9b43-9a6831485918#1789296424811",
            "xsec_token": "ABPxn9gOSecondHotQueryToken",
            "model_type": "hot_query",
            "hot_query": {
                "title": "大家都在搜",
                "source": 2,
                "queries": [
                    {
                        "name": "秋日护肤",
                        "search_word": "秋日护肤",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31skincare4"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=1b5d4e6f7a8b9c0d1e2f3a4b5c6d7e8f&t=1789296424812"
                        ),
                        "id": "query-skincare",
                    },
                    {
                        "name": "小个子通勤",
                        "search_word": "小个子通勤",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31commute05"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=2c6e5f7a8b9c0d1e2f3a4b5c6d7e8f9a&t=1789296424813"
                        ),
                        "id": "query-commute",
                    },
                    {
                        "name": "周末露营",
                        "search_word": "周末露营",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31camping06"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=3d7f6a8b9c0d1e2f3a4b5c6d7e8f9a0b&t=1789296424814"
                        ),
                        "id": "query-camping",
                    },
                    {
                        "name": "居家收纳",
                        "search_word": "居家收纳",
                        "cover": (
                            "https://sns-img-qc.xhscdn.com/1040g2sg31storage07"
                            "?imageView2/2/w/1080/format/webp"
                            "&sign=4e8a7b9c0d1e2f3a4b5c6d7e8f9a0b1c&t=1789296424815"
                        ),
                        "id": "query-storage",
                    },
                ],
            },
        },
        {
            "id": "ad-slot-1",
            "model_type": "ads",
            "ads": {"creative": {"banner": f"https://sns-img.example.com/ad.jpg?{_LONG}"}},
        },
    ],
    "has_more": False,
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
                    "capa": {"duration": 15},
                    "media_v2": _media_v2(),
                    "media": {
                        "stream": _stream(),
                        "video": {
                            "fileid": f"video-file-{_LONG}",
                            "duration": 15000,
                            "width": 1080,
                            "height": 1920,
                        },
                        "image": {
                            "first_frame_fileid": f"frame-file-{_LONG}",
                            "thumbnail_fileid": f"thumb-file-{_LONG}",
                            "url_default": f"https://sns-img.example.com/first-frame.jpg?{_LONG}",
                        },
                    },
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
    monkeypatch.setattr(
        "xhs_cli.commands.reading.run_client_action",
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

        assert set(data) == {"items", "has_more"}
        assert data["has_more"] is True
        assert "hot_queries" not in data
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

        assert set(data) == {"items", "has_more"}
        assert {key for item in data["items"] for key in item} == ITEM_WHITELIST

    def test_default_output_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _SearchClient(), ["search", "kw", "--json"])
        payload = _json_payload(result)

        assert payload["ok"] is True
        assert payload["schema_version"] == "1"
        assert payload["data"] == SEARCH_RESPONSE


class _HotQueryRegressionClient:
    def search_notes(self, **kwargs):
        return HOT_QUERY_REGRESSION_RESPONSE


class _HotQueryFeedClient:
    def get_home_feed(self):
        return HOT_QUERY_REGRESSION_RESPONSE

    def get_hot_feed(self, category):
        return HOT_QUERY_REGRESSION_RESPONSE


class TestSearchHotQuery:
    def test_compact_drops_hot_query_blocks_by_default(self, monkeypatch):
        result = _invoke(monkeypatch, _HotQueryRegressionClient(), ["search", "kw", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert set(data) == {"items", "has_more"}
        assert "hot_queries" not in data
        assert len(data["items"]) == 1
        assert "大家都在搜" not in result.output
        assert "xhscdn.com" not in result.output
        assert "sign=" not in result.output
        assert "t=" not in result.output

    def test_with_hot_queries_returns_text_terms_only(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _HotQueryRegressionClient(),
            ["search", "kw", "--compact", "--with-hot-queries", "--json"],
        )
        data = _json_payload(result)["data"]

        assert set(data) == {"items", "has_more", "hot_queries"}
        assert len(data["hot_queries"]) == 2
        for group in data["hot_queries"]:
            assert set(group) <= {"title", "queries"}
            assert group["title"] == "大家都在搜"
            assert len(group["queries"]) == 4
            for term in group["queries"]:
                assert set(term) == {"name", "search_word"}
                assert all(isinstance(value, str) and value for value in term.values())

        assert not _contains_any_key(data["hot_queries"], {"cover", "id", "source"})
        for leaked in ("xhscdn.com", "sign=", "t=", "imageView2"):
            assert leaked not in result.output

    def test_with_hot_queries_yaml(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _HotQueryRegressionClient(),
            ["search", "kw", "--compact", "--with-hot-queries", "--yaml"],
        )
        data = _yaml_payload(result)["data"]

        assert [term["name"] for group in data["hot_queries"] for term in group["queries"]] == [
            "regression 热词",
            "夏日穿搭",
            "citywalk 路线",
            "咖啡店探店",
            "秋日护肤",
            "小个子通勤",
            "周末露营",
            "居家收纳",
        ]

    def test_default_full_output_keeps_raw_hot_query_block(self, monkeypatch):
        result = _invoke(monkeypatch, _HotQueryRegressionClient(), ["search", "kw", "--json"])
        data = _json_payload(result)["data"]

        assert data == HOT_QUERY_REGRESSION_RESPONSE
        assert data["items"][1]["hot_query"]["queries"][0]["cover"].startswith("https://sns-img-qc.xhscdn.com/")
        assert "sign=" in data["items"][1]["hot_query"]["queries"][0]["cover"]
        assert "t=" in data["items"][1]["hot_query"]["queries"][0]["cover"]

    def test_with_hot_queries_without_compact_is_a_noop(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _HotQueryRegressionClient(),
            ["search", "kw", "--with-hot-queries", "--json"],
        )
        assert _json_payload(result)["data"] == HOT_QUERY_REGRESSION_RESPONSE

    def test_help_documents_switch_and_default(self):
        result = runner.invoke(cli, ["search", "--help"])
        assert result.exit_code == 0, result.output
        help_text = " ".join(result.output.split()).lower()
        assert "--with-hot-queries" in help_text
        assert "default: off" in help_text

    def test_no_empty_shell_note_rows(self, monkeypatch):
        result = _invoke(monkeypatch, _HotQueryRegressionClient(), ["search", "kw", "--compact", "--json"])
        data = _json_payload(result)["data"]

        # Only the real note survives: the hot_query entries and the unknown
        # "ads" entry must not become empty-shell note rows.
        assert len(data["items"]) == 1
        (item,) = data["items"]
        assert item["note_id"] == "note-1"
        assert item["title"]
        assert all("#" not in row["note_id"] for row in data["items"])
        assert all(row["title"] or row["author"] or row["liked"] for row in data["items"])

    def test_no_hot_queries_key_when_absent(self):
        compact = compact_search_results({"items": [], "has_more": True})
        assert compact == {"items": [], "has_more": True}


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

    def test_feed_compact_drops_hot_query_blocks_by_default(self, monkeypatch):
        result = _invoke(monkeypatch, _HotQueryFeedClient(), ["feed", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert set(data) == {"items", "has_more"}
        assert "hot_queries" not in data
        assert "xhscdn.com" not in result.output

    def test_feed_with_hot_queries_text_only(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _HotQueryFeedClient(),
            ["feed", "--compact", "--with-hot-queries", "--json"],
        )
        data = _json_payload(result)["data"]

        assert len(data["hot_queries"]) == 2
        assert not _contains_any_key(data["hot_queries"], {"cover", "id", "source"})
        assert "xhscdn.com" not in result.output
        assert "sign=" not in result.output
        assert "t=" not in result.output

    def test_hot_with_hot_queries_text_only(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _HotQueryFeedClient(),
            ["hot", "-c", "food", "--compact", "--with-hot-queries", "--yaml"],
        )
        data = _yaml_payload(result)["data"]

        assert data["hot_queries"][0]["queries"][0] == {
            "name": "regression 热词",
            "search_word": "regression 热词",
        }
        assert not _contains_any_key(data["hot_queries"], {"cover", "id", "source"})

    def test_feed_default_keeps_raw_hot_query_block(self, monkeypatch):
        result = _invoke(monkeypatch, _HotQueryFeedClient(), ["feed", "--json"])
        data = _json_payload(result)["data"]

        assert data == HOT_QUERY_REGRESSION_RESPONSE
        assert "sign=" in data["items"][1]["hot_query"]["queries"][0]["cover"]
        assert "t=" in data["items"][1]["hot_query"]["queries"][0]["cover"]

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
        assert "video" not in note
        assert note["user"] == NOTE_DETAIL_RESPONSE["items"][0]["note_card"]["user"]
        assert note["interact_info"] == NOTE_DETAIL_RESPONSE["items"][0]["note_card"]["interact_info"]
        assert data["items"][0]["xsec_token"] == "token-1"

    def test_no_media_strips_video_subtree_and_shrinks_payload(self, monkeypatch):
        client = _ReadClient(NOTE_DETAIL_RESPONSE)
        result = _invoke(monkeypatch, client, ["read", "note-1", "--no-media", "--json"])
        data = _json_payload(result)["data"]

        assert not _contains_any_key(data, MEDIA_KEYS | {"media_v2"})
        note = data["items"][0]["note_card"]
        for key in ("title", "desc", "user", "interact_info", "tag_list", "time"):
            assert key in note
        assert data["items"][0]["id"] == "note-1"

        full = len(json.dumps(NOTE_DETAIL_RESPONSE, ensure_ascii=False))
        trimmed = len(json.dumps(data, ensure_ascii=False))
        assert trimmed < full // 2

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
    def test_hot_query_items_are_not_projected_as_notes(self):
        compact = compact_search_results(HOT_QUERY_REGRESSION_RESPONSE)
        assert [item["note_id"] for item in compact["items"]] == ["note-1"]
        assert "hot_queries" not in compact

    def test_hot_queries_are_text_only_when_requested(self):
        compact = compact_search_results(HOT_QUERY_REGRESSION_RESPONSE, with_hot_queries=True)
        assert len(compact["hot_queries"]) == 2
        assert not _contains_any_key(compact["hot_queries"], {"cover", "id", "source"})
        assert compact["hot_queries"][0] == {
            "title": "大家都在搜",
            "queries": [
                {"name": "regression 热词", "search_word": "regression 热词"},
                {"name": "夏日穿搭", "search_word": "夏日穿搭"},
                {"name": "citywalk 路线", "search_word": "citywalk 路线"},
                {"name": "咖啡店探店", "search_word": "咖啡店探店"},
            ],
        }

    def test_compact_feed_omits_hot_queries_by_default(self):
        compact = compact_feed(HOT_QUERY_REGRESSION_RESPONSE)
        assert "hot_queries" not in compact

    def test_compact_hot_queries_skips_groups_without_text_terms(self):
        raw = [
            {"title": "无文本", "queries": [{"cover": "https://sns-img.example.com/x.jpg", "id": "q"}]},
            {"title": "有文本", "queries": [{"name": "热词", "cover": "https://sns-img.example.com/x.jpg"}]},
        ]
        assert compact_hot_queries(raw) == [
            {"title": "有文本", "queries": [{"name": "热词"}]},
        ]

    def test_unknown_model_types_are_dropped(self):
        data = {"items": [{"model_type": "ads", "id": "ad-1"}, {"model_type": "rec_query", "id": "rq-1"}]}
        assert compact_search_results(data)["items"] == []

    def test_items_without_model_type_need_a_note_card(self):
        note = {"id": "flat-1", "note_card": {"display_title": "t"}}
        shell = {"id": "flat-2"}
        compact = compact_search_results({"items": [note, shell]})
        assert [item["note_id"] for item in compact["items"]] == ["flat-1"]

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

    def test_hot_query_block_can_be_dropped_or_kept_as_text(self, capsys):
        full = self._size(HOT_QUERY_REGRESSION_RESPONSE)
        default = self._size(compact_search_results(HOT_QUERY_REGRESSION_RESPONSE))
        with_terms = self._size(
            compact_search_results(HOT_QUERY_REGRESSION_RESPONSE, with_hot_queries=True)
        )
        with capsys.disabled():
            print(
                "\nhot-query fixture: "
                f"full={full}B compact={default}B compact+terms={with_terms}B"
            )
        assert default < with_terms < full
        assert default <= full // 2

    def test_no_media_shrinks_note_payload(self, capsys):
        full = self._size(NOTE_DETAIL_RESPONSE)
        trimmed = self._size(strip_note_media(NOTE_DETAIL_RESPONSE))
        with capsys.disabled():
            print(f"\nread structured payload: full={full}B no-media={trimmed}B")
        assert trimmed < full // 2


# ─── comments / sub-comments (--compact / --limit) ──────────────────────────

COMMENT_WHITELIST = {
    "id",
    "note_id",
    "content",
    "like_count",
    "liked",
    "ip_location",
    "create_time",
    "is_author",
    "is_pinned",
    "sub_comment_count",
    "sub_comment_cursor",
    "sub_comment_has_more",
    "user_info",
    "at_users",
}
COMMENT_DROPPED_KEYS = {"show_tags", "sub_comments", "pictures", "status", "invalid", "avatar", "xsec_token"}


def _comment_user(index: int) -> dict:
    return {
        "user_id": f"user-{index}",
        "nickname": f"评论用户{index}",
        "avatar": f"https://sns-avatar.example.com/{index}.jpg?{_LONG}",
        "xsec_token": f"xsec-{index}-{_LONG}",
    }


def _embedded_sub_comment(index: int) -> dict:
    return {
        "id": f"sub-{index}",
        "note_id": "note-1",
        "content": f"楼中楼回复{index}，嵌入预览层。",
        "user_info": _comment_user(100 + index),
        "like_count": "12",
        "liked": False,
        "ip_location": "上海",
        "create_time": 1757000000000,
        "status": 2,
        "show_tags": [],
        "at_users": [],
        "sub_comment_count": "0",
        "sub_comment_cursor": "",
        "sub_comment_has_more": False,
    }


COMMENTS_RESPONSE = {
    "cursor": "cursor-page-2",
    "has_more": True,
    "comments": [
        {
            "id": "cmt-1",
            "note_id": "note-1",
            "content": "第一条评论内容，长度大约二三十个汉字。",
            "user_info": _comment_user(1),
            "like_count": "234",
            "liked": False,
            "ip_location": "广东",
            "create_time": 1757000000000,
            "status": 2,
            "invalid": False,
            "show_tags": ["is_author"],
            "at_users": [{"user_id": "user-9", "nickname": "被圈的人", "avatar": f"https://sns-avatar.example.com/9.jpg?{_LONG}"}],
            "sub_comment_count": "3",
            "sub_comment_cursor": "sub-cursor-1",
            "sub_comment_has_more": True,
            "sub_comments": [_embedded_sub_comment(1), _embedded_sub_comment(2)],
            "pictures": [_image(9)],
        },
        {
            "id": "cmt-2",
            "note_id": "note-1",
            "content": "回复别人的评论。",
            "user_info": _comment_user(2),
            "like_count": "5",
            "liked": True,
            "ip_location": "上海",
            "create_time": 1757000001000,
            "show_tags": None,
            "sub_comment_count": "0",
            "sub_comment_cursor": "",
            "sub_comment_has_more": False,
            "target_comment": {
                "id": "cmt-1",
                "user_info": _comment_user(1),
                "content": "第一条评论内容，长度大约二三十个汉字。",
            },
        },
    ],
}

SUB_COMMENTS_RESPONSE = {
    "cursor": "sub-page-2",
    "has_more": True,
    "comments": [
        {
            "id": "reply-1",
            "note_id": "note-1",
            "content": "楼中楼回复一。",
            "user_info": _comment_user(3),
            "like_count": "8",
            "liked": False,
            "ip_location": "北京",
            "create_time": 1757000002000,
            "show_tags": [],
            "sub_comment_count": "0",
            "sub_comment_cursor": "",
            "sub_comment_has_more": False,
            "target_comment": {
                "id": "cmt-1",
                "user_info": _comment_user(1),
                "content": "第一条评论内容，长度大约二三十个汉字。",
            },
        },
        {
            "id": "reply-2",
            "note_id": "note-1",
            "content": "楼中楼回复二。",
            "user_info": _comment_user(4),
            "like_count": "2",
            "liked": False,
            "ip_location": "浙江",
            "create_time": 1757000003000,
            "sub_comment_count": "0",
            "sub_comment_cursor": "",
            "sub_comment_has_more": False,
        },
    ],
}


class _CommentsClient:
    def get_comments(self, note_id, cursor="", **kwargs):
        return COMMENTS_RESPONSE


class _AllCommentsClient(ReadingEndpointsMixin):
    """Fake client that runs the real get_all_comments over scripted pages."""

    def __init__(self, pages):
        self._pages = pages
        self.requested_cursors = []

    def get_comments(self, note_id, cursor="", **kwargs):
        self.requested_cursors.append(cursor)
        return self._pages[len(self.requested_cursors) - 1]


def _comments_page(index: int, count: int, *, has_more: bool) -> dict:
    return {
        "cursor": f"cursor-page-{index + 1}",
        "has_more": has_more,
        "comments": [
            {
                "id": f"cmt-p{index}-{i}",
                "note_id": "note-1",
                "content": f"第{index}页第{i}条评论。",
                "user_info": _comment_user(index * 100 + i),
                "like_count": "1",
                "liked": False,
                "ip_location": "广东",
                "create_time": 1757000000000,
                "show_tags": [],
                "sub_comment_count": "0",
                "sub_comment_cursor": "",
                "sub_comment_has_more": False,
            }
            for i in range(count)
        ],
    }


class _SubCommentsClient:
    def get_sub_comments(self, note_id, root_comment_id, num=30, cursor=""):
        return SUB_COMMENTS_RESPONSE


class TestCommentsCompact:
    def test_compact_json_whitelist(self, monkeypatch):
        result = _invoke(monkeypatch, _CommentsClient(), ["comments", "note-1", "--compact", "--json"])
        payload = _json_payload(result)

        assert payload["ok"] is True
        assert payload["schema_version"] == "1"
        data = payload["data"]
        assert data["cursor"] == "cursor-page-2"
        assert data["has_more"] is True
        assert len(data["comments"]) == 2

        first, second = data["comments"]
        assert set(first) == COMMENT_WHITELIST
        assert first["id"] == "cmt-1"
        assert first["is_author"] is True
        assert first["is_pinned"] is False
        assert first["sub_comment_cursor"] == "sub-cursor-1"
        assert first["sub_comment_has_more"] is True
        assert first["user_info"] == {"nickname": "评论用户1", "user_id": "user-1"}
        assert first["at_users"] == ["被圈的人"]

        # show_tags null / at_users absent / target_comment present.
        assert set(second) == (COMMENT_WHITELIST - {"at_users"}) | {"target_comment"}
        assert second["is_author"] is False
        assert second["is_pinned"] is False
        assert second["target_comment"] == {"id": "cmt-1", "nickname": "评论用户1"}

        assert not _contains_any_key(data, COMMENT_DROPPED_KEYS)

    def test_compact_yaml_envelope(self, monkeypatch):
        result = _invoke(monkeypatch, _CommentsClient(), ["comments", "note-1", "--compact", "--yaml"])
        data = _yaml_payload(result)["data"]

        assert set(data) == {"comments", "cursor", "has_more"}
        assert {key for comment in data["comments"] for key in comment["user_info"]} == {"nickname", "user_id"}

    def test_compact_all_keeps_aggregate_fields(self, monkeypatch):
        pages = [_comments_page(0, 10, has_more=False)]
        result = _invoke(monkeypatch, _AllCommentsClient(pages), ["comments", "note-1", "--all", "--compact", "--json"])
        data = _json_payload(result)["data"]

        assert set(data) == {"comments", "cursor", "has_more", "total_fetched", "pages_fetched"}
        assert data["total_fetched"] == 10
        assert data["pages_fetched"] == 1

    def test_default_output_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _CommentsClient(), ["comments", "note-1", "--json"])
        assert _json_payload(result)["data"] == COMMENTS_RESPONSE

    def test_limit_truncates_page_but_keeps_pagination(self, monkeypatch):
        result = _invoke(monkeypatch, _CommentsClient(), ["comments", "note-1", "--limit", "1", "--json"])
        data = _json_payload(result)["data"]

        assert len(data["comments"]) == 1
        assert data["comments"][0]["id"] == "cmt-1"
        assert data["cursor"] == "cursor-page-2"
        assert data["has_more"] is True

    def test_limit_without_compact_keeps_raw_comment_fields(self, monkeypatch):
        result = _invoke(monkeypatch, _CommentsClient(), ["comments", "note-1", "--limit", "1", "--json"])
        comment = _json_payload(result)["data"]["comments"][0]

        assert comment == COMMENTS_RESPONSE["comments"][0]

    def test_limit_rejects_non_positive(self, monkeypatch):
        client = _CommentsClient()
        monkeypatch.setattr(
            "xhs_cli.commands._common.run_client_action",
            lambda ctx, action: action(client),
        )
        for bad in ("0", "-3"):
            result = runner.invoke(cli, ["comments", "note-1", "--limit", bad])
            assert result.exit_code != 0

    def test_all_with_limit_stops_paginating_early(self, monkeypatch):
        pages = [
            _comments_page(0, 10, has_more=True),
            _comments_page(1, 10, has_more=True),
            _comments_page(2, 10, has_more=False),
        ]
        client = _AllCommentsClient(pages)
        result = _invoke(monkeypatch, client, ["comments", "note-1", "--all", "--limit", "15", "--json"])
        data = _json_payload(result)["data"]

        assert data["total_fetched"] == 15
        assert data["pages_fetched"] == 2
        assert len(data["comments"]) == 15
        # Early stop: the third page was never requested, and the resume
        # cursor is reported instead of the exhausted aggregate sentinel.
        assert client.requested_cursors == ["", "cursor-page-1"]
        assert data["has_more"] is True
        assert data["cursor"] == "cursor-page-2"

    def test_all_without_limit_fetches_everything(self, monkeypatch):
        pages = [
            _comments_page(0, 10, has_more=True),
            _comments_page(1, 10, has_more=False),
        ]
        client = _AllCommentsClient(pages)
        result = _invoke(monkeypatch, client, ["comments", "note-1", "--all", "--json"])
        data = _json_payload(result)["data"]

        assert data["total_fetched"] == 20
        assert data["pages_fetched"] == 2
        assert data["has_more"] is False
        assert data["cursor"] == ""

    def test_compact_does_not_affect_rich_render(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "rich")
        result = _invoke(monkeypatch, _CommentsClient(), ["comments", "note-1", "--compact"])

        assert "评论用户1" in result.output
        assert "第一条评论内容" in result.output


class TestSubCommentsCompact:
    def test_compact_json_whitelist(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _SubCommentsClient(),
            ["sub-comments", "note-1", "cmt-1", "--compact", "--json"],
        )
        data = _json_payload(result)["data"]

        assert data["cursor"] == "sub-page-2"
        assert data["has_more"] is True
        first, second = data["comments"]
        assert first["target_comment"] == {"id": "cmt-1", "nickname": "评论用户1"}
        assert "target_comment" not in second
        assert not _contains_any_key(data, COMMENT_DROPPED_KEYS)

    def test_default_output_unchanged(self, monkeypatch):
        result = _invoke(monkeypatch, _SubCommentsClient(), ["sub-comments", "note-1", "cmt-1", "--yaml"])
        assert _yaml_payload(result)["data"] == SUB_COMMENTS_RESPONSE

    def test_limit_truncates_page_but_keeps_pagination(self, monkeypatch):
        result = _invoke(
            monkeypatch,
            _SubCommentsClient(),
            ["sub-comments", "note-1", "cmt-1", "--limit", "1", "--json"],
        )
        data = _json_payload(result)["data"]

        assert [comment["id"] for comment in data["comments"]] == ["reply-1"]
        assert data["cursor"] == "sub-page-2"
        assert data["has_more"] is True

    def test_limit_rejects_non_positive(self, monkeypatch):
        client = _SubCommentsClient()
        monkeypatch.setattr(
            "xhs_cli.commands._common.run_client_action",
            lambda ctx, action: action(client),
        )
        result = runner.invoke(cli, ["sub-comments", "note-1", "cmt-1", "--limit", "0"])
        assert result.exit_code != 0

    def test_compact_does_not_affect_rich_render(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "rich")
        result = _invoke(monkeypatch, _SubCommentsClient(), ["sub-comments", "note-1", "cmt-1", "--compact"])

        assert "评论用户3" in result.output
        assert "楼中楼回复一" in result.output


class TestCommentsProjectionUnits:
    def test_show_tags_missing_or_null_tolerated(self):
        projected = compact_comments({"comments": [{"id": "c1"}, {"id": "c2", "show_tags": None}]})
        first, second = projected["comments"]
        assert first["is_author"] is False
        assert first["is_pinned"] is False
        assert second["is_author"] is False

    def test_unknown_show_tags_ignored(self):
        projected = compact_comments({"comments": [{"id": "c1", "show_tags": ["is_author", "some_unknown_tag"]}]})
        (comment,) = projected["comments"]
        assert comment["is_author"] is True
        assert "show_tags" not in comment
        assert "some_unknown_tag" not in comment.values()

    def test_aggregate_fields_passed_through(self):
        compact = compact_comments(
            {"comments": [], "cursor": "", "has_more": False, "total_fetched": 3, "pages_fetched": 1}
        )
        assert compact == {"comments": [], "cursor": "", "has_more": False, "total_fetched": 3, "pages_fetched": 1}

    def test_compact_shrinks_comments_payload(self, capsys):
        full = TestPayloadSizeReduction._size(COMMENTS_RESPONSE)
        compact = TestPayloadSizeReduction._size(compact_comments(COMMENTS_RESPONSE))
        with capsys.disabled():
            print(f"\ncomments structured payload: full={full}B compact={compact}B")
        assert compact < full // 2
