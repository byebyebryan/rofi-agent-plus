"""Private desktop/target records and bounded, replay-safe launch reservations."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .desktop import Desktop, DesktopError, ProcessIdentity, Window
from .wire import WireError, decode_document, validate_string_bounds

HEX = re.compile(r"[a-f0-9]{64}\Z")
UUID = re.compile(r"[a-f0-9]{32}\Z")
ACTIVE = frozenset({"reserved", "dispatching", "opening", "live", "uncertain"})
STATES = ACTIVE | {"exited", "failed"}
MAX_RECORDS = 512


class RegistryError(RuntimeError):
    pass


def target_storage_key(identity: str) -> str:
    value = identity.removeprefix("sha256:")
    if not HEX.fullmatch(value):
        raise RegistryError("invalid_target_id")
    return value


def private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid() or value.st_mode & 0o077:
            raise RegistryError("runtime_directory_not_private")
    except OSError as error:
        raise RegistryError("runtime_directory_unavailable") from error


def atomic_json(path: Path, value: object) -> None:
    temporary = path.parent / ("." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        raise RegistryError("record_write_failed") from error
    finally:
        temporary.unlink(missing_ok=True)


def _unlink_durable(path: Path) -> None:
    try:
        path.unlink()
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        return
    except OSError as error:
        raise RegistryError("record_write_failed") from error


def read_private_json(path: Path, limit: int = 65536) -> object:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > limit
            ):
                raise RegistryError("invalid_private_record")
            value = decode_document(stream.read(limit + 1), limit=limit)
        validate_string_bounds(value, limit=8192)
        return value
    except (OSError, WireError) as error:
        raise RegistryError("invalid_private_record") from error


@dataclass(frozen=True)
class Instance:
    value: dict[str, object]
    archive_conflict: bool = False

    @property
    def instance_id(self) -> str:
        return str(self.value["instanceId"])

    @property
    def target_id(self) -> str:
        return str(self.value["targetId"])

    @property
    def state(self) -> str:
        state = str(self.value["state"])
        return "uncertain" if self.archive_conflict and state in ACTIVE else state

    @property
    def marker(self) -> str:
        return str(self.value["marker"])

    def process(self, name: str) -> ProcessIdentity | None:
        value = self.value.get(name)
        return ProcessIdentity.parse(value) if value is not None else None


def parse_instance(value: object, generation: str, target_id: str) -> Instance:
    required = {
        "schemaVersion",
        "desktop",
        "targetId",
        "instanceId",
        "requestId",
        "ordinal",
        "marker",
        "state",
        "createdAt",
        "target",
        "entryReference",
        "owner",
        "terminal",
        "wrapper",
        "client",
        "transport",
    }
    if (
        not isinstance(value, dict)
        or type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
        or not required <= set(value)
        or set(value) - required - {"error", "windowId"}
    ):
        raise RegistryError("invalid_instance")
    if value.get("desktop") != generation or value.get("targetId") != target_id:
        raise RegistryError("instance_scope_mismatch")
    for key in ("instanceId", "requestId"):
        if not isinstance(value.get(key), str) or not UUID.fullmatch(value[key]):
            raise RegistryError("invalid_instance_id")
    if value.get("marker") != "org.agentplus." + value["instanceId"]:
        raise RegistryError("invalid_instance_marker")
    if (
        not isinstance(value.get("state"), str)
        or value.get("state") not in STATES
        or type(value.get("ordinal")) is not int
        or value["ordinal"] < 1
    ):
        raise RegistryError("invalid_instance_state")
    if type(value.get("createdAt")) not in (int, float):
        raise RegistryError("invalid_instance_time")
    for key in ("target", "entryReference", "transport"):
        if not isinstance(value.get(key), dict):
            raise RegistryError("invalid_instance_payload")
    try:
        for key in ("owner", "terminal", "wrapper", "client"):
            if value.get(key) is not None:
                ProcessIdentity.parse(value[key])
    except DesktopError as error:
        raise RegistryError("invalid_instance_process") from error
    return Instance(value)


class Registry:
    def __init__(self, generation: str, root: Path | None = None):
        if not HEX.fullmatch(generation):
            raise RegistryError("invalid_desktop_generation")
        self.generation = generation
        if root is None:
            runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
            private_directory(runtime)
            package = runtime / "rofi-agent-plus"
            private_directory(package)
            version = package / "direct-v1"
            private_directory(version)
            root = version / generation
        self.root = root
        private_directory(root)

    def target_directory(self, target_id: str) -> Path:
        if not HEX.fullmatch(target_id):
            raise RegistryError("invalid_target_id")
        path = self.root / target_id
        private_directory(path)
        return path

    def control_path(self, instance_id: str) -> Path:
        if not UUID.fullmatch(instance_id):
            raise RegistryError("invalid_instance_id")
        # Desktop + target digests make the record tree longer than UNIX's
        # socket-name limit. Keep the private socket namespace shallow.
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        private_directory(runtime)
        directory = runtime / "rofi-agent-plus-sockets"
        private_directory(directory)
        path = directory / (instance_id + ".sock")
        if len(os.fsencode(path)) >= 104:
            raise RegistryError("runtime_socket_path_too_long")
        return path

    @contextlib.contextmanager
    def locked(self, target_id: str) -> Iterator[Path]:
        directory = self.target_directory(target_id)
        try:
            descriptor = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "r+") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise RegistryError("invalid_target_lock")
                deadline = time.monotonic() + 0.25
                while True:
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise RegistryError("target_busy") from None
                        time.sleep(0.01)
                yield directory
        except OSError as error:
            raise RegistryError("target_lock_unavailable") from error

    def _records(self, directory: Path) -> list[Instance]:
        if (directory / ".fault").exists():
            raise RegistryError("target_registry_corrupt")
        files = sorted(directory.glob("*.json"))
        if len(files) > MAX_RECORDS:
            raise RegistryError("target_record_limit")
        records = []
        for path in files:
            try:
                record = parse_instance(read_private_json(path), self.generation, directory.name)
                if path.stem != record.instance_id:
                    raise RegistryError("instance_filename_mismatch")
            except RegistryError:
                # Preserve uncertain launch evidence. Quarantine is scoped to this
                # target and must not authorize a replacement first launch.
                if path.exists():
                    path.rename(path.with_suffix(".quarantined"))
                    atomic_json(directory / ".fault", {"reason": "target_registry_corrupt"})
                    raise RegistryError("target_registry_corrupt") from None
                raise
            if record.state in {"exited", "failed"}:
                self._archive_record(directory, record, active_path=path)
                continue
            try:
                archived = self._read_retired_request(directory, record.value["requestId"])
            except RegistryError as error:
                if error.args == ("request_archive_corrupt",):
                    # A corrupt request tombstone blocks that request's replay,
                    # but cannot quarantine or stall other instance requests.
                    records.append(Instance(record.value, archive_conflict=True))
                    continue
                raise
            if archived is not None:
                if archived.instance_id != record.instance_id:
                    records.append(Instance(record.value, archive_conflict=True))
                    continue
                self._ensure_instance_index(directory, archived)
                _unlink_durable(path)
                continue
            records.append(record)
        return records

    @staticmethod
    def _retired_directory(directory: Path, *, create: bool) -> Path | None:
        retired = directory / "retired"
        if create:
            private_directory(retired)
            return retired
        try:
            info = retired.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RegistryError("retired_directory_unavailable") from error
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RegistryError("retired_directory_not_private")
        return retired

    def _read_retired_request(self, directory: Path, request_id: str) -> Instance | None:
        if not UUID.fullmatch(request_id):
            raise RegistryError("invalid_request_id")
        retired = self._retired_directory(directory, create=False)
        if retired is None:
            return None
        path = retired / (request_id + ".json")
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RegistryError("request_archive_corrupt") from error
        try:
            record = parse_instance(read_private_json(path), self.generation, directory.name)
        except RegistryError as error:
            raise RegistryError("request_archive_corrupt") from error
        if (
            path.stem != request_id
            or record.value["requestId"] != request_id
            or record.state not in {"exited", "failed"}
        ):
            raise RegistryError("request_archive_corrupt")
        return record

    def _read_instance_index(self, directory: Path, instance_id: str) -> str | None:
        if not UUID.fullmatch(instance_id):
            raise RegistryError("invalid_instance_id")
        retired = self._retired_directory(directory, create=False)
        if retired is None:
            return None
        path = retired / (instance_id + ".instance")
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RegistryError("instance_archive_corrupt") from error
        try:
            value = read_private_json(path, limit=4096)
        except RegistryError as error:
            raise RegistryError("instance_archive_corrupt") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "desktop", "targetId", "instanceId", "requestId"}
            or type(value.get("schemaVersion")) is not int
            or value["schemaVersion"] != 1
            or value.get("desktop") != self.generation
            or value.get("targetId") != directory.name
            or value.get("instanceId") != instance_id
            or not isinstance(value.get("requestId"), str)
            or not UUID.fullmatch(value["requestId"])
        ):
            raise RegistryError("instance_archive_corrupt")
        return value["requestId"]

    def _ensure_instance_index(self, directory: Path, record: Instance) -> None:
        retired = self._retired_directory(directory, create=True)
        assert retired is not None
        path = retired / (record.instance_id + ".instance")
        existing = self._read_instance_index(directory, record.instance_id)
        request_id = str(record.value["requestId"])
        if existing is not None:
            if existing != request_id:
                raise RegistryError("instance_archive_conflict")
            return
        atomic_json(
            path,
            {
                "schemaVersion": 1,
                "desktop": self.generation,
                "targetId": directory.name,
                "instanceId": record.instance_id,
                "requestId": request_id,
            },
        )

    def _archive_record(
        self, directory: Path, record: Instance, *, active_path: Path | None
    ) -> Instance:
        retired = self._retired_directory(directory, create=True)
        assert retired is not None
        request_id = str(record.value["requestId"])
        path = retired / (request_id + ".json")
        archived = self._read_retired_request(directory, request_id)
        if archived is None:
            atomic_json(path, record.value)
            archived = record
        elif archived.instance_id != record.instance_id:
            raise RegistryError("request_archive_conflict")
        self._ensure_instance_index(directory, archived)
        if active_path is not None:
            _unlink_durable(active_path)
        return archived

    def records(self, target_id: str) -> list[Instance]:
        with self.locked(target_id) as directory:
            return self._records(directory)

    def find_request(self, target_id: str, request_id: str) -> Instance | None:
        """Resolve one idempotency key without scanning the retired history."""

        if not UUID.fullmatch(request_id):
            raise RegistryError("invalid_request_id")
        with self.locked(target_id) as directory:
            archived = self._read_retired_request(directory, request_id)
            if archived is not None:
                return archived
            return next(
                (
                    record
                    for record in self._records(directory)
                    if record.value["requestId"] == request_id
                ),
                None,
            )

    def target_ids(self) -> list[str]:
        values = [path.name for path in self.root.iterdir() if HEX.fullmatch(path.name)]
        if len(values) > 1024:
            raise RegistryError("desktop_target_limit")
        return sorted(values)

    def reserve(
        self,
        *,
        target_id: str,
        target: dict[str, object],
        reference: dict[str, object],
        request_id: str,
        additional: bool,
        owner: ProcessIdentity,
        transport: dict[str, object],
    ) -> tuple[Instance, bool]:
        if not UUID.fullmatch(request_id):
            raise RegistryError("invalid_request_id")
        with self.locked(target_id) as directory:
            replay = self._read_retired_request(directory, request_id)
            if replay is not None:
                return replay, False
            records = self._records(directory)
            replay = next((r for r in records if r.value["requestId"] == request_id), None)
            if replay is not None:
                return replay, False
            active = [record for record in records if record.state in ACTIVE]
            if not additional and active:
                if len(active) > 1:
                    raise RegistryError("multiple_instances_refresh")
                return active[0], False
            if len(records) >= MAX_RECORDS:
                raise RegistryError("target_record_limit")
            ordinal = next(
                number
                for number in range(1, MAX_RECORDS + 1)
                if number not in {r.value["ordinal"] for r in active}
            )
            identifier = uuid.uuid4().hex
            value = {
                "schemaVersion": 1,
                "desktop": self.generation,
                "targetId": target_id,
                "instanceId": identifier,
                "requestId": request_id,
                "ordinal": ordinal,
                "marker": "org.agentplus." + identifier,
                "state": "reserved",
                "createdAt": time.time(),
                "target": target,
                "entryReference": reference,
                "owner": owner.wire(),
                "terminal": None,
                "wrapper": None,
                "client": None,
                "transport": transport,
            }
            record = parse_instance(value, self.generation, target_id)
            atomic_json(directory / (identifier + ".json"), value)
            return record, True

    def update(self, target_id: str, instance_id: str, **changes: object) -> Instance:
        if not UUID.fullmatch(instance_id):
            raise RegistryError("invalid_instance_id")
        allowed = {"state", "terminal", "wrapper", "client", "error", "windowId"}
        if not set(changes) <= allowed:
            raise RegistryError("invalid_instance_update")
        with self.locked(target_id) as directory:
            records = self._records(directory)
            current = next((r for r in records if r.instance_id == instance_id), None)
            if current is None:
                request_id = self._read_instance_index(directory, instance_id)
                if request_id is None:
                    raise RegistryError("instance_missing")
                current = self._read_retired_request(directory, request_id)
                if current is None or current.instance_id != instance_id:
                    raise RegistryError("instance_archive_corrupt")
                if "state" in changes:
                    changes = {key: value for key, value in changes.items() if key != "state"}
                value = {**current.value, **changes}
                record = parse_instance(value, self.generation, target_id)
                atomic_json(directory / "retired" / (request_id + ".json"), value)
                return record
            if current.archive_conflict:
                raise RegistryError("request_archive_corrupt")
            # A reconciler may have read the opening state before the viewer
            # retired it. Late acknowledgements can add process evidence but
            # must never resurrect a terminal request or change its outcome.
            if current.state not in ACTIVE and "state" in changes:
                changes = {key: value for key, value in changes.items() if key != "state"}
            value = {**current.value, **changes}
            record = parse_instance(value, self.generation, target_id)
            if record.state in {"exited", "failed"}:
                return self._archive_record(
                    directory,
                    record,
                    active_path=directory / (instance_id + ".json"),
                )
            atomic_json(directory / (instance_id + ".json"), value)
            return record

    def reconcile(
        self, target_id: str, desktop: Desktop, windows: tuple[Window, ...]
    ) -> list[Instance]:
        if desktop.generation != self.generation:
            raise RegistryError("desktop_generation_changed")
        result = []
        for record in self.records(target_id):
            if record.archive_conflict:
                result.append(record)
                continue
            if record.state not in ACTIVE:
                continue
            state = record.state
            client, wrapper, terminal, owner = (
                record.process(k) for k in ("client", "wrapper", "terminal", "owner")
            )
            try:
                if client is not None and not client.alive():
                    state = "exited"
                elif terminal is not None and not terminal.alive():
                    state = (
                        "exited"
                        if not any(w.app_id == record.marker for w in windows)
                        else "uncertain"
                    )
                elif terminal is not None:
                    window = desktop.match(windows, record.marker, terminal)
                    state = (
                        "live"
                        if window is not None
                        and client is not None
                        and wrapper is not None
                        and wrapper.alive()
                        else "opening"
                    )
                elif wrapper is not None and wrapper.alive():
                    # The viewer has acknowledged this launch, but the parent
                    # picker may exit before it can record Kitty's identity.
                    state = "opening"
                elif owner is not None and owner.alive():
                    state = "opening" if state != "reserved" else state
                elif state == "reserved":
                    state = "exited"
                else:
                    # No terminal acknowledgement after persisted dispatch is a
                    # nontransactional spawn gap. Absence of a mapped window is
                    # insufficient to retire this launch intent.
                    state = "uncertain"
            except DesktopError:
                state = "uncertain"
            if state != record.state:
                record = self.update(target_id, record.instance_id, state=state)
            if state in ACTIVE:
                result.append(record)
        return sorted(result, key=lambda record: record.value["ordinal"])
