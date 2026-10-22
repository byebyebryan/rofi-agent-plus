"""Private direct-v1 host catalog cache with partial, per-host publication."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .config import SUPPORTED_PROVIDERS
from .model import Inventory, RecordError, Target, UserScope, parse_target
from .transport import FleetSnapshot, HostResult, Transport

if TYPE_CHECKING:
    from collections.abc import Callable

SCHEMA_VERSION = 1
MAX_CACHE_BYTES = 8 << 20
MAX_HOSTS = 256
MAX_PROVIDER_ERRORS = 256
MAX_CACHE_AGE_SECONDS = 30


class CacheError(RuntimeError):
    """A malformed or inaccessible direct catalog cache."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("catalog contains a duplicate JSON field")
        result[key] = value
    return result


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise CacheError("catalog timestamp is invalid")
    return value


def _bounded_string(value: object, maximum: int, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise CacheError("catalog text field is invalid")
    return value


def _host_id(value: object) -> str:
    result = _bounded_string(value, 256)
    assert result is not None
    if (
        result.casefold() != result
        or not result[0].isalnum()
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in result)
    ):
        raise CacheError("catalog host identity is invalid")
    return result


def _diagnostic(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) - {"code", "stage", "message", "provider", "home"}:
        raise CacheError("catalog diagnostic is invalid")
    required = {"code", "stage", "message"}
    if not required <= set(value):
        raise CacheError("catalog diagnostic is incomplete")
    result: dict[str, str] = {}
    for key, maximum in (("code", 64), ("stage", 64), ("message", 4096)):
        item = _bounded_string(value[key], maximum)
        assert item is not None
        result[key] = item
    for key, maximum in (("provider", 16), ("home", 4096)):
        if key in value:
            item = _bounded_string(value[key], maximum)
            assert item is not None
            result[key] = item
    if "provider" in result and result["provider"] not in SUPPORTED_PROVIDERS:
        raise CacheError("catalog diagnostic provider is invalid")
    return result


def _bounded_hosts(hosts: list[HostCatalog]) -> list[HostCatalog]:
    current = [item for item in hosts if item.status != "removed"]
    removed = sorted(
        (item for item in hosts if item.status == "removed"),
        key=lambda item: (item.observed_at, item.host_id),
        reverse=True,
    )
    if len(current) > MAX_HOSTS:
        raise CacheError("current catalog host count exceeds its limit")
    keep_removed = removed[: max(0, MAX_HOSTS - len(current))]
    kept = current + keep_removed
    positions = {item.host_id: index for index, item in enumerate(hosts)}
    return sorted(kept, key=lambda item: positions.get(item.host_id, len(positions)))


def _user_json(value: UserScope | None) -> dict[str, object] | None:
    return value.to_json() if value is not None else None


def _parse_user(value: object) -> UserScope | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"uid", "account"}:
        raise CacheError("catalog user scope is invalid")
    uid, account = value.get("uid"), value.get("account")
    if type(uid) is not int or not 0 <= uid <= 2**32 - 1:
        raise CacheError("catalog user scope is invalid")
    account = _bounded_string(account, 256)
    assert account is not None
    return UserScope(uid, account)


