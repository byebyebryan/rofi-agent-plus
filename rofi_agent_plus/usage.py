"""Private rolling-week picker usage, independent of inventory and launch authority."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from .registry import RegistryError, atomic_json, private_directory, read_private_json

WEEK_MS = 168 * 60 * 60 * 1000
MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 32768
MAX_EVENTS = 65536
SNAPSHOT_ENV = "ROFI_AGENT_PLUS_USAGE_SNAPSHOT"
SNAPSHOT_NAME = "usage-snapshot.json"
_TARGET = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_TOKEN = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)


def _read_json(path: Path) -> object:
    # Preserve absence separately from invalid/unsafe records; the shared
    # bounded reader deliberately wraps every open error in RegistryError.
    path.lstat()
    return read_private_json(path, MAX_BYTES)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _timestamp(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


@dataclass(frozen=True)
class Usage:
    count: int = 0
    last_used: int = 0


def _entries(value: object) -> dict[str, dict]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "entries"}
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or not isinstance(value["entries"], dict)
        or len(value["entries"]) > MAX_ENTRIES
    ):
        raise ValueError("invalid usage history")
    events = 0
    tokens: set[str] = set()
    for target, entry in value["entries"].items():
        if (
            not _TARGET.fullmatch(target)
            or not isinstance(entry, dict)
            or set(entry) != {"lastUsed", "events"}
            or not _timestamp(entry["lastUsed"])
            or not isinstance(entry["events"], list)
        ):
            raise ValueError("invalid usage entry")
        for event in entry["events"]:
            events += 1
            if (
                events > MAX_EVENTS
                or not isinstance(event, dict)
                or set(event) != {"id", "at", "issuedAt"}
                or not isinstance(event["id"], str)
                or not _TOKEN.fullmatch(event["id"])
                or event["id"] in tokens
                or not _timestamp(event["at"])
                or not _timestamp(event["issuedAt"])
            ):
                raise ValueError("invalid usage event")
            tokens.add(event["id"])
    return value["entries"]


class UsageStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.path = self.root / "usage.json"

    def _read(self) -> dict[str, dict]:
        try:
            return _entries(_read_json(self.path))
        except FileNotFoundError:
            return {}

    def statistics(self, *, at: int | None = None) -> dict[str, Usage]:
        moment = now_ms() if at is None else at
        try:
            entries = self._read()
        except (RegistryError, OSError, ValueError, RecursionError):
            return {}
        return {
            target: Usage(
                sum(moment - WEEK_MS < event["at"] <= moment for event in entry["events"]),
                entry["lastUsed"] if entry["lastUsed"] <= moment else 0,
            )
            for target, entry in entries.items()
        }

    def record(self, target: str, token: str, issued_at: int, *, at: int | None = None) -> bool:
        moment = now_ms() if at is None else at
        if (
            not isinstance(target, str)
            or not _TARGET.fullmatch(target)
            or not isinstance(token, str)
            or not _TOKEN.fullmatch(token)
            or not _timestamp(moment)
            or not _timestamp(issued_at)
            or not moment - WEEK_MS < issued_at <= moment
        ):
            return False
        descriptor = None
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            private_directory(self.root)
            descriptor = os.open(
                self.root / ".usage.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return False
            deadline = time.monotonic() + 1
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(0.005)
            entries = self._read()
            # Keep receipts while their action token can still be counted. An
            # expired token is refused above, so an old replay cannot become new.
            for entry in entries.values():
                entry["events"] = [
                    event for event in entry["events"] if event["at"] > moment - WEEK_MS
                ]
                if any(event["id"] == token for event in entry["events"]):
                    return False
            entry = entries.setdefault(target, {"lastUsed": 0, "events": []})
            entry["events"].append({"id": token, "at": moment, "issuedAt": issued_at})
            entry["lastUsed"] = max(entry["lastUsed"], moment)
            value = {"schemaVersion": 1, "entries": entries}
            _entries(value)
            # Bounds are checked before the atomic replace; full/invalid state
            # stays intact and bookkeeping never blocks a successful action.
            if len(json.dumps(value).encode("utf-8")) > MAX_BYTES:
                return False
            atomic_json(self.path, value)
            return True
        except (RegistryError, OSError, ValueError, RecursionError):
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)


def frozen_statistics(store: UsageStore, env: dict[str, str]) -> dict[str, Usage]:
    """Use one immutable metric snapshot for all callbacks in a launched dialog."""

    configured = env.get(SNAPSHOT_ENV)
    if not configured:
        return store.statistics()
    path = Path(configured)
    try:
        parent = path.parent.lstat()
        if (
            not path.is_absolute()
            or path.name != SNAPSHOT_NAME
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            return {}
    except OSError:
        return {}
    try:
        value = _read_json(path)
    except FileNotFoundError:
        statistics = store.statistics()
        value = {
            "schemaVersion": 1,
            "entries": {
                target: {"count": usage.count, "lastUsed": usage.last_used}
                for target, usage in statistics.items()
            },
        }
        try:
            private_directory(path.parent)
            atomic_json(path, value)
            return statistics
        except (RegistryError, OSError, ValueError):
            return {}
    except (RegistryError, OSError, ValueError, RecursionError):
        return {}
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "entries"}
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or not isinstance(value["entries"], dict)
        or len(value["entries"]) > MAX_ENTRIES
    ):
        return {}
    result = {}
    for target, usage in value["entries"].items():
        if (
            not _TARGET.fullmatch(target)
            or not isinstance(usage, dict)
            or set(usage) != {"count", "lastUsed"}
            or type(usage["count"]) is not int
            or not 0 <= usage["count"] <= MAX_EVENTS
            or not _timestamp(usage["lastUsed"])
        ):
            return {}
        result[target] = Usage(usage["count"], usage["lastUsed"])
    return result
