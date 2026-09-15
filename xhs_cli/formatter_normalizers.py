"""Normalize reverse-engineered API payloads into stable renderer-friendly shapes."""

from __future__ import annotations

from typing import Any


def _coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def normalize_user_info(data: dict[str, Any]) -> dict[str, Any]:
    basic = data.get("basic_info", data)
    interactions = data.get("interactions", [])

    stats = {}
    for item in interactions:
        stats[item.get("type", "")] = item.get("count", "0")

    return {
        "nickname": basic.get("nickname", basic.get("nick_name", "Unknown")),
        "red_id": basic.get("red_id", ""),
        "desc": basic.get("desc", ""),
        "ip_location": basic.get("ip_location", ""),
        "user_id": basic.get("user_id", data.get("user_id", "")),
        "gender": basic.get("gender"),
        "stats": stats,
    }


def normalize_note_detail(data: dict[str, Any]) -> dict[str, Any] | None:
    items = data.get("items", [])
    if not items:
        return None

    note = items[0].get("note_card", {})
    user = note.get("user", {})
    interact = note.get("interact_info", {})
    tags = note.get("tag_list", [])

    return {
        "title": note.get("title", "Untitled"),
        "desc": note.get("desc", ""),
        "author": user.get("nickname", "Unknown"),
        "liked_count": interact.get("liked_count", "0"),
        "collected_count": interact.get("collected_count", "0"),
        "comment_count": interact.get("comment_count", "0"),
        "share_count": interact.get("share_count", "0"),
        "tags": [tag.get("name", "") for tag in tags if tag.get("name")],
        "image_count": len(note.get("image_list", [])),
    }


def normalize_note_summary(item: dict[str, Any]) -> dict[str, Any] | None:
    note_card = item.get("note_card", item)
    if not isinstance(note_card, dict):
        return None
    user = note_card.get("user", {})
    interact = note_card.get("interact_info", {})
    return {
        "title": str(note_card.get("title", note_card.get("display_title", "")))[:40],
        "author": user.get("nickname", ""),
        "liked": str(interact.get("liked_count", "")),
        "note_type": "video" if note_card.get("type") == "video" else "image",
        "note_id": item.get("id", note_card.get("note_id", "")),
        "xsec_token": item.get("xsec_token", note_card.get("xsec_token", "")),
    }


def normalize_search_results(data: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in (normalize_note_summary(item) for item in data.get("items", [])) if item]
    return {
        "items": items,
        "has_more": bool(data.get("has_more", False)),
    }