@dataclass(frozen=True)
class ProviderCatalog:
    provider: str
    status: str
    observed_at: int
    last_complete_at: int | None
    reason: str | None
    targets: tuple[Target, ...]
    errors: tuple[dict[str, str], ...] = ()

    def freshness(self, now_ms: int, ttl_seconds: int = MAX_CACHE_AGE_SECONDS) -> str:
        if self.status in {"missing", "unsupported"}:
            reference = self.observed_at
        else:
            reference = self.last_complete_at
        if reference is None:
            return "unknown"
        return "fresh" if max(0, now_ms - reference) <= ttl_seconds * 1000 else "stale"

    def to_json(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "status": self.status,
            "observedAt": self.observed_at,
            "lastCompleteAt": self.last_complete_at,
            "reason": self.reason,
            "targets": [target.to_json() for target in self.targets],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class HostCatalog:
    host_id: str
    display: str
    local: bool
    status: str
    observed_at: int
    route: str | None
    user: UserScope | None
    providers: tuple[ProviderCatalog, ...]
    errors: tuple[dict[str, str], ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "hostId": self.host_id,
            "display": self.display,
            "local": self.local,
            "status": self.status,
            "observedAt": self.observed_at,
            "route": self.route,
            "user": _user_json(self.user),
            "providers": [provider.to_json() for provider in self.providers],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class CatalogSnapshot:
    observed_at: int
    mesh_revision: str | None
    hosts: tuple[HostCatalog, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "observedAt": self.observed_at,
            "meshRevision": self.mesh_revision,
            "hosts": [host.to_json() for host in self.hosts],
        }

    def is_stale(
        self, ttl_seconds: int = MAX_CACHE_AGE_SECONDS, *, now_ms: int | None = None
    ) -> bool:
        current = _now_ms() if now_ms is None else now_ms
        active_hosts = [host for host in self.hosts if host.status != "removed"]
        if not active_hosts:
            return True
        for host in active_hosts:
            if host.status in {"error", "partial"}:
                return True
            for provider in host.providers:
                if provider.status in {"unknown", "partial"}:
                    return True
                if provider.freshness(current, ttl_seconds) != "fresh":
                    return True
        return False


def parse_snapshot(value: object) -> CatalogSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "observedAt",
        "meshRevision",
        "hosts",
    }:
        raise CacheError("catalog schema is invalid")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION:
        raise CacheError("catalog version is unsupported")
    observed = _timestamp(value["observedAt"])
    revision = value["meshRevision"]
    if revision is not None and (
        not isinstance(revision, str)
        or len(revision) != 71
        or not revision.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in revision[7:])
    ):
        raise CacheError("catalog mesh revision is invalid")
    raw_hosts = value["hosts"]
    if not isinstance(raw_hosts, list) or len(raw_hosts) > MAX_HOSTS:
        raise CacheError("catalog host count is invalid")
    hosts: list[HostCatalog] = []
    seen_hosts: set[str] = set()
    local_count = 0
    for raw_host in raw_hosts:
        if not isinstance(raw_host, dict) or set(raw_host) != {
            "hostId",
            "display",
            "local",
            "status",
            "observedAt",
            "route",
            "user",
            "providers",
            "errors",
        }:
            raise CacheError("catalog host record is invalid")
        host_id = _host_id(raw_host["hostId"])
        if host_id in seen_hosts:
            raise CacheError("catalog contains duplicate hosts")
        seen_hosts.add(host_id)
        display = _bounded_string(raw_host["display"], 16384)
        assert display is not None
        local = raw_host["local"]
        if not isinstance(local, bool):
            raise CacheError("catalog host locality is invalid")
        local_count += int(local)
        status = raw_host["status"]
        if not isinstance(status, str) or status not in {"complete", "partial", "error", "removed"}:
            raise CacheError("catalog host status is invalid")
        route = _bounded_string(raw_host["route"], 4096, optional=True)
        user = _parse_user(raw_host["user"])
        raw_providers = raw_host["providers"]
        if not isinstance(raw_providers, list) or len(raw_providers) > len(SUPPORTED_PROVIDERS):
            raise CacheError("catalog provider count is invalid")
        providers: list[ProviderCatalog] = []
        seen_providers: set[str] = set()
        for raw_provider in raw_providers:
            if not isinstance(raw_provider, dict) or set(raw_provider) != {
                "provider",
                "status",
                "observedAt",
                "lastCompleteAt",
                "reason",
                "targets",
                "errors",
            }:
                raise CacheError("catalog provider record is invalid")
            provider_name = raw_provider["provider"]
            if (
                not isinstance(provider_name, str)
                or provider_name not in SUPPORTED_PROVIDERS
                or provider_name in seen_providers
            ):
                raise CacheError("catalog provider identity is invalid")
            seen_providers.add(provider_name)
            provider_status = raw_provider["status"]
            if not isinstance(provider_status, str) or provider_status not in {
                "complete",
                "partial",
                "missing",
                "unsupported",
                "unknown",
            }:
                raise CacheError("catalog provider status is invalid")
            provider_observed = _timestamp(raw_provider["observedAt"])
            last_complete = raw_provider["lastCompleteAt"]
            if last_complete is not None:
                last_complete = _timestamp(last_complete)
            reason = _bounded_string(raw_provider["reason"], 64, optional=True)
            raw_targets = raw_provider["targets"]
            if not isinstance(raw_targets, list) or len(raw_targets) > 128:
                raise CacheError("catalog target count is invalid")
            targets: list[Target] = []
            target_keys: set[str] = set()
            for raw_target in raw_targets:
                try:
                    target = parse_target(raw_target)
                except RecordError as error:
                    raise CacheError("catalog target record is invalid") from error
                key = target.key
                if key.host_id != host_id or key.provider != provider_name:
                    raise CacheError("catalog target scope is invalid")
                if user is not None and (key.uid != user.uid or key.account != user.account):
                    raise CacheError("catalog target user is invalid")
                if user is None:
                    raise CacheError("catalog targets require a known user scope")
                if key.identity in target_keys:
                    raise CacheError("catalog has duplicate targets")
                target_keys.add(key.identity)
                targets.append(target)
            raw_errors = raw_provider["errors"]
            if not isinstance(raw_errors, list) or len(raw_errors) > MAX_PROVIDER_ERRORS:
                raise CacheError("catalog provider diagnostics are invalid")
            providers.append(
                ProviderCatalog(
                    provider_name,
                    provider_status,
                    provider_observed,
                    last_complete,
                    reason,
                    tuple(targets),
                    tuple(_diagnostic(item) for item in raw_errors),
                )
            )
        raw_errors = raw_host["errors"]
        if not isinstance(raw_errors, list) or len(raw_errors) > MAX_PROVIDER_ERRORS:
            raise CacheError("catalog host diagnostics are invalid")
        hosts.append(
            HostCatalog(
                host_id,
                display,
                local,
                status,
                _timestamp(raw_host["observedAt"]),
                route,
                user,
                tuple(providers),
                tuple(_diagnostic(item) for item in raw_errors),
            )
        )
    if local_count > 1:
        raise CacheError("catalog contains multiple local hosts")
    if local_count == 1 and not hosts[0].local:
        raise CacheError("catalog local host must be first")
    return CatalogSnapshot(observed, revision, tuple(hosts))


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise CacheError("catalog directory is not private")
    except OSError as error:
        raise CacheError("catalog directory is unavailable") from error


