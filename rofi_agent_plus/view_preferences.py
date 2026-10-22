"""Private direct-v1 selection hints for stable picker redraws."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 4096
_TARGET_ID = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_INSTANCE_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("preference contains a duplicate JSON field")
        result[key] = value
    return result


@dataclass(frozen=True)
class ViewPreference:
    last_target_id: str | None = None
    last_instance_id: str | None = None


def _valid(value: object) -> ViewPreference | None:
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "lastTargetId",
        "lastInstanceId",
    }:
        return None
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION:
        return None
    target, instance = value["lastTargetId"], value["lastInstanceId"]
    if target is not None and (not isinstance(target, str) or not _TARGET_ID.fullmatch(target)):
        return None
    if instance is not None and (
        not isinstance(instance, str) or not _INSTANCE_ID.fullmatch(instance)
    ):
        return None
    if instance is not None and target is None:
        return None
    return ViewPreference(target, instance)


class ViewPreferenceStore:
    """Atomically persist the last successful target and optional instance."""

    def __init__(
        self, root: str | Path | None = None, *, env: dict[str, str] | None = None
    ) -> None:
        environment = os.environ if env is None else env
        if root is None:
            configured = environment.get("XDG_STATE_HOME", "")
            base = Path(configured) if configured else Path.home() / ".local" / "state"
            if not base.is_absolute():
                raise ValueError("XDG_STATE_HOME must be absolute")
            root = base / "rofi-agent-plus" / "direct-v1"
        self.root = Path(root)
        self.path = self.root / "view.json"

    def load(self) -> ViewPreference:
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or info.st_size > MAX_RECORD_BYTES
                ):
                    return ViewPreference()
                raw = stream.read(MAX_RECORD_BYTES + 1)
        except OSError:
            return ViewPreference()
        if len(raw) > MAX_RECORD_BYTES:
            return ViewPreference()
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeError, ValueError, RecursionError):
            return ViewPreference()
        return _valid(value) or ViewPreference()

    def save(self, preference: ViewPreference) -> bool:
        if not isinstance(preference, ViewPreference):
            return False
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "lastTargetId": preference.last_target_id,
            "lastInstanceId": preference.last_instance_id,
        }
        if _valid(payload) is None:
            return False
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            return False
        descriptor = None
        temporary: str | None = None
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return False
            descriptor, temporary = tempfile.mkstemp(prefix=".view-", dir=self.root)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return True
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
