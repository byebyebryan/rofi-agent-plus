"""Versioned, bounded records for the direct TUI host boundary."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from .wire import WireError, decode_document, validate_string_bounds

SCHEMA_VERSION = 1
MAX_WIRE_BYTES = 1 << 20
MAX_REFERENCE_BYTES = 64 << 10
MAX_FIELD = 16 << 10
MAX_MESSAGE = 4096
MAX_TARGETS = 128
MAX_COVERAGE = 2
MAX_ERRORS = 256
SUPPORTED_PROVIDERS = ("codex", "claude")
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z", re.ASCII)
_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_REVISION = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


class RecordError(ValueError):
    """A malformed or unsupported direct host record."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RecordError(f"{label} must be an object")
    return value


def _required(value: Mapping[str, object], *keys: str) -> None:
    if any(key not in value for key in keys):
        raise RecordError("record omitted a required field")


def _fields(
    value: Mapping[str, object], required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> None:
    _required(value, *required)
    if set(value) - set(required) - set(optional):
        raise RecordError("record contains an unsupported field")


def _string(value: object, label: str, *, limit: int = MAX_FIELD) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise RecordError(f"{label} is invalid")
    return value


def _account(value: object) -> str:
    return _string(value, "account", limit=256)


def _host_id(value: object) -> str:
    result = _string(value, "host id", limit=256)
    if not _HOST_ID.fullmatch(result):
        raise RecordError("host id is invalid")
    return result.casefold()


def _uid(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**32 - 1:
        raise RecordError("uid is invalid")
    return value


def _timestamp(value: object, label: str = "timestamp") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise RecordError(f"{label} is invalid")
    return value


def _home(value: object) -> str:
    result = _string(value, "home", limit=4096)
    path = PurePosixPath(result)
    if not path.is_absolute() or str(path) != result or ".." in path.parts:
        raise RecordError("home is not a canonical absolute path")
    return result


def _provider(value: object) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_PROVIDERS:
        raise RecordError("provider is unsupported")
    return value


def _revision(value: object) -> str:
    result = _string(value, "specification revision", limit=71)
    if not _REVISION.fullmatch(result):
        raise RecordError("specification revision is invalid")
    return result


def _code(value: object) -> str:
    result = _string(value, "diagnostic code", limit=64)
    if not _CODE.fullmatch(result):
        raise RecordError("diagnostic code is invalid")
    return result


def _protocol(value: Mapping[str, object]) -> None:
    version = value.get("schemaVersion")
    if isinstance(version, bool) or version != SCHEMA_VERSION or not isinstance(version, int):
        raise RecordError("unsupported direct host schema version")


def _reject_unsafe_reference_fields(value: object) -> None:
    forbidden_fragments = (
        "argv",
        "command",
        "credential",
        "daemonpid",
        "display",
        "executable",
        "label",
        "pid",
        "password",
        "secret",
        "token",
    )
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(char for char in str(key).casefold() if char.isalnum())
            if any(fragment in normalized for fragment in forbidden_fragments):
                raise RecordError("entry reference contains a forbidden launch field")
            _reject_unsafe_reference_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_unsafe_reference_fields(child)


def canonical_json(value: object) -> bytes:
    """Encode structured identity input in a stable, UTF-8 form."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise RecordError("identity is not canonical JSON") from error


def canonical_revision(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class UserScope:
    uid: int
    account: str

    def to_json(self) -> dict[str, object]:
        return {"uid": self.uid, "account": self.account}


@dataclass(frozen=True)
class TargetKey:
    host_id: str
    uid: int
    account: str
    provider: str
    home: str

    def to_json(self) -> dict[str, object]:
        return {
            "hostId": self.host_id,
            "uid": self.uid,
            "account": self.account,
            "provider": self.provider,
            "home": self.home,
        }

    @property
    def wire(self) -> dict[str, object]:
        return self.to_json()

    @property
    def identity(self) -> str:
        return canonical_revision(self.to_json())


@dataclass(frozen=True)
class Observation:
    status: str
    observed_at: int
    reason: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"status": self.status, "observedAt": self.observed_at, "reason": self.reason}


@dataclass(frozen=True)
class LaunchCapability:
    status: str
    reason: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"status": self.status, "reason": self.reason}


@dataclass(frozen=True)
class Target:
    key: TargetKey
    label: str
    spec_revision: str
    capability: LaunchCapability
    daemon: Observation

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key.to_json(),
            "label": self.label,
            "specRevision": self.spec_revision,
            "capability": self.capability.to_json(),
            "daemon": self.daemon.to_json(),
        }

    @property
    def wire(self) -> dict[str, object]:
        return self.to_json()


@dataclass(frozen=True)
class ProviderCoverage:
    provider: str
    status: str
    observed_at: int
    reason: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "status": self.status,
            "observedAt": self.observed_at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class HostDiagnostic:
    code: str
    stage: str
    message: str
    provider: str | None = None
    home: str | None = None

    def to_json(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
        }
        if self.provider is not None:
            result["provider"] = self.provider
        if self.home is not None:
            result["home"] = self.home
        return result


@dataclass(frozen=True)
class Inventory:
    host_id: str
    user: UserScope
    observed_at: int
    provider_coverage: tuple[ProviderCoverage, ...]
    targets: tuple[Target, ...]
    errors: tuple[HostDiagnostic, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "self": {"hostId": self.host_id, "user": self.user.to_json()},
            "observedAt": self.observed_at,
            "providerCoverage": [item.to_json() for item in self.provider_coverage],
            "targets": [item.to_json() for item in self.targets],
            "errors": [item.to_json() for item in self.errors],
        }


@dataclass(frozen=True)
class EntryReference:
    expected_host_id: str
    expected_user: UserScope
    target_key: TargetKey
    spec_revision: str

    def to_json(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "expectedHostId": self.expected_host_id,
            "expectedUser": self.expected_user.to_json(),
            "targetKey": self.target_key.to_json(),
            "specRevision": self.spec_revision,
        }

    @property
    def wire(self) -> dict[str, object]:
        return self.to_json()


def parse_user_scope(value: object) -> UserScope:
    data = _mapping(value, "user scope")
    _fields(data, ("uid", "account"))
    return UserScope(_uid(data["uid"]), _account(data["account"]))


def parse_target_key(value: object) -> TargetKey:
    data = _mapping(value, "target key")
    _fields(data, ("hostId", "uid", "account", "provider", "home"))
    host_id = _host_id(data["hostId"])
    if data["hostId"] != host_id:
        raise RecordError("target host id is not canonical")
    return TargetKey(
        host_id,
        _uid(data["uid"]),
        _account(data["account"]),
        _provider(data["provider"]),
        _home(data["home"]),
    )


def _parse_observation(value: object, *, statuses: set[str], label: str) -> Observation:
    data = _mapping(value, label)
    _fields(data, ("status", "observedAt", "reason"))
    status = data["status"]
    if not isinstance(status, str) or status not in statuses:
        raise RecordError(f"{label} status is invalid")
    reason = data["reason"]
    if reason is not None:
        reason = _code(reason)
    return Observation(status, _timestamp(data["observedAt"]), reason)


def _parse_capability(value: object) -> LaunchCapability:
    data = _mapping(value, "capability")
    _fields(data, ("status", "reason"))
    status = data["status"]
    if not isinstance(status, str) or status not in {
        "supported",
        "unavailable",
        "missing",
        "unsupported",
        "unknown",
    }:
        raise RecordError("capability status is invalid")
    reason = data["reason"]
    if reason is not None:
        reason = _code(reason)
    return LaunchCapability(status, reason)


def _parse_diagnostic(value: object) -> HostDiagnostic:
    data = _mapping(value, "error")
    _required(data, "code", "stage", "message")
    provider = data.get("provider")
    if provider is not None:
        provider = _provider(provider)
    home = data.get("home")
    if home is not None:
        home = _home(home)
    return HostDiagnostic(
        _code(data["code"]),
        _code(data["stage"]),
        _string(data["message"], "diagnostic message", limit=MAX_MESSAGE),
        provider,
        home,
    )


def parse_target(value: object) -> Target:
    """Parse one strict target record independently of its inventory envelope."""

    item = _mapping(value, "target")
    _fields(item, ("key", "label", "specRevision", "capability", "daemon"))
    return Target(
        parse_target_key(item["key"]),
        _string(item["label"], "target label", limit=256),
        _revision(item["specRevision"]),
        _parse_capability(item["capability"]),
        _parse_observation(
            item["daemon"], statuses={"running", "stopped", "unknown"}, label="daemon"
        ),
    )


def parse_inventory(value: object) -> Inventory:
    data = _mapping(value, "inventory")
    _protocol(data)
    _fields(data, ("schemaVersion", "self", "observedAt", "providerCoverage", "targets", "errors"))
    self_data = _mapping(data["self"], "self identity")
    _fields(self_data, ("hostId", "user"))
    host_id = _host_id(self_data["hostId"])
    if self_data["hostId"] != host_id:
        raise RecordError("inventory host id is not canonical")
    user = parse_user_scope(self_data["user"])
    coverage_value = data["providerCoverage"]
    targets_value = data["targets"]
    errors_value = data["errors"]
    if (
        not isinstance(coverage_value, list)
        or len(coverage_value) > MAX_COVERAGE
        or not isinstance(targets_value, list)
        or len(targets_value) > MAX_TARGETS
        or not isinstance(errors_value, list)
        or len(errors_value) > MAX_ERRORS
    ):
        raise RecordError("inventory exceeds its record count limit")
    coverage: list[ProviderCoverage] = []
    seen_providers: set[str] = set()
    for value_item in coverage_value:
        item = _mapping(value_item, "provider coverage")
        _fields(item, ("provider", "status", "observedAt", "reason"))
        provider = _provider(item["provider"])
        if provider in seen_providers:
            raise RecordError("inventory contains duplicate provider coverage")
        seen_providers.add(provider)
        status = item["status"]
        if not isinstance(status, str) or status not in {
            "complete",
            "partial",
            "missing",
            "unsupported",
            "unknown",
        }:
            raise RecordError("provider coverage status is invalid")
        reason = item["reason"]
        if reason is not None:
            reason = _code(reason)
        coverage.append(ProviderCoverage(provider, status, _timestamp(item["observedAt"]), reason))
    targets: list[Target] = []
    seen_targets: set[str] = set()
    for value_item in targets_value:
        target = parse_target(value_item)
        key = target.key
        if key.host_id != host_id or key.uid != user.uid or key.account != user.account:
            raise RecordError("target identity does not match inventory self identity")
        if key.identity in seen_targets:
            raise RecordError("inventory contains duplicate target identities")
        seen_targets.add(key.identity)
        targets.append(target)
    errors = tuple(_parse_diagnostic(item) for item in errors_value)
    observed_at = _timestamp(data["observedAt"])
    try:
        validate_string_bounds(value, limit=MAX_FIELD)
    except WireError as error:
        raise RecordError("inventory contains an oversized string") from error
    return Inventory(host_id, user, observed_at, tuple(coverage), tuple(targets), errors)


def parse_entry_reference(value: object) -> EntryReference:
    data = _mapping(value, "entry reference")
    _protocol(data)
    try:
        validate_string_bounds(data, limit=MAX_FIELD)
    except WireError as error:
        raise RecordError("entry reference exceeds structural bounds") from error
    _reject_unsafe_reference_fields(data)
    _fields(data, ("schemaVersion", "expectedHostId", "expectedUser", "targetKey", "specRevision"))
    host_id = _host_id(data["expectedHostId"])
    if data["expectedHostId"] != host_id:
        raise RecordError("expected host id is not canonical")
    user = parse_user_scope(data["expectedUser"])
    key = parse_target_key(data["targetKey"])
    if key.host_id != host_id or key.uid != user.uid or key.account != user.account:
        raise RecordError("entry reference scope does not match its target key")
    try:
        validate_string_bounds(value, limit=MAX_FIELD)
    except WireError as error:
        raise RecordError("entry reference contains an oversized string") from error
    return EntryReference(host_id, user, key, _revision(data["specRevision"]))


def decode_reference_json(value: str) -> EntryReference:
    """Decode the compact JSON string accepted by `enter --reference-json`."""

    if not isinstance(value, str):
        raise RecordError("entry reference must be text")
    try:
        raw = value.encode("utf-8", "strict")
        decoded = decode_document(raw + b"\n", limit=MAX_REFERENCE_BYTES)
        return parse_entry_reference(decoded)
    except (UnicodeEncodeError, WireError, RecursionError, RecordError) as error:
        raise RecordError("entry reference JSON is invalid or oversized") from error


def encode_document(value: object) -> bytes:
    if hasattr(value, "to_json"):
        value = value.to_json()
    try:
        payload = canonical_json(value) + b"\n"
    except RecordError:
        raise
    if len(payload) > MAX_WIRE_BYTES:
        raise RecordError("record exceeds the host wire byte limit")
    return payload


def decode_document_json(raw: bytes) -> object:
    try:
        value = decode_document(raw, limit=MAX_WIRE_BYTES)
        validate_string_bounds(value, limit=MAX_FIELD)
        return value
    except (WireError, RecursionError) as error:
        raise RecordError(str(error)) from error
