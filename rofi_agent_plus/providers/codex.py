"""Codex native environment and Agent Command Center adapter."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from ..process import ProcessResult
from .common import ProbeObservation

PROVIDER = "codex"
ADAPTER_REVISION = "codex-agents-v1"
EXECUTABLE = "codex"
ENTRY_COMMAND = ("agents",)
_MISSING_ENDPOINT = re.compile(
    r"(?:socket|daemon endpoint|connect(?:ion)? to .*daemon).*"
    r"(?:no such file or directory)"
    r"|no daemon(?: is)? running",
    re.IGNORECASE,
)


def default_home(user_home: Path, env: Mapping[str, str]) -> str:
    del env
    return str((user_home / ".codex").resolve(strict=False))


def capability_argv(executable: str) -> tuple[str, ...]:
    return (executable, "agents", "--help")


def status_argv(executable: str) -> tuple[str, ...]:
    return (executable, "app-server", "daemon", "version")


def entry_argv(executable: str) -> tuple[str, ...]:
    return (executable, *ENTRY_COMMAND)


def launch_spec(executable: str | None, home: str) -> dict[str, object]:
    return {
        "provider": PROVIDER,
        "adapterRevision": ADAPTER_REVISION,
        "executablePath": executable,
        "entryArgv": list(ENTRY_COMMAND),
        "environment": {"CODEX_HOME": home},
        "clearedEnvironment": ["CODEX_PLUS_SOCKET"],
    }


def entry_environment(base: Mapping[str, str], home: str) -> dict[str, str]:
    selected = dict(base)
    selected.pop("CODEX_PLUS_SOCKET", None)
    selected["CODEX_HOME"] = home
    return selected


def home_problem(home: str, *, is_default: bool) -> str | None:
    path = Path(home)
    if not path.exists():
        return None if is_default else "home_missing"
    try:
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            return "home_unavailable"
    except OSError:
        return "home_unavailable"
    if is_default:
        return None
    config = path / "config.toml"
    try:
        info = config.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(config, os.R_OK):
            return "config_unreadable"
        with config.open("rb") as stream:
            stream.read(1)
    except OSError:
        return "config_missing_or_unreadable"
    return None


def daemon_observation(result: ProcessResult) -> ProbeObservation:
    if result.returncode == 0:
        try:
            value = json.loads(result.stdout.decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ProbeObservation("unknown", "malformed_status")
        if (
            isinstance(value, Mapping)
            and value.get("status") == "running"
            and isinstance(value.get("backend"), str)
            and value["backend"]
            and isinstance(value.get("appServerVersion"), str)
            and value["appServerVersion"]
        ):
            return ProbeObservation("running")
        return ProbeObservation("unknown", "malformed_status")
    diagnostic = (result.stderr + b"\n" + result.stdout).decode("utf-8", "replace")
    if _MISSING_ENDPOINT.search(diagnostic):
        return ProbeObservation("stopped", "daemon_endpoint_absent")
    return ProbeObservation("unknown", "status_probe_failed")
