"""Claude Code native configuration and Agent View adapter."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from ..process import ProcessResult
from .common import ProbeObservation

PROVIDER = "claude"
ADAPTER_REVISION = "claude-agents-v1"
EXECUTABLE = "claude"
ENTRY_COMMAND = ("agents",)
_STOPPED = re.compile(
    r"not running|no (?:active )?daemon(?: is| appears to be)? running|daemon is stopped|daemon stopped",
    re.IGNORECASE,
)
_PID = re.compile(r"^pid:\s+[1-9][0-9]*\s*$", re.IGNORECASE | re.MULTILINE)
_CONTROL_REACHABLE = re.compile(r"^\s*control\.sock:\s+reachable\s*$", re.IGNORECASE | re.MULTILINE)


def default_home(user_home: Path, env: Mapping[str, str]) -> str:
    del env
    return str((user_home / ".claude").resolve(strict=False))


def capability_argv(executable: str) -> tuple[str, ...]:
    return (executable, "agents", "--help")


def status_argv(executable: str) -> tuple[str, ...]:
    return (executable, "daemon", "status")


def entry_argv(executable: str) -> tuple[str, ...]:
    return (executable, *ENTRY_COMMAND)


def launch_spec(executable: str | None, home: str) -> dict[str, object]:
    return {
        "provider": PROVIDER,
        "adapterRevision": ADAPTER_REVISION,
        "executablePath": executable,
        "entryArgv": list(ENTRY_COMMAND),
        "environment": {"CLAUDE_CONFIG_DIR": home},
        "clearedEnvironment": [],
    }


def entry_environment(base: Mapping[str, str], home: str) -> dict[str, str]:
    selected = dict(base)
    selected["CLAUDE_CONFIG_DIR"] = home
    return selected


def home_problem(home: str, *, is_default: bool) -> str | None:
    path = Path(home)
    if not path.exists():
        return None if is_default else "config_dir_missing"
    try:
        if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            return "config_dir_unavailable"
    except OSError:
        return "config_dir_unavailable"
    return None


def daemon_observation(result: ProcessResult) -> ProbeObservation:
    output = (result.stdout + b"\n" + result.stderr).decode("utf-8", "replace").strip()
    if output:
        try:
            value = json.loads(output)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, Mapping):
            status = value.get("status")
            if isinstance(status, str):
                status = status.casefold()
                if status in {"stopped", "not_running", "not-running"}:
                    return ProbeObservation("stopped", "daemon_not_running")
                if status in {"running", "ready", "listening"}:
                    return ProbeObservation("running")
    if _STOPPED.search(output):
        return ProbeObservation("stopped", "daemon_not_running")
    if result.returncode == 0 and _PID.search(output) and _CONTROL_REACHABLE.search(output):
        return ProbeObservation("running")
    return ProbeObservation("unknown", "status_probe_failed")
