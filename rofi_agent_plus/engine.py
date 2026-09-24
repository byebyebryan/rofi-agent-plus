"""Provider-native parsing, process correlation, and row merging.

Host discovery, SSH policy, tmux inventory, terminal spawning, and lifecycle
actions belong to the companion Rofi Plus commands.  This module intentionally
contains no generic host, SSH, tmux, Niri, or terminal implementation.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_LIMIT = 40
DEFAULT_TIMEOUT = 4.0
VERSION = "0.5.3"
UUID_PATTERN = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
OPENCODE_ID_PATTERN = re.compile(r"ses_[A-Za-z0-9]+")


class PickerError(RuntimeError):
    """An expected failure that can be reported without a traceback."""


def _process_arguments(pid: int) -> list[str]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except (FileNotFoundError, PermissionError, OSError):
        return []


def _is_shared_codex_app_server(arguments: Sequence[str]) -> bool:
    try:
        app_server_index = arguments.index("app-server")
    except ValueError:
        return False
    for index, argument in enumerate(arguments[app_server_index + 1 :]):
        endpoint = (
            arguments[app_server_index + 2 + index]
            if argument == "--listen" and app_server_index + 2 + index < len(arguments)
            else argument.partition("=")[2]
            if argument.startswith("--listen=")
            else None
        )
        if endpoint is not None:
            return endpoint not in {"stdio://", "off"}
    return False


def _process_table() -> tuple[dict[int, int], set[int], set[int], set[int]]:
    result = subprocess.run(
        ["ps", "-u", str(os.getuid()), "-o", "pid=,ppid=,comm="],
        check=True,
        capture_output=True,
        text=True,
    )
    parents: dict[int, int] = {}
    codex: set[int] = set()
    claude: set[int] = set()
    opencode: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, parent = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        parents[pid] = parent
        command = parts[2].casefold()
        if "codex" in command and not _is_shared_codex_app_server(_process_arguments(pid)):
            codex.add(pid)
        if "claude" in command:
            claude.add(pid)
        if "opencode" in command:
            opencode.add(pid)
    return parents, codex, claude, opencode


def _rollout_is_subagent(path: str) -> bool | None:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            record = json.loads(stream.readline())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, Mapping) or record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    source = payload.get("source") if isinstance(payload, Mapping) else None
    return isinstance(source, Mapping) and "subagent" in source


def _rollout_candidates_for_process(
    pid: int, proc_root: str | Path = "/proc"
) -> list[tuple[str, bool | None]]:
    try:
        entries = sorted(
            (Path(proc_root) / str(pid) / "fd").iterdir(), key=lambda entry: entry.name
        )
    except (FileNotFoundError, PermissionError, OSError):
        return []
    candidates: dict[str, bool | None] = {}
    for entry in entries:
        try:
            target = os.readlink(entry)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if "rollout-" not in target or ".jsonl" not in target:
            continue
        match = UUID_PATTERN.search(target)
        if match is None:
            continue
        identifier = match.group(1).lower()
        subagent = _rollout_is_subagent(target)
        if identifier not in candidates or (
            candidates[identifier] is True and subagent is not True
        ):
            candidates[identifier] = subagent
    return sorted(candidates.items())


def _thread_id_for_process(pid: int, proc_root: str | Path = "/proc") -> str | None:
    candidates = _rollout_candidates_for_process(pid, proc_root)
    for subagent in (False, None):
        matches = [identifier for identifier, observed in candidates if observed is subagent]
        if matches:
            return matches[0]
    return None


def _claude_session_id_from_args(arguments: Sequence[str]) -> str | None:
    for index, argument in enumerate(arguments):
        if argument in {"--resume", "-r", "--session-id"} and index + 1 < len(arguments):
            candidate = arguments[index + 1].lower()
            if UUID_PATTERN.fullmatch(candidate):
                return candidate
        for flag in ("--resume=", "--session-id="):
            if argument.startswith(flag):
                candidate = argument[len(flag) :].lower()
                if UUID_PATTERN.fullmatch(candidate):
                    return candidate
    return None


def _claude_session_id_for_process(pid: int) -> str | None:
    arguments = _process_arguments(pid)
    if identifier := _claude_session_id_from_args(arguments):
        return identifier
    try:
        entries = (Path("/proc") / str(pid) / "fd").iterdir()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    for entry in entries:
        try:
            path = Path(os.readlink(entry))
        except (FileNotFoundError, PermissionError, OSError):
            continue
        candidate = path.stem.lower()
        if (
            path.suffix == ".jsonl"
            and "projects" in path.parts
            and UUID_PATTERN.fullmatch(candidate)
        ):
            return candidate
    return None


def _opencode_session_id_from_args(arguments: Sequence[str]) -> str | None:
    for index, argument in enumerate(arguments):
        if argument in {"--session", "-s"} and index + 1 < len(arguments):
            candidate = arguments[index + 1]
            if OPENCODE_ID_PATTERN.fullmatch(candidate):
                return candidate
        if argument.startswith("--session="):
            candidate = argument.partition("=")[2]
            if OPENCODE_ID_PATTERN.fullmatch(candidate):
                return candidate
    return None


def _opencode_session_id_for_process(pid: int) -> str | None:
    return _opencode_session_id_from_args(_process_arguments(pid))


def _ancestry(pid: int, parents: Mapping[int, int]) -> list[int]:
    result: list[int] = []
    current = pid
    while current > 1 and current not in result and len(result) < 128:
        result.append(current)
        current = parents.get(current, 0)
    return result


def provider_active_snapshot() -> dict[str, object]:
    """Return provider processes without inferring tmux ownership.

    The result has the same portable shape as the contract activity probe, so
    it remains useful as a local diagnostic without retaining a generic tmux
    implementation in Agent Plus.
    """

    try:
        parents, codex, claude, opencode = _process_table()
    except (OSError, subprocess.SubprocessError) as error:
        raise PickerError(f"provider activity probe failed: {error}") from error

    def collect(pids: set[int], resolver: Any) -> dict[str, object]:
        rows: dict[str, list[dict[str, object]]] = {}
        for pid in sorted(pids):
            identifier = resolver(pid)
            if identifier:
                rows.setdefault(identifier, []).append(
                    {"pid": pid, "ancestors": _ancestry(pid, parents)}
                )
        return {identifier: {"candidates": values} for identifier, values in rows.items()}

    return {
        "nativeHostname": socket.gethostname(),
        "active": collect(codex, _thread_id_for_process),
        "claudeActive": collect(claude, _claude_session_id_for_process),
        "opencodeActive": collect(opencode, _opencode_session_id_for_process),
    }


def merge_provider_results(
    host_id: str,
    display: str,
    codex_result: object,
    claude_result: object,
    opencode_result: object,
    active_result: object,
    limit: int,
) -> dict[str, Any]:
    """Merge provider-native records for one logical host.

    Tmux association is deliberately added later from the public Tmux Session
    inventory.  Provider activity only establishes whether creation is safe.
    """

    errors: list[dict[str, str]] = []
    if isinstance(active_result, Exception):
        errors.append({"host": host_id, "stage": "active", "message": str(active_result)})
        active: Mapping[str, object] = {}
        activity_known = False
    elif isinstance(active_result, Mapping):
        active = active_result
        activity_known = True
    else:
        errors.append({"host": host_id, "stage": "active", "message": "invalid activity data"})
        active = {}
        activity_known = False

    sessions: list[dict[str, Any]] = []

    def append_rows(
        provider: str,
        result: object,
        active_key: str,
        *,
        installed: bool = False,
    ) -> None:
        if isinstance(result, Exception):
            errors.append({"host": host_id, "stage": active_key, "message": str(result)})
            return
        if provider == "codex":
            values = result if isinstance(result, list) else None
        elif isinstance(result, Mapping) and (not installed or result.get("installed") is True):
            values = result.get("sessions")
        else:
            values = []
        if not isinstance(values, list):
            errors.append(
                {"host": host_id, "stage": active_key, "message": "invalid provider data"}
            )
            return
        observed = active.get(
            {"codex": "active", "claude": "claudeActive", "opencode": "opencodeActive"}[provider],
            {},
        )
        active_map = observed if isinstance(observed, Mapping) else {}
        for value in values:
            if not isinstance(value, Mapping):
                continue
            raw_identifier = value.get("id")
            identifier = str(raw_identifier or "")
            if provider in {"codex", "claude"}:
                identifier = identifier.lower()
                valid = UUID_PATTERN.fullmatch(identifier)
            else:
                valid = OPENCODE_ID_PATTERN.fullmatch(identifier)
            if not valid:
                continue
            name = str(value.get("name") or "").strip()
            cwd = str(value.get("cwd") or "").strip()
            active_info = active_map.get(identifier)
            sessions.append(
                {
                    "kind": provider,
                    "id": identifier,
                    "name": name
                    or Path(cwd).name
                    or (identifier[:8] if provider != "opencode" else identifier),
                    "cwd": cwd,
                    "host": display,
                    "hostId": host_id,
                    "recencyAt": int(value.get("recencyAt") or 0),
                    "updatedAt": int(value.get("updatedAt") or 0),
                    "active": active_info is not None,
                    "activityState": "active"
                    if active_info is not None
                    else "idle"
                    if activity_known
                    else "unknown",
                }
            )

    append_rows("codex", codex_result, "threads")
    append_rows("claude", claude_result, "claude", installed=True)
    append_rows("opencode", opencode_result, "opencode", installed=True)
    sessions.sort(key=lambda item: (item["recencyAt"], item["id"]), reverse=True)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for session in sessions:
        key = (host_id, str(session["kind"]), str(session["id"]))
        if key not in seen:
            seen.add(key)
            unique.append(session)
        if len(unique) >= limit:
            break
    return {"generatedAt": 0, "sessions": unique, "errors": errors}
