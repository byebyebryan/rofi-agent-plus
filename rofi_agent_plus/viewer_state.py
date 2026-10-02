"""Private endpoint viewer observations supplied by the public Tmux inventory.

This cache never discovers windows or changes provider snapshots. A finite
helper publishes only its current request, and presentation joins observations
to complete, current tmux references at the caller's desktop.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import engine
from .cache import cache_root
from .wire import WireError, decode_document

FRESH_SECONDS = 10
# Leave time for the finite helper and the one-second completion callback.
REFRESH_SECONDS = 7
REQUEST_SECONDS = 30
MAX_BYTES = 1024 * 1024
MAX_ROWS = 128 * 256
_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z", re.ASCII)
_REVISION = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_REASON = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_SESSION = re.compile(r"\$[0-9]+\Z", re.ASCII)
_MAX_INTEGER = 2**63 - 1


class ViewerStateError(engine.PickerError):
    """A bounded viewer-observation failure with no lifecycle consequences."""


def _millis() -> int:
    return time.time_ns() // 1_000_000


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_INTEGER


def _text(value: object, limit: int = 16_384) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def desktop_context(environ: Mapping[str, str] | None = None) -> str:
    """Hash the desktop epoch without storing environment values in the cache."""
    environ = os.environ if environ is None else environ
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot = "unavailable"
    context: list[object] = [boot]
    runtime = environ.get("XDG_RUNTIME_DIR", "")
    for key in ("NIRI_SOCKET", "WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR"):
        value = environ.get(key, "")
        context.append([key, value])
        candidate = value
        if key == "WAYLAND_DISPLAY" and value and not value.startswith("/"):
            candidate = str(Path(runtime) / value) if runtime else ""
        if key in {"NIRI_SOCKET", "WAYLAND_DISPLAY"} and candidate.startswith("/"):
            try:
                metadata = os.stat(candidate)
                context.append([metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns])
            except OSError:
                context.append(None)
    encoded = json.dumps(context, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def make_scope(
    fingerprint: str,
    backend: Mapping[str, object],
    endpoint_host_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    scope: dict[str, object] = {
        "fingerprint": fingerprint,
        "backend": dict(backend),
        "endpointHostId": endpoint_host_id,
        "desktop": desktop_context(environ),
    }
    if not _valid_scope(scope):
        raise ViewerStateError("Viewer observation scope is unavailable")
    return scope


def _valid_scope(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    backend = value.get("backend")
    return bool(
        _text(value.get("fingerprint"), 1024)
        and isinstance(backend, Mapping)
        and backend.get("kind") == "contract"
        and backend.get("capability") == "host-mesh-v1+tmux-session-v1"
        and (
            backend.get("meshRevision") is None
            or isinstance(backend.get("meshRevision"), str)
            and _REVISION.fullmatch(backend["meshRevision"])
        )
        and isinstance(value.get("endpointHostId"), str)
        and _HOST.fullmatch(value["endpointHostId"])
        and isinstance(value.get("desktop"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["desktop"], re.ASCII)
    )


def reference_key(value: object) -> tuple[str, str, str, int] | None:
    if not isinstance(value, Mapping):
        return None
    host, generation = value.get("hostId"), value.get("serverGeneration")
    session, created = value.get("sessionId"), value.get("createdAt")
    if not (
        isinstance(host, str)
        and _HOST.fullmatch(host)
        and _text(generation)
        and isinstance(session, str)
        and len(session) <= 256
        and _SESSION.fullmatch(session)
        and _integer(created)
    ):
        return None
    return host, generation, session, created


def observation(value: object) -> dict[str, object]:
    """Validate only public fields we consume; future extension fields are ignored."""
    if (
        not isinstance(value, Mapping)
        or not isinstance(value.get("state"), str)
        or value.get("state") not in {"open", "none", "unknown"}
    ):
        raise ViewerStateError("Tmux local viewer observation is invalid")
    result: dict[str, object] = {"state": value["state"]}
    if value["state"] == "open":
        if (
            not isinstance(value.get("confidence"), str)
            or value.get("confidence") not in {"confirmed", "matched"}
            or "reason" in value
        ):
            raise ViewerStateError("Tmux local viewer confidence is invalid")
        result["confidence"] = value["confidence"]
    elif "confidence" in value:
        raise ViewerStateError("Tmux local viewer confidence contradicts its state")
    if "reason" in value:
        reason = value["reason"]
        if (
            value["state"] != "unknown"
            or not isinstance(reason, str)
            or not _REASON.fullmatch(reason)
        ):
            raise ViewerStateError("Tmux local viewer reason is invalid")
        result["reason"] = reason
    return result


def observations_from_inventory(
    inventory: Mapping[str, object], scope: Mapping[str, object]
) -> dict[str, object]:
    """Normalize an already wire/authority-validated public inventory."""
    endpoint = inventory.get("viewerEndpoint")
    backend = scope.get("backend")
    if not (
        _valid_scope(scope)
        and type(inventory.get("schemaVersion")) is int
        and inventory.get("schemaVersion") == 1
        and isinstance(endpoint, Mapping)
        and endpoint.get("hostId") == scope.get("endpointHostId")
        and _integer(endpoint.get("observedAt"))
        and isinstance(backend, Mapping)
        and inventory.get("meshRevision") == backend.get("meshRevision")
    ):
        raise ViewerStateError("Tmux viewer endpoint or authority is invalid")
    hosts = inventory.get("hosts")
    if not isinstance(hosts, list) or len(hosts) > 128:
        raise ViewerStateError("Tmux viewer inventory hosts are invalid")
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for host in hosts:
        if not isinstance(host, Mapping):
            raise ViewerStateError("Tmux viewer inventory host is invalid")
        sessions = host.get("sessions")
        if not isinstance(sessions, list) or len(sessions) > 256:
            raise ViewerStateError("Tmux viewer inventory sessions are invalid")
        if host.get("status") != "ok":
            if sessions:
                raise ViewerStateError("Tmux unavailable host has viewer sessions")
            continue
        for session in sessions:
            key = reference_key(session)
            if key is None or key in seen or key[0] != host.get("hostId"):
                raise ViewerStateError("Tmux viewer inventory reference is invalid")
            seen.add(key)
            raw = session.get("localViewer")
            viewer = (
                observation(raw)
                if raw is not None
                else {"state": "unknown", "reason": "observation_missing"}
            )
            rows.append(
                {
                    "sessionRef": dict(
                        zip(
                            ("hostId", "serverGeneration", "sessionId", "createdAt"),
                            key,
                            strict=True,
                        )
                    ),
                    "viewer": viewer,
                }
            )
    return {
        "version": 1,
        "scope": dict(scope),
        "observedAt": endpoint["observedAt"],
        "rows": rows,
    }


def _valid_record(record: object) -> bool:
    if not isinstance(record, Mapping):
        return False
    if (
        type(record.get("version")) is not int
        or record.get("version") != 1
        or not _valid_scope(record.get("scope"))
        or not _integer(record.get("observedAt"))
    ):
        return False
    rows = record.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return False
    seen: set[tuple[str, str, str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            return False
        key = reference_key(row.get("sessionRef"))
        if key is None or key in seen:
            return False
        seen.add(key)
        try:
            observation(row.get("viewer"))
        except (ViewerStateError, TypeError):
            return False
    return True


def _refresh_at(record: Mapping[str, Any]) -> int:
    # An all-Unknown observation (including failed helpers) retains the full
    # retry interval. Known observations renew before their strict expiry.
    interval = (
        REFRESH_SECONDS
        if any(row["viewer"]["state"] != "unknown" for row in record["rows"])
        else FRESH_SECONDS
    )
    return record["observedAt"] + interval * 1000


class ViewerStateStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else cache_root() / "viewer-state"
        self.snapshot_path = self.root / "snapshot.json"
        self.request_path = self.root / "request.json"
        self.lock_path = self.root / "state.lock"

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ViewerStateError("Viewer cache ownership is invalid")
        self.root.chmod(0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_root()
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise ViewerStateError("Viewer cache lock is invalid")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self, path: Path) -> Mapping[str, object] | None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                metadata = os.fstat(fd)
                if not (
                    stat.S_ISREG(metadata.st_mode)
                    and metadata.st_uid == os.getuid()
                    and metadata.st_nlink == 1
                    and stat.S_IMODE(metadata.st_mode) == 0o600
                    and metadata.st_size <= MAX_BYTES
                ):
                    return None
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(MAX_BYTES + 1)
            finally:
                os.close(fd)
            value = decode_document(raw, limit=MAX_BYTES)
            return value if isinstance(value, Mapping) else None
        except (OSError, WireError, ValueError):
            return None

    def _write(self, path: Path, record: Mapping[str, object]) -> None:
        raw = (
            json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        ).encode()
        if len(raw) > MAX_BYTES:
            raise ViewerStateError("Viewer cache exceeded its size bound")
        fd, temporary = tempfile.mkstemp(prefix=".viewer.", dir=self.root)
        temporary_path = Path(temporary)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def current(
        self, scope: Mapping[str, object], *, now: int | None = None
    ) -> Mapping[str, object] | None:
        record = self._read(self.snapshot_path)
        now = _millis() if now is None else now
        if not (
            _valid_record(record)
            and record.get("scope") == dict(scope)
            and 0 <= now - record["observedAt"] < FRESH_SECONDS * 1000
        ):
            return None
        return record

    def pending(
        self, scope: Mapping[str, object] | None = None, *, now: int | None = None
    ) -> Mapping[str, object] | None:
        request = self._read(self.request_path)
        now = _millis() if now is None else now
        if not (
            request is not None
            and type(request.get("version")) is int
            and request.get("version") == 1
            and isinstance(request.get("requestId"), str)
            and _ID.fullmatch(request["requestId"])
            and _valid_scope(request.get("scope"))
            and _integer(request.get("requestedAt"))
            and 0 <= now - request["requestedAt"] < REQUEST_SECONDS * 1000
            and (scope is None or request.get("scope") == dict(scope))
        ):
            return None
        return request

    def request(
        self,
        scope: Mapping[str, object],
        command: Callable[[str], list[str]],
        *,
        spawn: bool = True,
    ) -> bool:
        if not _valid_scope(scope):
            raise ViewerStateError("Viewer observation scope is unavailable")
        with self._locked():
            now = _millis()
            record = self.current(scope, now=now)
            if record is not None and now < _refresh_at(record):
                return False
            if self.pending(scope, now=now) is not None:
                return True
            request_id = secrets.token_hex(16)
            self._write(
                self.request_path,
                {
                    "version": 1,
                    "requestId": request_id,
                    "scope": dict(scope),
                    "requestedAt": now,
                },
            )
        if spawn:
            try:
                environment = {
                    key: value for key, value in os.environ.items() if not key.startswith("ROFI_")
                }
                subprocess.Popen(
                    command(request_id),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                    env=environment,
                )
            except OSError:
                self.publish(request_id, scope, error="refresh_spawn_failed")
                return False
        return True

    def publish(
        self,
        request_id: str,
        scope: Mapping[str, object],
        inventory: Mapping[str, object] | None = None,
        *,
        error: str = "refresh_failed",
    ) -> bool:
        record = (
            observations_from_inventory(inventory, scope)
            if inventory is not None
            else {
                "version": 1,
                "scope": dict(scope),
                "observedAt": _millis(),
                "rows": [],
                "error": error,
            }
        )
        with self._locked():
            request = self.pending(scope)
            if request is None or request.get("requestId") != request_id:
                return False
            self._write(self.snapshot_path, record)
            self.request_path.unlink(missing_ok=True)
            return True

    def ingest(self, inventory: Mapping[str, object], scope: Mapping[str, object]) -> bool:
        """Take a normal bulk refresh without replacing a newer observation/request."""
        record = observations_from_inventory(inventory, scope)
        return self._ingest_record(record)

    def ingest_failure(self, scope: Mapping[str, object], observed_at: int) -> bool:
        if not _valid_scope(scope) or not _integer(observed_at):
            return False
        return self._ingest_record(
            {
                "version": 1,
                "scope": dict(scope),
                "observedAt": observed_at,
                "rows": [],
                "error": "refresh_failed",
            }
        )

    def _ingest_record(self, record: Mapping[str, object]) -> bool:
        with self._locked():
            previous = self._read(self.snapshot_path)
            request = self.pending()
            if (
                previous is not None
                and _integer(previous.get("observedAt"))
                and previous["observedAt"] > record["observedAt"]
            ) or (request is not None and request["requestedAt"] > record["observedAt"]):
                return False
            self._write(self.snapshot_path, record)
            if request is not None:
                # This normal observation is newer than the request, even
                # when authority/desktop changed while its helper was running.
                self.request_path.unlink(missing_ok=True)
            return True

    def decorate(
        self, snapshot: Mapping[str, Any], scope: Mapping[str, object], *, now: int | None = None
    ) -> dict[str, Any]:
        """Overlay display observations on copies, preserving all provider timestamps."""
        record = self.current(scope, now=now)
        indexed = (
            {reference_key(row["sessionRef"]): dict(row["viewer"]) for row in record["rows"]}
            if record is not None
            else {}
        )

        def row_copy(row: object) -> object:
            if not isinstance(row, Mapping):
                return row
            result = dict(row)
            reference = row.get("tmux")
            # Provider rows carry owner identity outside their subordinate
            # tmux record, just as the existing action path does.
            key = (
                reference_key({**reference, "hostId": row.get("hostId")})
                if isinstance(reference, Mapping)
                and reference.get("hostId", row.get("hostId")) == row.get("hostId")
                else None
            )
            backend = scope.get("backend")
            valid = (
                key is not None
                and isinstance(backend, Mapping)
                and snapshot.get("backend") == backend
                and isinstance(reference, Mapping)
                and reference.get("meshRevision") == backend.get("meshRevision")
                and row.get("sourceObservation") == "current"
                and not row.get("tmuxStale")
                and not row.get("tmuxAmbiguous")
            )
            result["localViewer"] = (
                indexed.get(key, {"state": "unknown", "reason": "unobserved"})
                if valid
                else {"state": "unknown", "reason": "association_unknown"}
            )
            return result

        result = dict(snapshot)
        if isinstance(snapshot.get("sessions"), list):
            result["sessions"] = [row_copy(row) for row in snapshot["sessions"]]
        hosts = snapshot.get("hosts")
        if isinstance(hosts, Mapping):
            result["hosts"] = {
                key: {**host, "sessions": [row_copy(row) for row in host["sessions"]]}
                if isinstance(host, Mapping) and isinstance(host.get("sessions"), list)
                else host
                for key, host in hosts.items()
            }
        result["_viewerWatch"] = True
        result["_viewerObservedAt"] = record.get("observedAt") if record is not None else None
        result["_viewerRefreshAt"] = _refresh_at(record) if record is not None else None
        result["_viewerPending"] = self.pending(scope, now=now) is not None
        return result
