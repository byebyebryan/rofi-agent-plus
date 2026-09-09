"""Command-line and Rofi entry points."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from . import engine
from .cache import CacheStore
from .config import ConfigError, PickerConfig, load_config
from .rofi import run_rofi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover agent sessions through Rofi Plus contracts"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="refresh and list agent sessions as JSON")
    list_parser.add_argument("--limit", type=int, default=None)

    subparsers.add_parser("active", help="show active local provider processes as JSON")

    refresh_parser = subparsers.add_parser("refresh", help="refresh the session cache")
    refresh_parser.add_argument("--background", action="store_true")
    return parser


def _apply_cli_config(config: PickerConfig, args: argparse.Namespace) -> PickerConfig:
    if args.command != "list" or args.limit is None:
        return config
    return config.with_overrides(max_sessions=args.limit)


def diagnostic_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    refresh_store = CacheStore() if args.command == "refresh" else None
    try:
        config = _apply_cli_config(load_config(), args)
        if args.command == "active":
            print(json.dumps(engine.provider_active_snapshot(), separators=(",", ":")))
            return 0

        store = refresh_store or CacheStore()
        try:
            snapshot = store.refresh(config, force=True)
        finally:
            if args.command == "refresh" and args.background:
                store.clear_owned_background_marker()
        if args.command != "refresh" or not args.background:
            print(json.dumps(snapshot, separators=(",", ":")))
        return 0
    except (ConfigError, engine.PickerError) as exc:
        if refresh_store is not None and args.background:
            refresh_store.clear_owned_background_marker()
        print(f"rofi-agent-plus: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "ROFI_RETV" in os.environ:
        return run_rofi()
    return diagnostic_main(argv)
