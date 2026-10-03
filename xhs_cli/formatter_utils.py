"""Base formatting utilities shared across formatter modules.

This module breaks the circular dependency between formatter.py and
formatter_renderers.py by providing the shared primitives both need.
"""

import json
import os
import sys
from collections.abc import Callable
from typing import Any

import click
import yaml
from rich.console import Console

console = Console(stderr=True)
error_console = Console(stderr=True)
_stdout = Console()
_OUTPUT_ENV = "OUTPUT"
_SCHEMA_VERSION = "1"


# ─── Output format resolution ──────────────────────────────────────────────


def resolve_output_format(*, as_json: bool, as_yaml: bool) -> str | None:
    """Resolve explicit flags first, then env override, then TTY default."""
    if as_json and as_yaml:
        raise click.UsageError("Use only one of --json or --yaml.")
    if as_yaml:
        return "yaml"
    if as_json:
        return "json"

    output_mode = os.getenv(_OUTPUT_ENV, "auto").strip().lower()
    if output_mode == "yaml":
        return "yaml"
    if output_mode == "json":
        return "json"
    if output_mode == "rich":
        return None

    if not sys.stdout.isatty():
        return "yaml"
    return None


# ─── Structured output ─────────────────────────────────────────────────────


def print_json(data: Any) -> None:
    """Print raw JSON output to stdout."""
    _stdout.print_json(json.dumps(data, ensure_ascii=False, indent=2))


# Local patch for a PyYAML upstream defect (present in 6.0.2, 6.0.3 and
# upstream main; https://github.com/yaml/pyyaml/issues/771 is a sister issue).
# Emitter.analyze_scalar() accepts astral-plane characters (non-BMP,
# '\U00010000' <= ch < '\U0010ffff', e.g. emoji) as printable unicode, but
# write_double_quoted() only writes BMP ranges verbatim, so emoji in fields
# forced into double-quoted style (anything containing \n / \t, e.g. a note's
# `desc`) are emitted as noisy \Uxxxxxxxx escape sequences while BMP CJK
# characters pass through untouched.
# The method below is copied verbatim from PyYAML 6.0.3 yaml/emitter.py with
# a single added range check in the "write verbatim" condition (marked
# `# patched`); remove this subclass once upstream fixes the writer.
class _AstralSafeDumper(yaml.SafeDumper):
    """SafeDumper whose double-quoted writer emits astral characters verbatim."""

    def write_double_quoted(self, text, split=True):
        self.write_indicator('"', True)
        start = end = 0
        while end <= len(text):
            ch = None
            if end < len(text):
                ch = text[end]
            if ch is None or ch in '"\\\x85\u2028\u2029\uFEFF' \
                    or not ('\x20' <= ch <= '\x7E'
                            or (self.allow_unicode
                                and ('\xA0' <= ch <= '\uD7FF'
                                    or '\uE000' <= ch <= '\uFFFD'
                                    or '\U00010000' <= ch < '\U0010ffff'))):  # patched
                if start < end:
                    data = text[start:end]
                    self.column += len(data)
                    if self.encoding:
                        data = data.encode(self.encoding)
                    self.stream.write(data)
                    start = end
                if ch is not None:
                    if ch in self.ESCAPE_REPLACEMENTS:
                        data = '\\'+self.ESCAPE_REPLACEMENTS[ch]
                    elif ch <= '\xFF':
                        data = '\\x%02X' % ord(ch)  # noqa: UP031 - verbatim upstream copy
                    elif ch <= '\uFFFF':
                        data = '\\u%04X' % ord(ch)  # noqa: UP031 - verbatim upstream copy
                    else:
                        data = '\\U%08X' % ord(ch)  # noqa: UP031 - verbatim upstream copy
                    self.column += len(data)
                    if self.encoding:
                        data = data.encode(self.encoding)
                    self.stream.write(data)
                    start = end+1
            if 0 < end < len(text)-1 and (ch == ' ' or start >= end)    \
                    and self.column+(end-start) > self.best_width and split:
                data = text[start:end]+'\\'
                if start < end:
                    start = end
                self.column += len(data)
                if self.encoding:
                    data = data.encode(self.encoding)
                self.stream.write(data)
                self.write_indent()
                self.whitespace = False
                self.indention = False
                if text[start] == ' ':
                    data = '\\'
                    self.column += len(data)
                    if self.encoding:
                        data = data.encode(self.encoding)
                    self.stream.write(data)
            end += 1
        self.write_indicator('"', False)


def print_yaml(data: Any) -> None:
    """Print raw YAML output to stdout."""
    click.echo(
        yaml.dump(
            data,
            Dumper=_AstralSafeDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    )


def success_payload(data: Any) -> dict[str, Any]:
    """Wrap structured success data in the shared agent schema."""
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "data": data,
    }


def error_payload(code: str, message: str, *, details: Any | None = None) -> dict[str, Any]:
    """Wrap structured error data in the shared agent schema."""
    error = {
        "code": code,
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return {
        "ok": False,
        "schema_version": _SCHEMA_VERSION,
        "error": error,
    }


def _normalize_success_payload(data: Any) -> Any:
    """Wrap plain structured data in the shared agent success schema."""
    if isinstance(data, dict) and data.get("schema_version") == _SCHEMA_VERSION and "ok" in data:
        return data
    return success_payload(data)


def maybe_print_structured(
    data: Any,
    *,
    as_json: bool,
    as_yaml: bool,
    project: Callable[[Any], Any] | None = None,
) -> bool:
    """Print structured output when requested or when stdout is non-TTY.

    ``project`` optionally transforms the payload (e.g. --compact/--fields
    projections) before it is wrapped in the shared envelope. It never
    affects the rich rendering path.
    """
    fmt = resolve_output_format(as_json=as_json, as_yaml=as_yaml)
    if not fmt:
        return False
    if project is not None:
        data = project(data)
    payload = _normalize_success_payload(data)
    if fmt == "json":
        print_json(payload)
    else:
        print_yaml(payload)
    return True


def emit_error(
    code: str,
    message: str,
    *,
    as_json: bool | None = None,
    as_yaml: bool | None = None,
    details: Any | None = None,
) -> bool:
    """Emit a structured error when the active output mode is machine-readable."""
    if as_json is None or as_yaml is None:
        ctx = click.get_current_context(silent=True)
        params = ctx.params if ctx is not None else {}
        as_json = bool(params.get("as_json", False)) if as_json is None else as_json
        as_yaml = bool(params.get("as_yaml", False)) if as_yaml is None else as_yaml

    fmt = resolve_output_format(as_json=bool(as_json), as_yaml=bool(as_yaml))
    if fmt is None:
        return False

    payload = error_payload(code, message, details=details)
    if fmt == "json":
        print_json(payload)
    else:
        print_yaml(payload)
    return True


# ─── UI helpers ─────────────────────────────────────────────────────────────


def print_error(message: str) -> None:
    """Print error message."""
    if emit_error("api_error", message):
        return
    error_console.print(f"[red]✗[/red] {message}")


def print_success(message: str) -> None:
    """Print success message."""
    console.print(f"[green]✓[/green] {message}")


def print_info(message: str) -> None:
    """Print info message."""
    console.print(f"[dim]ℹ[/dim] {message}")


# ─── Data formatting ───────────────────────────────────────────────────────


def coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort integer coercion for reverse-engineered API fields."""
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


def format_count(n: int | str) -> str:
    """Format number for display (e.g., 12345 → 1.2万)."""
    if isinstance(n, str):
        try:
            n = int(n)
        except ValueError:
            return str(n)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)
