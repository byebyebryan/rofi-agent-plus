"""Small bounded subprocess helper for passive host probes."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STDOUT_LIMIT = 1 << 20
DEFAULT_STDERR_LIMIT = 64 << 10
_CANCELLED = threading.Event()


def cancel_probes() -> None:
    """Cancel only passive probes in this launcher-owned refresh process."""
    _CANCELLED.set()


@contextmanager
def passive_probe_signals():
    """Let a passive helper reap nested probes when its transport disconnects."""
    prior = {
        number: signal.getsignal(number)
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        for number in prior:
            signal.signal(number, lambda *_: cancel_probes())
        yield
        if _CANCELLED.is_set():
            raise KeyboardInterrupt
    finally:
        for number, handler in prior.items():
            signal.signal(number, handler)


class ProcessError(RuntimeError):
    """A subprocess could not be run within its bounded resource profile."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


def run_bounded(
    argv: Sequence[str],
    *,
    timeout: float,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Drain both streams incrementally and reap the exact probe on failure."""

    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise ProcessError("invalid_argv", "probe command is invalid")
    if _CANCELLED.is_set():
        raise ProcessError("probe_cancelled", "passive refresh was cancelled")
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None

    def terminate() -> None:
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            if process.poll() is None:
                process.kill()
        process.wait()

    try:
        process = subprocess.Popen(
            tuple(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=None if env is None else dict(env),
            close_fds=True,
            start_new_session=True,
        )
    except OSError as error:
        raise ProcessError("command_unavailable", "probe command could not be started") from error
    try:
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": stdout_limit, "stderr": stderr_limit}
        deadline = time.monotonic() + max(0.001, timeout)
        while selector.get_map():
            if _CANCELLED.is_set():
                raise ProcessError("probe_cancelled", "passive refresh was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessError("probe_timeout", "probe command timed out")
            for key, _events in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                stream = key.data
                buffers[stream].extend(chunk)
                if len(buffers[stream]) > limits[stream]:
                    raise ProcessError(
                        "probe_output_too_large", f"probe {stream} exceeded its byte limit"
                    )
        try:
            returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise ProcessError("probe_timeout", "probe command timed out") from error
        return ProcessResult(
            tuple(argv), returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
        )
    except ProcessError:
        terminate()
        raise
    finally:
        terminate()
        if selector is not None:
            selector.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