@contextmanager
def _cache_lock(path: Path) -> Iterator[None]:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise CacheError("catalog lock is not private")
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield
    except OSError as error:
        raise CacheError("catalog lock is unavailable") from error


def _target_union(previous: tuple[Target, ...], current: tuple[Target, ...]) -> tuple[Target, ...]:
    by_identity = {item.key.identity: item for item in previous}
    by_identity.update((item.key.identity, item) for item in current)
    current_ids = {item.key.identity for item in current}
    ordered = sorted(
        by_identity.values(),
        key=lambda item: (
            item.key.identity not in current_ids,
            item.key.home,
            item.key.identity,
        ),
    )
    return tuple(ordered[:128])


def _old_provider(host: HostCatalog | None, provider: str) -> ProviderCatalog:
    if host is not None:
        for item in host.providers:
            if item.provider == provider:
                return item
    return ProviderCatalog(provider, "unknown", 0, None, None, ())


def _merge_host(previous: HostCatalog | None, result: HostResult) -> HostCatalog:
    host_id = _host_id(result.host_id)
    display = _bounded_string(result.display, 16384)
    assert display is not None
    if not isinstance(result.local, bool) or result.status not in {"complete", "partial", "error"}:
        raise CacheError("host result status or locality is invalid")
    old_providers = {item.provider: item for item in previous.providers} if previous else {}
    if result.inventory is None:
        errors = (result.error.to_json(),) if result.error is not None else ()
        return HostCatalog(
            host_id,
            display,
            result.local,
            "error",
            _timestamp(result.observed_at),
            result.route,
            previous.user if previous else None,
            tuple(_old_provider(previous, name) for name in SUPPORTED_PROVIDERS),
            errors,
        )

    inventory: Inventory = result.inventory
    if inventory.host_id != host_id:
        raise CacheError("inventory self identity does not match host result")
    if previous is not None and previous.user is not None and previous.user != inventory.user:
        old_providers = {}
    covered = {item.provider: item for item in inventory.provider_coverage}
    inventory_targets = {name: [] for name in SUPPORTED_PROVIDERS}
    for target in inventory.targets:
        if (
            target.key.host_id != host_id
            or target.key.uid != inventory.user.uid
            or target.key.account != inventory.user.account
        ):
            raise CacheError("inventory target identity is outside host scope")
        inventory_targets[target.key.provider].append(target)
    providers: list[ProviderCatalog] = []
    for name in SUPPORTED_PROVIDERS:
        old = old_providers.get(name, ProviderCatalog(name, "unknown", 0, None, None, ()))
        coverage = covered.get(name)
        if coverage is None:
            providers.append(
                ProviderCatalog(
                    name,
                    "unknown",
                    old.observed_at,
                    old.last_complete_at,
                    "provider_not_reported",
                    old.targets,
                    old.errors,
                )
            )
            continue
        matching_errors = tuple(
            error.to_json() for error in inventory.errors if error.provider == name
        )
        if coverage.status == "complete":
            targets = tuple(
                sorted(inventory_targets[name], key=lambda item: (item.key.home, item.key.identity))
            )
            complete_at = coverage.observed_at
        elif coverage.status in {"missing", "unsupported"}:
            targets = ()
            complete_at = old.last_complete_at
        else:
            targets = _target_union(old.targets, tuple(inventory_targets[name]))
            complete_at = old.last_complete_at
        providers.append(
            ProviderCatalog(
                name,
                coverage.status,
                coverage.observed_at,
                complete_at,
                coverage.reason,
                targets,
                matching_errors,
            )
        )
    host_errors = tuple(error.to_json() for error in inventory.errors if error.provider is None)
    return HostCatalog(
        host_id,
        display,
        result.local,
        result.status,
        _timestamp(result.observed_at),
        result.route,
        inventory.user,
        tuple(providers),
        host_errors,
    )


