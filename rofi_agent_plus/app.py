"""Direct host inventory diagnostics and Rofi script-mode entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

from .cache import CacheError, HostCatalog
from .config import DEFAULT_CATALOG_TTL_SECONDS
from .desktop import DesktopError
from .launcher import initial_frame
from .process import passive_probe_signals
from .rofi import PickerRow, _local_view, build_context, build_rows, clean_text, run_rofi
from .transport import TransportError

DIAGNOSTIC_VERSION = 1
_RETIRED_COMMANDS = {
    "active": "active conversation picking is unsupported; use `list --json` for direct inventory",
    "refresh": "the old cache refresh command is unsupported; the direct picker refreshes bounded host catalogs",
    "_batch-worker": "private batch workers were retired by the direct window contract",
    "_batch-prepare": "private batch workers were retired by the direct window contract",
    "_viewer-refresh": "legacy viewer refresh workers are unsupported by the direct window contract",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Plus direct host and window diagnostics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="refresh and list the direct host catalog")
    list_parser.add_argument("--json", action="store_true", required=True)
    subparsers.add_parser("_direct-refresh", help=argparse.SUPPRESS)
    return parser


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _host_json(host: HostCatalog, ttl_seconds: int, now_ms: int) -> dict[str, object]:
    providers: list[dict[str, object]] = []
    for provider in host.providers:
        providers.append(
            {
                "provider": provider.provider,
                "status": provider.status,
                "observedAt": provider.observed_at,
                "lastCompleteAt": provider.last_complete_at,
                "freshness": provider.freshness(now_ms, ttl_seconds),
                "reason": provider.reason,
                "targets": [target.to_json() for target in provider.targets],
                "errors": list(provider.errors),
            }
        )
    return {
        "hostId": host.host_id,
        "display": host.display,
        "local": host.local,
        "status": host.status,
        "observedAt": host.observed_at,
        "route": host.route,
        "user": host.user.to_json() if host.user is not None else None,
        "providers": providers,
        "errors": list(host.errors),
    }


def _window_json(
    row: PickerRow,
    context: Any,
    windows: tuple[Any, ...],
) -> dict[str, object]:
    value: dict[str, object] = {
        "targetId": row.target_id,
        "instanceId": row.instance_id,
        "ordinal": row.record.value["ordinal"] if row.record is not None else None,
        "state": row.record.state if row.record is not None else "unknown",
        "marker": row.record.marker if row.record is not None else None,
        "presence": "unknown",
        "window": None,
    }
    if row.record is None or context.desktop is None or row.record.state != "live":
        return value
    terminal = row.record.process("terminal")
    if terminal is None:
        return value
    try:
        window = context.desktop.match(windows, row.record.marker, terminal)
    except DesktopError:
        return value
    if window is not None:
        value["presence"] = "present"
        value["window"] = {
            "id": window.window_id,
            "title": clean_text(window.title, limit=1024),
            "focused": window.focused,
        }
    return value


def diagnostic_main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if args.command == "_direct-refresh":
        # The dialog owns this process. Cancellation reaches every bounded
        # passive probe thread, which reaps its own process group before exit.
        context = build_context()
        try:
            with passive_probe_signals():
                context.cache.refresh(context.transport)
            return 0
        except (CacheError, TransportError, OSError, ValueError) as error:
            print(
                f"rofi-agent-plus: direct refresh failed · {clean_text(error, limit=512)}",
                file=sys.stderr,
            )
            return 1

    context = build_context()
    refresh_error: str | None = None
    try:
        with passive_probe_signals():
            context.cache.refresh(context.transport)
    except (CacheError, TransportError, OSError, ValueError) as error:
        refresh_error = clean_text(error, limit=512)
    cache_error: str | None = None
    try:
        snapshot = context.cache.load()
    except CacheError as error:
        snapshot = None
        cache_error = clean_text(error, limit=512)
    windows, window_error = _local_view(context)
    rows = build_rows(
        snapshot,
        registry=context.registry,
        desktop=context.desktop,
        windows=windows,
        desktop_error=window_error,
    )
    now = _now_ms()
    ttl = (
        context.config.desktop.catalog_ttl_seconds
        if context.config
        else DEFAULT_CATALOG_TTL_SECONDS
    )
    output: dict[str, object] = {
        "schemaVersion": DIAGNOSTIC_VERSION,
        "observedAt": now,
        "meshRevision": snapshot.mesh_revision if snapshot is not None else None,
        "catalog": {
            "status": "unknown"
            if snapshot is None
            else ("stale" if snapshot.is_stale(ttl, now_ms=now) else "current"),
            "hosts": [_host_json(host, ttl, now) for host in snapshot.hosts] if snapshot else [],
            "error": cache_error or refresh_error,
        },
        "desktop": {
            "status": "unknown" if window_error else "available",
            "generation": getattr(context.desktop, "generation", None),
            "error": clean_text(window_error, limit=512) if window_error else None,
        },
        "windows": [_window_json(row, context, windows) for row in rows if row.kind == "instance"],
    }
    print(json.dumps(output, ensure_ascii=True, separators=(",", ":")))
    return 1 if refresh_error or cache_error else 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in _RETIRED_COMMANDS:
        print(f"rofi-agent-plus: {_RETIRED_COMMANDS[arguments[0]]}", file=sys.stderr)
        return 2
    if "ROFI_RETV" in os.environ:
        prepared = initial_frame(os.environ)
        if prepared is not None:
            print(prepared, end="")
            return 0
        try:
            with passive_probe_signals():
                return run_rofi()
        except KeyboardInterrupt:
            return 130
    try:
        return diagnostic_main(arguments)
    except KeyboardInterrupt:
        return 130
