"""Persistent device fingerprint and signing-session counters (D1a).

State file: ``~/.xiaohongshu-cli/fingerprint.json`` (0600).

Why: xhshow regenerates the hardware fingerprint (GPU, screen, CPU cores,
memory, canvas hash, ...) on **every HTTP request** (verified for 0.1.9 and
0.2.0: ``xhshow/core/common_sign.py`` → ``XsCommonSigner.sign`` →
``FingerprintGenerator.generate``) and keeps the ``SessionManager`` counters in
process memory only. The same
``a1`` cookie therefore claims a different machine on every request and a
brand-new session on every process start — both are risk signals that request
pacing cannot fix. This module persists the fingerprint and session counters
so every process presents one stable device and one continuous session.

Schema (version 1)::

    {
      "version": 1,
      "created_at": <epoch_seconds>,
      "fingerprint": {...},          # full xhshow fingerprint dict
      "session": {
        "page_load_timestamp": <epoch_ms>,
        "sequence_value": <int>,
        "window_props_length": <int>,
        "updated_at": <epoch_seconds>
      }
    }

Rotation: the file is deleted (and thus regenerated) whenever fresh cookies
are saved (``save_cookies``, i.e. login/cookie refresh — a new identity gets
a new device), or explicitly via ``xhs fingerprint-reset`` / deleting the
file.

xhshow upgrade checkpoints — after bumping xhshow, verify:
- ``xhs_cli/signing.py`` monkeypatches
  ``xhshow.core.common_sign.FingerprintGenerator``; ``XsCommonSigner.__init__``
  must still instantiate that module-level name (not a direct import binding).
- ``PersistentSessionManager`` subclasses ``xhshow.session.SessionManager``
  and relies on ``__init__``/``update_state`` semantics and the attribute
  names ``page_load_timestamp`` / ``sequence_value`` / ``window_props_length``.
- ``REQUIRED_FP_KEYS`` below must still cover every key
  ``FingerprintGenerator.generate_b1`` reads.

Field stability contract (verified for xhshow 0.1.9 and 0.2.0): only ``x1``
(user agent), ``x39``, ``x44`` (millisecond timestamp) and ``x57`` (cookie
string) are refreshed per request by ``PersistentFingerprintGenerator`` —
these mirror the fields ``FingerprintGenerator.update`` treats as dynamic.
Every other persisted field (hardware/GPU/screen/canvas hashes, ``x53``
random salt, ...) must stay stable for the lifetime of the store, otherwise
the same ``a1`` cookie presents a different machine.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .constants import FINGERPRINT_FILE
from .cookies import get_config_dir

logger = logging.getLogger(__name__)

STORE_VERSION = 1

# Keys consumed by xhshow's FingerprintGenerator.generate_b1 (verified
# identical in 0.1.9 and 0.2.0). A persisted fingerprint missing any of these (e.g. after an xhshow upgrade
# changes the fingerprint shape) is discarded and regenerated.
REQUIRED_FP_KEYS = frozenset({
    "x33", "x34", "x35", "x36", "x37", "x38", "x39", "x42", "x43", "x44",
    "x45", "x46", "x48", "x49", "x50", "x51", "x52", "x82",
})


def get_fingerprint_path() -> Path:
    """Get fingerprint store file path."""
    return get_config_dir() / FINGERPRINT_FILE


def _load_store(path: Path | None = None) -> dict[str, Any]:
    path = path or get_fingerprint_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to load fingerprint store %s: %s", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_store(store: dict[str, Any], path: Path | None = None) -> None:
    path = path or get_fingerprint_path()
    store["version"] = STORE_VERSION
    try:
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(store))
        tmp_path.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        logger.debug("Failed to save fingerprint store %s: %s", path, exc)


def load_fingerprint(path: Path | None = None) -> dict[str, Any] | None:
    """Load the persisted fingerprint, or None if missing/corrupt/incompatible."""
    fp = _load_store(path).get("fingerprint")
    if not isinstance(fp, dict) or not REQUIRED_FP_KEYS.issubset(fp.keys()):
        return None
    return fp


def save_fingerprint(fingerprint: dict[str, Any], path: Path | None = None) -> None:
    """Persist a freshly generated fingerprint; never raises."""
    store = _load_store(path)
    store.setdefault("created_at", time.time())
    store["fingerprint"] = fingerprint
    _save_store(store, path)


def load_session(path: Path | None = None) -> dict[str, int] | None:
    """Load persisted SessionManager counters, or None if missing/invalid."""
    session = _load_store(path).get("session")
    if not isinstance(session, dict):
        return None
    try:
        return {
            "page_load_timestamp": int(session["page_load_timestamp"]),
            "sequence_value": int(session["sequence_value"]),
            "window_props_length": int(session["window_props_length"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def save_session(
    page_load_timestamp: int,
    sequence_value: int,
    window_props_length: int,
    path: Path | None = None,
) -> None:
    """Persist SessionManager counters; never raises."""
    store = _load_store(path)
    store["session"] = {
        "page_load_timestamp": int(page_load_timestamp),
        "sequence_value": int(sequence_value),
        "window_props_length": int(window_props_length),
        "updated_at": time.time(),
    }
    _save_store(store, path)


def reset_state(path: Path | None = None) -> None:
    """Delete the persisted fingerprint/session so both rotate on next use."""
    path = path or get_fingerprint_path()
    try:
        path.unlink()
        logger.debug("Fingerprint store reset: %s", path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Failed to reset fingerprint store %s: %s", path, exc)
