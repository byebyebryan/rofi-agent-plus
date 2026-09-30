"""Private, best-effort storage for Agent Plus picker navigation hints."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote_to_bytes

from . import engine

PREFERENCE_VERSION = 1
PREFERENCE_DATA_PREFIX = "last-used:"
MAX_RECORD_BYTES = 64 * 1024
MAX_ID_LENGTH = 16 * 1024
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z", re.ASCII)
_PROVIDERS = frozenset({"codex", "claude", "opencode"})
SessionIdentity = tuple[str, str, str]


@dataclass(frozen=True)
class ViewPreference:
    """One saved page and the last successfully used conversation identity."""

    page_kind: str = "all"
    page_host_id: str | None = None
    last_used: SessionIdentity | None = None


def _valid_host_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 256 and _HOST_ID.fullmatch(value) is not None


def _valid_identity(value: object) -> SessionIdentity | None:
    if not isinstance(value, dict) or set(value) != {"hostId", "provider", "sessionId"}:
        return None
    host_id = value.get("hostId")
    provider = value.get("provider")
    session_id = value.get("sessionId")
    if (
        not _valid_host_id(host_id)
        or not isinstance(provider, str)
        or provider not in _PROVIDERS
        or not isinstance(session_id, str)
        or not session_id
        or len(session_id) > MAX_ID_LENGTH
    ):
        return None
    pattern = engine.OPENCODE_ID_PATTERN if provider == "opencode" else engine.UUID_PATTERN
    if pattern.fullmatch(session_id) is None:
        return None
    return host_id.casefold(), provider, session_id


def _identity_json(identity: SessionIdentity | None) -> dict[str, str] | None:
    if identity is None:
        return None
    if not isinstance(identity, tuple) or len(identity) != 3:
        return None
    host_id, provider, session_id = identity
    parsed = _valid_identity({"hostId": host_id, "provider": provider, "sessionId": session_id})
    if parsed is None:
        return None
    return {"hostId": host_id, "provider": provider, "sessionId": session_id}


def _preference_json(preference: ViewPreference) -> dict[str, object] | None:
    if not isinstance(preference, ViewPreference):
        return None
    page: dict[str, str] = {"kind": preference.page_kind}
    if preference.page_kind == "host":
        if not _valid_host_id(preference.page_host_id):
            return None
        page["hostId"] = preference.page_host_id
    elif (
        preference.page_kind not in {"active", "all", "local"}
        or preference.page_host_id is not None
    ):
        return None
    identity = _identity_json(preference.last_used)
    if preference.last_used is not None and identity is None:
        return None
    return {"version": PREFERENCE_VERSION, "page": page, "lastUsed": identity}


def _parse_preference(value: object) -> ViewPreference | None:
    if not isinstance(value, dict) or set(value) != {"version", "page", "lastUsed"}:
        return None
    version = value.get("version")
    page = value.get("page")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != PREFERENCE_VERSION
        or not isinstance(page, dict)
        or not isinstance(page.get("kind"), str)
    ):
        return None
    kind = page["kind"]
    if kind == "host":
        if set(page) != {"kind", "hostId"} or not _valid_host_id(page.get("hostId")):
            return None
        host_id = page["hostId"]
    elif kind in {"active", "all", "local"}:
        if set(page) != {"kind"}:
            return None
        host_id = None
    else:
        return None

    raw_identity = value.get("lastUsed")
    if raw_identity is None:
        identity = None
    else:
        identity = _valid_identity(raw_identity)
        if identity is None:
            return None
    return ViewPreference(kind, host_id, identity)


def encode_last_used(identity: SessionIdentity | None) -> str | None:
    """Encode a validated identity for private Rofi continuation data."""

    identity_json = _identity_json(identity)
    if identity is None or identity_json is None:
        return None
    payload = {"version": PREFERENCE_VERSION, "lastUsed": identity_json}
    encoded = quote(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), safe="")
    return PREFERENCE_DATA_PREFIX + encoded


def parse_last_used(value: object) -> SessionIdentity | None:
    """Parse the optional last-used identity carried between Rofi callbacks."""

    if not isinstance(value, str) or len(value) > MAX_RECORD_BYTES:
        return None
    components = [
        component[len(PREFERENCE_DATA_PREFIX) :]
        for component in value.split(";")
        if component.startswith(PREFERENCE_DATA_PREFIX)
    ]
    if len(components) != 1 or not components[0]:
        return None
    try:
        payload = json.loads(unquote_to_bytes(components[0]).decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        return None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "lastUsed"}
        or isinstance(payload.get("version"), bool)
        or not isinstance(payload.get("version"), int)
        or payload.get("version") != PREFERENCE_VERSION
    ):
        return None
    return _valid_identity(payload.get("lastUsed"))


class ViewPreferenceStore:
    """Load and atomically write ``$XDG_STATE_HOME/rofi-agent-plus/view.json``."""

    def __init__(self, state_home: str | Path | None = None) -> None:
        if state_home is None:
            configured = os.environ.get("XDG_STATE_HOME", "")
            candidate = Path(configured).expanduser() if configured else None
            if candidate is not None and candidate.is_absolute():
                state_home = candidate
            else:
                state_home = Path.home() / ".local" / "state"
        self.path = Path(state_home) / "rofi-agent-plus" / "view.json"

    def load(self) -> ViewPreference:
        """Return validated state, using defaults for absent or malformed files."""

        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(self.path, flags)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RECORD_BYTES:
                    return ViewPreference()
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    raw = stream.read(MAX_RECORD_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(raw) > MAX_RECORD_BYTES:
                return ViewPreference()
            parsed = _parse_preference(json.loads(raw.decode("utf-8")))
        except (OSError, UnicodeError, ValueError, RecursionError):
            return ViewPreference()
        return parsed if parsed is not None else ViewPreference()

    def save(self, preference: ViewPreference) -> bool:
        """Best-effort atomic replacement with private file and directory modes."""

        payload = _preference_json(preference)
        if payload is None:
            return False
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            return False
        temporary: str | None = None
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            descriptor, temporary = tempfile.mkstemp(prefix=".view-", dir=self.path.parent)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            try:
                directory_fd = os.open(
                    self.path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
            except OSError:
                return True
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
            return True
        except OSError:
            return False
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
