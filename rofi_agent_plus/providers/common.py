"""Shared bounded capability-cache helpers for the fixed native adapters."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..model import canonical_json, canonical_revision
from ..process import ProcessError, ProcessResult, run_bounded
from ..wire import WireError, decode_document

CAPABILITY_CACHE_TTL_SECONDS = 300
MAX_CAPABILITY_CACHE_BYTES = 64 << 10
CAPABILITY_PROBE_TIMEOUT_SECONDS = 3.0
_CACHE_RELATIVE = Path("rofi-agent-plus") / "direct-v1" / "provider-capabilities.json"


@dataclass(frozen=True)
class ProbeObservation:
    status: str
    reason: str | None = None


Runner = Callable[..., ProcessResult]


def resolve_executable(command: str, *, env: Mapping[str, str]) -> str | None:
    candidate = shutil.which(command, path=env.get("PATH", ""))
    if candidate is None:
        return None
    try:
        path = Path(candidate).resolve(strict=True)
        info = path.stat()
    except (OSError, RuntimeError):
        return None
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        return None
    return str(path)


def _capability_key(provider: str, adapter_revision: str, executable: str) -> str:
    try:
        info = Path(executable).stat()
    except OSError:
        stat_identity: tuple[int, ...] = ()
    else:
        stat_identity = (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_mode,
        )
    return canonical_revision(
        {
            "provider": provider,
            "adapterRevision": adapter_revision,
            "executable": executable,
            "stat": stat_identity,
        }
    )


def _cache_path(env: Mapping[str, str], user_home: Path | str) -> Path:
    configured = env.get("XDG_CACHE_HOME")
    root = Path(configured) if configured else Path(user_home) / ".cache"
    if not root.is_absolute():
        root = Path(user_home) / ".cache"
    return root / _CACHE_RELATIVE


def _read_cache(path: Path, now: int) -> dict[str, ProbeObservation]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return {}
            raw = stream.read(MAX_CAPABILITY_CACHE_BYTES + 1)
        value = decode_document(raw, limit=MAX_CAPABILITY_CACHE_BYTES)
    except (OSError, WireError):
        return {}
    if not isinstance(value, Mapping) or value.get("schemaVersion") != 1:
        return {}
    entries = value.get("entries")
    if not isinstance(entries, Mapping):
        return {}
    found: dict[str, ProbeObservation] = {}
    for key, entry in list(entries.items())[:16]:
        if not isinstance(key, str) or not isinstance(entry, Mapping):
            continue
        status = entry.get("status")
        observed_at = entry.get("observedAt")
        reason = entry.get("reason")
        if (
            status not in {"supported", "unsupported"}
            or isinstance(observed_at, bool)
            or not isinstance(observed_at, int)
            or now < observed_at
            or now - observed_at > CAPABILITY_CACHE_TTL_SECONDS * 1000
            or (reason is not None and not isinstance(reason, str))
        ):
            continue
        found[key] = ProbeObservation(status, reason)
    return found


def _write_cache(path: Path, entries: Mapping[str, ProbeObservation], now: int) -> None:
    bounded = dict(list(entries.items())[-16:])
    value = {
        "schemaVersion": 1,
        "entries": {
            key: {"status": item.status, "observedAt": now, "reason": item.reason}
            for key, item in bounded.items()
        },
    }
    raw = canonical_json(value) + b"\n"
    if len(raw) > MAX_CAPABILITY_CACHE_BYTES:
        return
    temporary: str | None = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".provider-capabilities-", dir=path.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        return
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _unsupported_output(result: ProcessResult) -> bool:
    output = (result.stdout + b"\n" + result.stderr).decode("utf-8", "replace").casefold()
    return any(
        marker in output
        for marker in (
            "unknown command",
            "unknown subcommand",
            "unrecognized command",
            "not a command",
        )
    )


def probe_capability(
    provider: str,
    adapter_revision: str,
    executable: str,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    user_home: Path | str,
    runner: Runner = run_bounded,
    now_ms: int | None = None,
    timeout_seconds: float = CAPABILITY_PROBE_TIMEOUT_SECONDS,
) -> ProbeObservation:
    """Cache only stable help outcomes, keyed by the executable's file identity."""

    now = int(time.time() * 1000) if now_ms is None else now_ms
    key = _capability_key(provider, adapter_revision, executable)
    cache_path = _cache_path(env, user_home)
    entries = _read_cache(cache_path, now)
    cached = entries.get(key)
    if cached is not None:
        return cached
    try:
        result = runner(
            tuple(argv),
            timeout=min(CAPABILITY_PROBE_TIMEOUT_SECONDS, max(0.001, timeout_seconds)),
            stdout_limit=256 << 10,
            stderr_limit=64 << 10,
            cwd=str(user_home),
            env=dict(env),
        )
    except ProcessError as error:
        return ProbeObservation("unknown", error.code)
    except OSError:
        return ProbeObservation("unknown", "capability_probe_failed")
    if result.returncode == 0:
        observation = ProbeObservation("supported")
    elif _unsupported_output(result):
        observation = ProbeObservation("unsupported", "manager_command_unsupported")
    else:
        observation = ProbeObservation("unknown", "capability_probe_failed")
    if observation.status in {"supported", "unsupported"}:
        entries[key] = observation
        _write_cache(cache_path, entries, now)
    return observation
