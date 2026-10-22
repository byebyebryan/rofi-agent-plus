"""One bounded, dialog-owned background catalog refresh and redraw ticket."""

from __future__ import annotations

import math
import os
import signal
import stat
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .registry import RegistryError, atomic_json, read_private_json

STATUS_ENV = "ROFI_AGENT_PLUS_STARTUP_REFRESH"
POLL_SECONDS = 0.5
REFRESH_SECONDS = 30.0
STOP_SECONDS = 2.0
PENDING_MESSAGE = "Refreshing host inventory…"


@dataclass(frozen=True)
class RefreshStatus:
    state: str
    deadline: float
    message: str

    @property
    def pending(self) -> bool:
        return self.state == "pending"


def read_status(environ: Mapping[str, str]) -> RefreshStatus | None:
    """A ticket controls redraws only; it never grants an agent action."""

    name = environ.get(STATUS_ENV)
    if not name:
        return None
    path = Path(name)
    try:
        parent = path.parent.lstat()
        if (
            not path.is_absolute()
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            return None
        value = read_private_json(path, limit=4096)
    except (OSError, RegistryError):
        return None
    if (
        not isinstance(value, dict)
        or type(value.get("schemaVersion")) is not int
        or value["schemaVersion"] != 1
    ):
        return None
    state, deadline, message = value.get("state"), value.get("deadline"), value.get("message")
    if (
        not isinstance(state, str)
        or state not in {"pending", "complete", "failed"}
        or type(deadline) not in {int, float}
        or not math.isfinite(deadline)
        or not isinstance(message, str)
        or len(message) > 720
    ):
        return None
    now = time.monotonic()
    if deadline > now + REFRESH_SECONDS + 1:
        return None
    if state == "pending" and now >= deadline:
        return RefreshStatus("failed", deadline, "Host refresh timed out; use Alt+R to retry")
    return RefreshStatus(state, deadline, message)


def finish_refresh(environ: Mapping[str, str], error: str | None = None) -> None:
    status = read_status(environ)
    if status is None:
        return
    atomic_json(
        Path(environ[STATUS_ENV]),
        {
            "schemaVersion": 1,
            "state": "failed" if error else "complete",
            "deadline": status.deadline,
            "message": f"Host refresh failed · {error}"[:720] if error else "",
        },
    )


def wait_for_refresh(environ: Mapping[str, str]) -> RefreshStatus | None:
    """An explicit refresh may join the launcher's bounded passive worker."""

    status = read_status(environ)
    while status is not None and status.pending:
        time.sleep(min(0.05, max(0.0, status.deadline - time.monotonic())))
        status = read_status(environ)
    return status


class StartupRefresh:
    """The launcher cancels this passive child when its Rofi dialog exits."""

    def __init__(self, directory: Path, executable: Path, environ: Mapping[str, str]):
        self.path = directory / "startup-refresh.json"
        self.executable = executable
        self.env = dict(environ, **{STATUS_ENV: str(self.path)})
        self.process: subprocess.Popen[bytes] | None = None
        atomic_json(
            self.path,
            {
                "schemaVersion": 1,
                "state": "pending",
                "deadline": time.monotonic() + REFRESH_SECONDS,
                "message": PENDING_MESSAGE,
            },
        )

    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                [str(self.executable), "_direct-refresh"],
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            finish_refresh(self.env, "background helper could not start; use Alt+R to retry")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=STOP_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.wait()