def normalize_comments(data: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for comment in data.get("comments", []):
        user = comment.get("user_info", {})
        normalized.append({
            "nickname": user.get("nickname", "Unknown"),
            "content": comment.get("content", ""),
            "like_count": comment.get("like_count", "0"),
            "sub_comment_count": _coerce_int(comment.get("sub_comment_count", 0)),
        })
    return normalized


def normalize_feed(data: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for item in data.get("items", [])[:20]:
        note_card = item.get("note_card", {})
        user = note_card.get("user", {})
        interact = note_card.get("interact_info", {})
        normalized.append({
            "title": note_card.get("title", note_card.get("display_title", ""))[:40],
            "author": user.get("nickname", ""),
            "liked": str(interact.get("liked_count", "")),
            "note_id": item.get("id", ""),
            "xsec_token": item.get("xsec_token", note_card.get("xsec_token", "")),
        })
    return normalized


def normalize_user_posts(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for note in notes:
        interact = note.get("interact_info", {})
        normalized.append({
            "title": note.get("display_title", "")[:40],
            "liked": str(interact.get("liked_count", note.get("liked_count", ""))),
            "note_type": "video" if note.get("type") == "video" else "image",
            "note_id": note.get("note_id", ""),
        })
    return normalized


def normalize_topics(data: Any) -> list[dict[str, Any]]:
    topics = data if isinstance(data, list) else data.get("topic_info_dtos", [])
    return [
        {
            "name": topic.get("name", ""),
            "view_num": topic.get("view_num", 0),
            "topic_id": topic.get("id", ""),
        }
        for topic in topics
    ]


def normalize_users(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        users = data
    elif isinstance(data, dict):
        users = data.get("user_info_dtos") or data.get("users") or data.get("items") or []
    else:
        users = []

    normalized = []
    for user in users:
        base = user.get("user_base_dto", user)
        normalized.append({
            "nickname": base.get("user_nickname", base.get("nickname", base.get("nick_name", ""))),
            "red_id": base.get("red_id", ""),
            "fans": user.get("fans_total", base.get("fans", base.get("fansCount", 0))),
            "user_id": base.get("user_id", base.get("id", "")),
        })
    return normalized


def normalize_creator_notes(data: Any) -> list[dict[str, Any]]:
    notes = data if isinstance(data, list) else data.get("notes", data.get("note_list", []))
    normalized = []
    for note in notes:
        interact = note.get("interact_info", {})
        normalized.append({
            "title": note.get("title", note.get("display_title", ""))[:40],
            "liked": str(note.get("liked_count", interact.get("liked_count", ""))),
            "comment_count": str(note.get("comment_count", interact.get("comment_count", ""))),
            "status": note.get("status"),
            "note_id": note.get("note_id", note.get("id", "")),
        })
    return normalized


def normalize_notifications(data: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for message in data.get("message_list", []):
        user = message.get("user_info", {}) or {}
        item = message.get("item_info", {}) or {}
        normalized.append({
            "nickname": user.get("nickname", ""),
            "title": message.get("title", ""),
            "note_content": item.get("content", ""),
            "time": message.get("time", 0),
        })
    return normalized


# ─── Compact projections (--compact / --no-media / --fields) ────────────────
#
# These power trimmed structured output: they reuse the renderer-facing
# normalize_* shapes where they already match the whitelist, and add the
# pagination tokens (has_more / cursor / xsec_token) downstream paging needs.

# Media fields stripped by read --no-media (also implied by --fields).
# "video" drops the whole video-note subtree (media_v2 bitrate ladders with
# opaque1 blobs, media.video fileid, media.image first-frame thumbnails,
# consumer, capa) — live samples showed it alone wastes tens of KB per note.
# "media_v2" is depth defense in case a future API promotes it to the
# note_card top level.
_NOTE_MEDIA_KEYS = frozenset({"image_list", "cover", "stream", "live_photo", "video", "media_v2"})


def _split_list_items(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split raw list items by model_type before projecting.

    - ``model_type == "note"`` (or a missing model_type on an item that carries
      a ``note_card``) goes through the note whitelist projection.
    - ``model_type == "hot_query"`` is the "大家都在搜" hot-words block; its
      inner ``hot_query`` object (``{title, source, queries: [...]}``) is
      collected separately instead of being projected as a note.
    - Any other model_type is dropped entirely. Unknown entries are not notes,
      and forcing them through the note whitelist produced empty-shell rows
      (empty title/author/liked, a ``<uuid>#<timestamp>`` request-id in
      ``note_id``) that polluted research output; passing them through raw
      risks leaking arbitrarily large blocks, so they are discarded.
    """
    notes: list[dict[str, Any]] = []
    hot_queries: list[dict[str, Any]] = []
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        model_type = item.get("model_type")
        if model_type == "hot_query":
            hot_query = item.get("hot_query")
            if isinstance(hot_query, dict):
                hot_queries.append(hot_query)
            continue
        if model_type is None and not isinstance(item.get("note_card"), dict):
            continue
        if model_type not in (None, "note"):
            continue
        summary = normalize_note_summary(item)
        if summary:
            notes.append(summary)
    return notes, hot_queries


def compact_search_results(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist projection of search results for --compact structured output.

    Note items are projected to the whitelist; ``hot_query`` items surface as a
    top-level ``hot_queries`` list (only present when the search returned any).
    """
    notes, hot_queries = _split_list_items(data)
    compact: dict[str, Any] = {
        "items": notes,
        "has_more": bool(data.get("has_more", False)),
    }
    if hot_queries:
        compact["hot_queries"] = hot_queries
    return compact


def compact_feed(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist projection of feed/hot results for --compact structured output."""
    notes, hot_queries = _split_list_items(data)
    compact: dict[str, Any] = {
        "items": notes,
        "has_more": bool(data.get("has_more", False)),
    }
    if hot_queries:
        compact["hot_queries"] = hot_queries
    return compact


def compact_note_item(note: dict[str, Any]) -> dict[str, Any]:
    """Whitelist projection of one flat paged note (user-posts/favorites/likes/my-notes)."""
    interact = note.get("interact_info", {})
    user = note.get("user", {})
    return {
        "note_id": note.get("note_id", note.get("id", "")),
        "xsec_token": note.get("xsec_token", ""),
        "title": str(note.get("display_title", note.get("title", "")))[:40],
        "author": user.get("nickname", ""),
        "liked": str(interact.get("liked_count", note.get("liked_count", ""))),
        "note_type": "video" if note.get("type") == "video" else "image",
    }


def compact_paged_notes(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist projection of paged note lists for --compact structured output."""
    notes = data.get("notes", data.get("note_list", []))
    return {
        "items": [compact_note_item(note) for note in notes],
        "has_more": bool(data.get("has_more", False)),
        "cursor": data.get("cursor", ""),
    }


def _strip_keys(value: Any, keys: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {key: _strip_keys(item, keys) for key, item in value.items() if key not in keys}
    if isinstance(value, list):
        return [_strip_keys(item, keys) for item in value]
    return value


def strip_note_media(data: Any) -> Any:
    """Return a copy of note detail data without media fields (read --no-media)."""
    return _strip_keys(data, _NOTE_MEDIA_KEYS)


# Confirmed show_tags values for comments. Only "is_author" has been verified
# against real API samples; the pinned-comment tag name is still unconfirmed,
# so the pinned set stays empty — unknown tags are ignored (never passed
# through) and is_pinned stays False until the tag name is verified.
_COMMENT_AUTHOR_TAG = "is_author"
_COMMENT_PINNED_TAGS: frozenset[str] = frozenset()

_COMMENT_PASSTHROUGH_KEYS = (
    "id",
    "note_id",
    "content",
    "like_count",
    "liked",
    "ip_location",
    "create_time",
)


def _compact_comment(comment: Any) -> Any:
    """Whitelist projection of one comment (shared by comments/sub-comments)."""
    if not isinstance(comment, dict):
        return comment
    show_tags = comment.get("show_tags")
    if not isinstance(show_tags, list):
        show_tags = []

    projected: dict[str, Any] = {key: comment[key] for key in _COMMENT_PASSTHROUGH_KEYS if key in comment}
    projected["is_author"] = _COMMENT_AUTHOR_TAG in show_tags
    projected["is_pinned"] = any(tag in _COMMENT_PINNED_TAGS for tag in show_tags)
    for key in ("sub_comment_count", "sub_comment_cursor", "sub_comment_has_more"):
        if key in comment:
            projected[key] = comment[key]

    user = comment.get("user_info")
    if isinstance(user, dict):
        projected["user_info"] = {key: user[key] for key in ("nickname", "user_id") if key in user}

    if "at_users" in comment:
        at_users = comment.get("at_users")
        projected["at_users"] = (
            [u.get("nickname", "") for u in at_users if isinstance(u, dict)]
            if isinstance(at_users, list)
            else []
        )

    target = comment.get("target_comment")
    if isinstance(target, dict):
        target_user = target.get("user_info")
        projected["target_comment"] = {
            "id": target.get("id", ""),
            "nickname": target_user.get("nickname", "") if isinstance(target_user, dict) else "",
        }

    return projected


def compact_comments(data: Any) -> Any:
    """Whitelist projection of comments/sub-comments responses for --compact.

    Keeps pagination tokens (cursor / has_more, plus the --all aggregate's
    total_fetched / pages_fetched) and projects each comment to the whitelist;
    drops embedded sub_comments, pictures, avatar/xsec_token, show_tags,
    status and invalid.
    """
    if not isinstance(data, dict):
        return data
    comments = data.get("comments", [])
    projected: dict[str, Any] = {
        "comments": [_compact_comment(comment) for comment in comments] if isinstance(comments, list) else [],
        "cursor": data.get("cursor", ""),
        "has_more": bool(data.get("has_more", False)),
    }
    for key in ("total_fetched", "pages_fetched"):
        if key in data:
            projected[key] = data[key]
    return projected


def pick_note_fields(data: Any, fields: list[str]) -> Any:
    """Keep only whitelisted note fields after stripping media (read --fields).

    Handles both note detail shapes: the feed-API envelope
    ({"items": [{"note_card": ...}]}) and the flat HTML-parsed note.
    """
    stripped = strip_note_media(data)
    if isinstance(stripped, dict) and isinstance(stripped.get("items"), list):
        items = []
        for item in stripped["items"]:
            note = item.get("note_card") if isinstance(item, dict) else None
            if not isinstance(note, dict):
                items.append(item)
                continue
            source = dict(note)
            source.setdefault("note_id", item.get("id", item.get("note_id", "")))
            items.append({"note_card": {field: source[field] for field in fields if field in source}})
        return {"items": items}
    if isinstance(stripped, dict):
        return {field: stripped[field] for field in fields if field in stripped}
    return stripped
