"""Resource-level risk marks shared between the gateway and the CLI.

State file: ``~/.xiaohongshu-cli/risk_marks.json`` (0600 when the CLI writes
it). This is the CLI read side of the resource blacklist (D2); the gateway
(cli-gateway) is the primary writer.

Schema (version 1)::

    {
      "version": 1,
      "marks": [
        {
          "kind": "note" | "keyword" | "comment",
          "value": "<note_id | keyword | comment_id>",
          "note_id": "<owning note id, optional>",
          "marked_at": <epoch_ms>,
          "ttl_seconds": <int>,
          "reason": "<error code or free text>"
        }
      ]
    }

Behavior:

- Read-only command entries (search / read / comments / sub-comments) check
  this file first; an active (unexpired) mark fast-fails the command with the
  existing ``verification_required`` error envelope — zero upstream requests.
- When the CLI itself triggers a captcha, it appends a mark for the resource
  tracked via :func:`set_current_resource` (self-healing loop).
- A missing, unreadable, or corrupt file silently degrades to "no marks" —
  state-file problems must never break normal requests.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .constants import RISK_MARKS_FILE
from .cookies import get_config_dir
from .exceptions import NeedVerifyError

logger = logging.getLogger(__name__)

MARKS_VERSION = 1

KIND_NOTE = "note"
KIND_KEYWORD = "keyword"
KIND_COMMENT = "comment"
_VALID_KINDS = {KIND_NOTE, KIND_KEYWORD, KIND_COMMENT}

# TTL for marks the CLI writes for itself after triggering a captcha.
# Aligned with the gateway's 4h cooldown tier.
DEFAULT_TTL_SECONDS = 4 * 3600

MAX_MARKS = 512

# Resource of the in-flight command, used to self-mark on captcha.
_CURRENT_RESOURCE: dict[str, str] = {}


def get_risk_marks_path() -> Path:
    """Get risk marks file path."""
    return get_config_dir() / RISK_MARKS_FILE


def _normalize_mark(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    value = raw.get("value")
    if kind not in _VALID_KINDS or not isinstance(value, str) or not value:
        return None
    try:
        marked_at = float(raw.get("marked_at", 0))
        ttl_seconds = float(raw.get("ttl_seconds", 0))
    except (TypeError, ValueError):
        return None
    if marked_at <= 0 or ttl_seconds <= 0:
        return None
    mark = {
        "kind": kind,
        "value": value,
        "marked_at": marked_at,
        "ttl_seconds": ttl_seconds,
        "reason": str(raw.get("reason", "")),
    }
    note_id = raw.get("note_id")
    if isinstance(note_id, str) and note_id:
        mark["note_id"] = note_id
    return mark


def load_marks(path: Path | None = None) -> list[dict[str, Any]]:
    """Load marks from disk; silently degrade to an empty list on any problem."""
    path = path or get_risk_marks_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to load risk marks %s: %s", path, exc)
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get("marks"), list):
        return []
    return [m for m in (_normalize_mark(item) for item in raw["marks"]) if m is not None]


def save_marks(marks: list[dict[str, Any]], path: Path | None = None) -> None:
    """Persist marks atomically with 0600 permissions; never raises."""
    path = path or get_risk_marks_path()
    try:
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps({"version": MARKS_VERSION, "marks": marks}, indent=2))
        tmp_path.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        logger.debug("Failed to save risk marks %s: %s", path, exc)


def _is_active(mark: dict[str, Any], now_ms: float) -> bool:
    return mark["marked_at"] + mark["ttl_seconds"] * 1000 > now_ms


def find_active_mark(
    pairs: Iterable[tuple[str, str]],
    *,
    now_ms: float | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Return the first active mark matching any (kind, value) pair, or None."""
    marks = load_marks(path)
    if not marks:
        return None
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    wanted = {(kind, value) for kind, value in pairs if kind in _VALID_KINDS and value}
    if not wanted:
        return None
    for mark in marks:
        if (mark["kind"], mark["value"]) in wanted and _is_active(mark, now_ms):
            return mark
    return None


def add_mark(
    kind: str,
    value: str,
    *,
    note_id: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    reason: str = "",
    path: Path | None = None,
) -> None:
    """Add or refresh a mark; expired marks and overflow are pruned. Never raises."""
    if kind not in _VALID_KINDS or not value:
        return
    try:
        marks = load_marks(path)
        now_ms = time.time() * 1000
        marks = [
            m for m in marks
            if (m["kind"], m["value"]) != (kind, value) and _is_active(m, now_ms)
        ]
        mark: dict[str, Any] = {
            "kind": kind,
            "value": value,
            "marked_at": now_ms,
            "ttl_seconds": ttl_seconds,
            "reason": reason,
        }
        if note_id:
            mark["note_id"] = note_id
        marks.append(mark)
        save_marks(marks[-MAX_MARKS:], path)
    except Exception as exc:  # marks are best-effort; never break the caller
        logger.debug("Failed to add risk mark: %s", exc)


def set_current_resource(kind: str, value: str, *, note_id: str = "") -> None:
    """Track the resource of the in-flight command for captcha self-marking."""
    _CURRENT_RESOURCE.clear()
    if kind in _VALID_KINDS and value:
        _CURRENT_RESOURCE.update({"kind": kind, "value": value, "note_id": note_id})


def clear_current_resource() -> None:
    _CURRENT_RESOURCE.clear()


def mark_current_resource(*, reason: str = "", ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
    """Self-mark the in-flight resource after the CLI itself triggered a captcha."""
    if not _CURRENT_RESOURCE:
        return
    add_mark(
        _CURRENT_RESOURCE["kind"],
        _CURRENT_RESOURCE["value"],
        note_id=_CURRENT_RESOURCE.get("note_id", ""),
        ttl_seconds=ttl_seconds,
        reason=reason,
    )


def guard(pairs: Iterable[tuple[str, str]]) -> None:
    """Fast-fail with NeedVerifyError when any (kind, value) pair is marked.

    Maps to the existing ``verification_required`` structured error envelope.
    """
    mark = find_active_mark(pairs)
    if mark is not None:
        logger.warning(
            "Risk mark hit: %s=%s (reason=%s), failing fast without upstream request",
            mark["kind"], mark["value"], mark.get("reason", ""),
        )
        raise NeedVerifyError(
            verify_type=f"risk_mark:{mark['kind']}",
            verify_uuid=str(mark["value"]),
        )
