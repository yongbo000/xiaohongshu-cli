"""Account-level risk/backoff state, persisted across processes.

State file: ``~/.xiaohongshu-cli/risk_state.json`` (0600). Before this module
every anti-risk counter (captcha count, cooldown, delay escalation) lived in
process memory and was wiped by every CLI/gateway restart — meaning the first
request after a restart fired straight at the upstream API even when the
account was one captcha away from a permanent block.

Schema (version 1)::

    {
      "version": 1,
      "captcha_timestamps": [<epoch_seconds>, ...],   # pruned to the last 24h
      "last_captcha_at": <epoch_seconds>,
      "captcha_count_24h": <int>,
      "cooldown_until": <epoch_seconds>,
      "delay_multiplier": <float>,
      "updated_at": <epoch_seconds>
    }

The gateway (cli-gateway) owns authoritative cooldown-tier escalation
(45min → 4h → permanent-block risk) and may also write ``cooldown_until``
into this file; both sides honor ``max(cooldown_until)``. The CLI only keeps
its own state self-consistent and fast-fails (zero upstream requests) while
the cooldown is active.

A missing or corrupt state file silently degrades to defaults.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .constants import RISK_STATE_FILE
from .cookies import get_config_dir

logger = logging.getLogger(__name__)

STATE_VERSION = 1

# Window over which captcha occurrences are counted.
CAPTCHA_WINDOW_SECONDS = 24 * 3600

# CLI-local cooldown tiers indexed by captcha count within the window
# (1st → 45min, 2nd → 4h, 3rd+ → 24h). Mirrors the gateway tiers so a CLI
# that observes a captcha itself cools down for at least as long as the
# gateway would; the gateway may still extend cooldown_until further.
COOLDOWN_TIERS_SECONDS = (45 * 60, 4 * 3600, 24 * 3600)

MAX_DELAY_MULTIPLIER = 8.0


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "captcha_timestamps": [],
        "last_captcha_at": 0.0,
        "captcha_count_24h": 0,
        "cooldown_until": 0.0,
        "delay_multiplier": 1.0,
        "updated_at": 0.0,
    }


def get_risk_state_path() -> Path:
    """Get risk state file path."""
    return get_config_dir() / RISK_STATE_FILE


def _as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def load_risk_state(path: Path | None = None) -> dict[str, Any]:
    """Load persisted risk state; silently degrade to defaults on any problem."""
    state = default_state()
    path = path or get_risk_state_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return state
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to load risk state %s: %s", path, exc)
        return state
    if not isinstance(raw, dict):
        return state

    now = time.time()
    timestamps = raw.get("captcha_timestamps")
    if isinstance(timestamps, list):
        state["captcha_timestamps"] = [
            ts for ts in (_as_float(t, -1.0) for t in timestamps)
            if 0 < ts and now - ts < CAPTCHA_WINDOW_SECONDS
        ]
    state["last_captcha_at"] = _as_float(raw.get("last_captcha_at"))
    state["captcha_count_24h"] = len(state["captcha_timestamps"])
    state["cooldown_until"] = _as_float(raw.get("cooldown_until"))
    state["delay_multiplier"] = min(
        MAX_DELAY_MULTIPLIER, max(1.0, _as_float(raw.get("delay_multiplier"), 1.0))
    )
    state["updated_at"] = _as_float(raw.get("updated_at"))
    return state


def save_risk_state(state: dict[str, Any], path: Path | None = None) -> None:
    """Persist risk state atomically with 0600 permissions; never raises."""
    path = path or get_risk_state_path()
    try:
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(state, indent=2))
        tmp_path.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        logger.debug("Failed to save risk state %s: %s", path, exc)


def cooldown_remaining(state: dict[str, Any] | None = None, now: float | None = None) -> float:
    """Seconds of cooldown left; 0 when no cooldown is active.

    When ``state`` is omitted the state file is re-read from disk so external
    (gateway) updates to ``cooldown_until`` are honored immediately.
    """
    now = time.time() if now is None else now
    if state is None:
        state = load_risk_state()
    until = _as_float(state.get("cooldown_until"))
    return max(0.0, until - now)


def record_captcha(path: Path | None = None, now: float | None = None) -> dict[str, Any]:
    """Record a captcha (HTTP 461/471): bump counters, cooldown, and delay tier."""
    now = time.time() if now is None else now
    state = load_risk_state(path)

    timestamps = [ts for ts in state["captcha_timestamps"] if now - ts < CAPTCHA_WINDOW_SECONDS]
    timestamps.append(now)
    count = len(timestamps)

    tier = COOLDOWN_TIERS_SECONDS[min(count, len(COOLDOWN_TIERS_SECONDS)) - 1]
    state["captcha_timestamps"] = timestamps
    state["last_captcha_at"] = now
    state["captcha_count_24h"] = count
    state["cooldown_until"] = max(_as_float(state.get("cooldown_until")), now + tier)
    state["delay_multiplier"] = min(MAX_DELAY_MULTIPLIER, 2.0 ** count)
    state["updated_at"] = now
    save_risk_state(state, path)
    return state