class CacheStore:
    """Bounded private cache under ``$XDG_CACHE_HOME/rofi-agent-plus/direct-v1``."""

    def __init__(
        self, root: str | Path | None = None, *, env: dict[str, str] | None = None
    ) -> None:
        if root is None:
            environment = os.environ if env is None else env
            configured = environment.get("XDG_CACHE_HOME", "")
            base = Path(configured) if configured else Path.home() / ".cache"
            if not base.is_absolute():
                raise CacheError("XDG_CACHE_HOME must be absolute")
            root = base / "rofi-agent-plus" / "direct-v1"
        self.root = Path(root)
        self.path = self.root / "catalog.json"
        self.lock_path = self.root / ".catalog.lock"

    def _read_unlocked(self) -> CatalogSnapshot | None:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or info.st_size > MAX_CACHE_BYTES
                ):
                    raise CacheError("catalog file is not a bounded private regular file")
                raw = stream.read(MAX_CACHE_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise CacheError("catalog file cannot be read") from error
        if len(raw) > MAX_CACHE_BYTES:
            raise CacheError("catalog file exceeds its size limit")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            return parse_snapshot(value)
        except (UnicodeError, ValueError, RecursionError) as error:
            raise CacheError("catalog file is malformed") from error

    def load(self) -> CatalogSnapshot | None:
        """Read the last published snapshot, rejecting unsafe or unknown versions."""

        if not self.root.exists():
            return None
        _private_directory(self.root)
        return self._read_unlocked()

    def _write_unlocked(self, snapshot: CatalogSnapshot) -> None:
        value = parse_snapshot(snapshot.to_json()).to_json()
        content = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_CACHE_BYTES:
            raise CacheError("catalog snapshot exceeds its size limit")
        descriptor, name = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=self.root)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _mutate(
        self, mutate: Callable[[CatalogSnapshot | None], CatalogSnapshot]
    ) -> CatalogSnapshot:
        _private_directory(self.root)
        with _cache_lock(self.lock_path):
            current = self._read_unlocked()
            updated = mutate(current)
            self._write_unlocked(updated)
            return updated

    def publish_host(self, result: HostResult, *, mesh_revision: str | None) -> CatalogSnapshot:
        """Merge a completed host while preserving other hosts and partial targets."""

        def merge(current: CatalogSnapshot | None) -> CatalogSnapshot:
            hosts = list(current.hosts if current is not None else ())
            found = next(
                (index for index, item in enumerate(hosts) if item.host_id == result.host_id), None
            )
            previous = hosts[found] if found is not None else None
            updated = _merge_host(previous, result)
            if found is None:
                # Host Mesh keeps the local machine first; a newly seen local
                # result must also lead a cache assembled from earlier remotes.
                hosts.insert(0 if result.local else len(hosts), updated)
            else:
                hosts[found] = updated
            hosts = _bounded_hosts(hosts)
            return CatalogSnapshot(_now_ms(), mesh_revision, tuple(hosts))

        return self._mutate(merge)

    def finish_fleet(self, fleet: FleetSnapshot) -> CatalogSnapshot:
        """Mark hosts absent from the completed current Mesh as removed."""

        present = {host.host_id for host in fleet.hosts}
        current_order = {host.host_id: index for index, host in enumerate(fleet.hosts)}

        def merge(current: CatalogSnapshot | None) -> CatalogSnapshot:
            hosts = list(current.hosts if current is not None else ())
            hosts = [
                HostCatalog(
                    item.host_id,
                    item.display,
                    item.local,
                    "removed",
                    fleet.observed_at,
                    None,
                    item.user,
                    item.providers,
                    item.errors,
                )
                if item.host_id not in present
                else item
                for item in hosts
            ]
            hosts.sort(
                key=lambda item: (
                    0 if item.host_id in current_order else 1,
                    current_order.get(item.host_id, len(current_order)),
                    item.host_id,
                )
            )
            hosts = _bounded_hosts(hosts)
            return CatalogSnapshot(fleet.observed_at, fleet.mesh_revision, tuple(hosts))

        return self._mutate(merge)

    def refresh(
        self,
        transport: Transport,
        *,
        on_host: Callable[[HostResult], None] | None = None,
    ) -> FleetSnapshot:
        """Publish every host completion, then commit the completed Mesh membership."""

        def publish(result: HostResult) -> None:
            self.publish_host(result, mesh_revision=transport.current_mesh_revision)
            if on_host is not None:
                on_host(result)

        fleet = transport.read_fleet(on_result=publish)
        self.finish_fleet(fleet)
        return fleet


def freshness(provider: ProviderCatalog, *, ttl_seconds: int = MAX_CACHE_AGE_SECONDS) -> str:
    return provider.freshness(_now_ms(), ttl_seconds)
