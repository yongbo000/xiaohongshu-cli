"""Common helpers for CLI commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import click

from .. import risk_state
from ..client import XhsClient
from ..cookies import get_cookies
from ..error_codes import error_code_for_exception
from ..exceptions import (
    NeedVerifyError,
    NoCookieError,
    SessionExpiredError,
    XhsApiError,
)
from ..formatter import emit_error, print_error

T = TypeVar("T")


def structured_output_options(command: Callable) -> Callable:
    """Add --json/--yaml options to a Click command."""
    command = click.option("--yaml", "as_yaml", is_flag=True, help="Output as YAML.")(command)
    command = click.option("--json", "as_json", is_flag=True, help="Output as JSON.")(command)
    return command


def compact_output_option(command: Callable | None = None, *, help: str | None = None) -> Callable:
    """Add a --compact option for whitelist-projected structured output."""
    option = click.option(
        "--compact",
        is_flag=True,
        help=help
        or "Trim structured output to essential fields "
        "(note_id, xsec_token, title, author, liked, note_type, pagination).",
    )
    if command is None:
        return option
    return option(command)


def _cookie_source(ctx) -> str:
    return ctx.obj.get("cookie_source", "auto") if ctx.obj else "auto"


def get_client(ctx, *, force_refresh: bool = False) -> XhsClient:
    """Get a local client from the click context."""
    _browser, cookies = get_cookies(_cookie_source(ctx), force_refresh=force_refresh)
    return XhsClient(cookies)


def _fail_fast_if_cooling_down() -> None:
    """Block any authenticated action while a persisted risk cooldown is active.

    Guarantees the first request after a process/gateway restart does not hit
    the upstream API during a captcha cooldown (D3). Surfaces as the existing
    ``verification_required`` error envelope.
    """
    remaining = risk_state.cooldown_remaining()
    if remaining > 0:
        raise NeedVerifyError(verify_type="cooldown", verify_uuid="risk_state")


def run_client_action(ctx, action: Callable[[XhsClient], T]) -> T:
    """Run an authenticated client action and retry once with fresh browser cookies."""
    _fail_fast_if_cooling_down()
    try:
        with get_client(ctx) as client:
            return action(client)
    except SessionExpiredError as exc:
        try:
            with get_client(ctx, force_refresh=True) as client:
                return action(client)
        except NoCookieError:
            raise exc from None


def handle_command(
    ctx,
    *,
    action: Callable[[XhsClient], T],
    render: Callable[[T], None] | None,
    as_json: bool,
    as_yaml: bool,
    prefix: str | None = None,
    project: Callable[[T], Any] | None = None,
):
    """Run a client action, emit structured output if requested, else render.

    ``project`` optionally transforms the structured payload (e.g. --compact
    projections); the rich rendering path always sees the raw data.
    """
    from ..formatter import maybe_print_structured

    try:
        data = run_client_action(ctx, action)
        if not maybe_print_structured(data, as_json=as_json, as_yaml=as_yaml, project=project) and render:
            render(data)
        return data
    except (XhsApiError, NoCookieError) as exc:
        exit_for_error(exc, as_json=as_json, as_yaml=as_yaml, prefix=prefix)


def handle_errors(
    fn: Callable[[], T],
    *,
    as_json: bool,
    as_yaml: bool,
    prefix: str | None = None,
) -> T:
    """Run arbitrary command logic and funnel failures through exit_for_error."""
    try:
        return fn()
    except (XhsApiError, NoCookieError) as exc:
        exit_for_error(exc, as_json=as_json, as_yaml=as_yaml, prefix=prefix)


def exit_for_error(
    exc: Exception,
    *,
    as_json: bool,
    as_yaml: bool,
    prefix: str | None = None,
) -> None:
    """Emit a structured/non-structured error and terminate the command."""
    message = str(exc)
    if prefix:
        message = f"{prefix}: {message}"

    if emit_error(error_code_for_exception(exc), message, as_json=as_json, as_yaml=as_yaml):
        raise SystemExit(1) from None

    print_error(message)
    raise SystemExit(1) from None
