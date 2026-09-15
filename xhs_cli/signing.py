"""
Main API signing for edith.xiaohongshu.com

Thin adapter over the xhshow library, configured for macOS/Chrome.
Maintains a persistent SessionManager for realistic session simulation.

Public API (unchanged from previous implementation):
  - sign_main_api(method, uri, cookies, ...) -> dict of 5 headers
  - build_get_uri(uri, params) -> str
  - extract_uri(url) -> str
"""

from __future__ import annotations

import time

from xhshow import CryptoConfig, SessionManager, Xhshow
from xhshow.core import common_sign as _common_sign
from xhshow.generators.fingerprint import FingerprintGenerator
from xhshow.utils.url_utils import extract_uri  # noqa: F401 — re-export

from . import fingerprint_store
from .constants import APP_ID, PLATFORM, SDK_VERSION, USER_AGENT

# ─── macOS/Chrome configuration ────────────────────────────────────────────

_config = CryptoConfig().with_overrides(
    PUBLIC_USERAGENT=USER_AGENT,
    SIGNATURE_DATA_TEMPLATE={
        "x0": SDK_VERSION,
        "x1": APP_ID,
        "x2": PLATFORM,
        "x3": "",
        "x4": "",
    },
    SIGNATURE_XSCOMMON_TEMPLATE={
        "s0": 5,
        "s1": "",
        "x0": "1",
        "x1": SDK_VERSION,
        "x2": PLATFORM,
        "x3": APP_ID,
        "x4": "4.86.0",
        "x5": "",
        "x6": "",
        "x7": "",
        "x8": "",
        "x9": -596800761,
        "x10": 0,
        "x11": "normal",
    },
)

_xhshow = Xhshow(_config)


# ─── Persistent fingerprint & session (D1a) ────────────────────────────────
# xhshow 0.1.9 regenerates the hardware fingerprint on every request and keeps
# SessionManager counters in memory only. The overrides below make both
# persistent across processes. See fingerprint_store.py for the xhshow-upgrade
# checklist that keeps these patches valid.


class PersistentFingerprintGenerator(FingerprintGenerator):
    """Reuse the fingerprint persisted on disk instead of randomizing per request."""

    def generate(self, cookies: dict, user_agent: str) -> dict:
        fp = fingerprint_store.load_fingerprint()
        if fp is None:
            fp = super().generate(cookies, user_agent)
            fingerprint_store.save_fingerprint(fp)
            return fp
        # Refresh per-request dynamic fields, mirroring FingerprintGenerator.update().
        fp["x1"] = user_agent
        fp["x39"] = 0
        fp["x44"] = f"{int(time.time() * 1000)}"
        fp["x57"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return fp


# XsCommonSigner instantiates FingerprintGenerator via this module-level name.
_common_sign.FingerprintGenerator = PersistentFingerprintGenerator


class PersistentSessionManager(SessionManager):
    """SessionManager whose counters survive process restarts."""

    def __init__(self, config: CryptoConfig | None = None):
        super().__init__(config)
        self._restored = False

    def _restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        saved = fingerprint_store.load_session()
        if saved:
            self.page_load_timestamp = saved["page_load_timestamp"]
            self.sequence_value = saved["sequence_value"]
            self.window_props_length = saved["window_props_length"]

    def update_state(self):
        self._restore()
        super().update_state()
        fingerprint_store.save_session(
            self.page_load_timestamp,
            self.sequence_value,
            self.window_props_length,
        )


_session = PersistentSessionManager(_config)


# ─── Public API ─────────────────────────────────────────────────────────────


def sign_main_api(
    method: str,
    uri: str,
    cookies: dict[str, str],
    params: dict[str, str | int | list[str]] | None = None,
    payload: dict | None = None,
    timestamp: float | None = None,
) -> dict[str, str]:
    """
    Generate all signing headers for a main API (edith.xiaohongshu.com) request.

    Returns dict with keys: x-s, x-s-common, x-t, x-b3-traceid, x-xray-traceid
    """
    if method.upper() == "GET":
        return _xhshow.sign_headers_get(
            uri, cookies, params=params, timestamp=timestamp, session=_session,
        )
    return _xhshow.sign_headers_post(
        uri, cookies, payload=payload, timestamp=timestamp, session=_session,
    )


def build_get_uri(
    uri: str,
    params: dict[str, str | int | list[str]] | None = None,
) -> str:
    """Build URI with query parameters for GET requests."""
    if not params:
        return uri
    return _xhshow.build_url(uri, params)
