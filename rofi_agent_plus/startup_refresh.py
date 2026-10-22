"""One bounded passive refresh shared by pickers using the same catalog."""

from __future__ import annotations

import fcntl
import math
import os
import signal
import stat
import subprocess
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .process import cancel_probes
from .registry import RegistryError, atomic_json, read_private_json

STATUS_ENV = "ROFI_AGENT_PLUS_STARTUP_REFRESH"
POLL_SECONDS = 0.5
REFRESH_SECONDS = 30.0
STOP_SECONDS = 2.0
PENDING_MESSAGE = "Refreshing host inventory…"
LOCK_FD_ENV = "ROFI_AGENT_PLUS_REFRESH_LOCK_FD"


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ValueError("refresh directory is not private")


def _lock_file(path: Path) -> int:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        os.close(descriptor)
        raise ValueError("refresh lock is not private")
    return descriptor


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
    """An explicit refresh may join the catalog's bounded passive worker."""

    status = read_status(environ)
    while status is not None and status.pending:
        time.sleep(min(0.05, max(0.0, status.deadline - time.monotonic())))
        status = read_status(environ)
    return status


@contextmanager
def refresh_worker(environ: Mapping[str, str]):
    """Retain the inherited catalog lock and enforce the refresh's deadline."""

    raw_descriptor = environ.get(LOCK_FD_ENV)
    if raw_descriptor is None:
        # The internal command remains usable synchronously by controlled
        # callers; only detached startup workers receive an inherited lock.
        yield
        return
    if not raw_descriptor.isascii() or not raw_descriptor.isdecimal():
        raise ValueError("refresh lock descriptor is invalid")
    descriptor = int(raw_descriptor)
    status = read_status(environ)
    path = Path(environ.get(STATUS_ENV, ""))
    expected = path.with_name("startup-refresh.lock").lstat()
    actual = os.fstat(descriptor)
    if (
        descriptor < 3
        or status is None
        or not status.pending
        or not stat.S_ISREG(actual.st_mode)
        or actual.st_uid != os.getuid()
        or actual.st_mode & 0o077
        or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        raise ValueError("refresh lock descriptor does not match its ticket")
    # Do not let provider/SSH subprocesses inherit the lock. Only this finite
    # worker owns it once the launcher has closed its descriptor.
    os.set_inheritable(descriptor, False)
    prior = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        cancel_probes()
        raise TimeoutError("background inventory deadline expired")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, status.deadline - time.monotonic()))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior)
        os.close(descriptor)


class StartupRefresh:
    """Reserve or join finite refresh work independently of dialog lifetime."""

    def __init__(
        self,
        directory: Path,
        executable: Path,
        environ: Mapping[str, str],
        *,
        only_join: bool = False,
    ):
        _private_directory(directory)
        self.path = directory / "startup-refresh.json"
        self.executable = executable
        self.env = dict(environ, **{STATUS_ENV: str(self.path)})
        self.process: subprocess.Popen[bytes] | None = None
        self.active = False
        self.descriptor: int | None = _lock_file(directory / "startup-refresh.lock")
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.close()
            self.active = True
            return
        except BaseException:
            self.close()
            raise
        if only_join:
            self.close()
            return
        try:
            atomic_json(
                self.path,
                {
                    "schemaVersion": 1,
                    "state": "pending",
                    "deadline": time.monotonic() + REFRESH_SECONDS,
                    "message": PENDING_MESSAGE,
                },
            )
            self.active = True
        except BaseException:
            self.close()
            raise

    def start(self) -> None:
        if self.descriptor is None:
            return
        try:
            self.process = subprocess.Popen(
                [str(self.executable), "_direct-refresh"],
                env=dict(self.env, **{LOCK_FD_ENV: str(self.descriptor)}),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(self.descriptor,),
            )
        except OSError:
            finish_refresh(self.env, "background helper could not start; use Alt+R to retry")
        finally:
            self.close()

    def close(self) -> None:
        """Release an unstarted reservation without stopping a detached worker."""

        if self.descriptor is not None:
            # flock is shared by inherited descriptors: close rather than
            # explicitly unlocking, so a started worker retains ownership.
            os.close(self.descriptor)
            self.descriptor = None

    def stop(self) -> None:
        """Cancel an explicitly owned worker, for controlled callers/cleanup."""

        self.close()
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=STOP_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.wait()
