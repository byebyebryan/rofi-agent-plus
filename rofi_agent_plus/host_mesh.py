"""The public SSH Plus Host Mesh parser and local self-identity resolver."""

from __future__ import annotations

import re
import socket
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .wire import WireError, decode_document, validate_string_bounds

MAX_STDOUT = 512 << 10
MAX_STDERR = 64 << 10
MAX_FIELD = 16 << 10
MAX_HOSTS = 128
MESH_COMMAND = "rofi-ssh-plus"
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z", re.ASCII)
_REVISION = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


class HostMeshError(ValueError):
    """The configured Host Mesh authority could not provide valid identity."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProcessRunner(Protocol):
    def __call__(self, argv: tuple[str, ...], **kwargs: object) -> object: ...


@dataclass(frozen=True)
class Route:
    destination: str
    configured_index: int
    last_reachable_at: int | None = None
    last_unreachable_at: int | None = None


@dataclass(frozen=True)
class MeshHost:
    host_id: str
    display: str
    local: bool
    aliases: tuple[str, ...]
    routes: tuple[Route, ...]


@dataclass(frozen=True)
class Mesh:
    revision: str | None
    executable: str
    connect_timeout: int
    attempts: int
    hosts: tuple[MeshHost, ...]

    @property
    def local(self) -> MeshHost:
        return next(host for host in self.hosts if host.local)


def _text(value: object, label: str, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not empty and not value)
        or len(value) > MAX_FIELD
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise HostMeshError("invalid_mesh", f"Host Mesh {label} is invalid")
    return value


def _host_id(value: object, label: str = "host id") -> str:
    result = _text(value, label)
    if len(result) > 256 or result.startswith("-") or not _HOST_ID.fullmatch(result):
        raise HostMeshError("invalid_mesh", f"Host Mesh {label} is invalid")
    return result.casefold()


def _token(value: object, label: str) -> str:
    result = _text(value, label)
    if result.startswith("-") or result.strip() != result or any(char.isspace() for char in result):
        raise HostMeshError("invalid_mesh", f"Host Mesh {label} is invalid")
    return result


def _display(value: object, label: str = "host display") -> str:
    result = _text(value, label)
    if result.strip() != result:
        raise HostMeshError("invalid_mesh", f"Host Mesh {label} is invalid")
    return result


def _required(value: Mapping[str, object], *names: str) -> None:
    if any(name not in value for name in names):
        raise HostMeshError("invalid_mesh", "Host Mesh omitted a required field")


def _timestamp(value: object, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise HostMeshError("invalid_mesh", f"Host Mesh {label} is invalid")
    return value


def parse_mesh(payload: object) -> Mesh:
    """Parse the published Host Mesh v1 list envelope without SSH Plus imports."""

    if not isinstance(payload, Mapping):
        raise HostMeshError("invalid_mesh", "Host Mesh response is not an object")
    version = payload.get("schemaVersion")
    if isinstance(version, bool) or version != 1 or not isinstance(version, int):
        raise HostMeshError("unsupported_mesh_schema", "Host Mesh schema is unsupported")
    _required(payload, "generatedAt", "meshRevision", "localHostId", "sshPolicy", "hosts")
    _timestamp(payload["generatedAt"], "generatedAt")
    revision = _text(
        payload["meshRevision"],
        "revision",
    )
    if not _REVISION.fullmatch(revision):
        raise HostMeshError("invalid_mesh", "Host Mesh revision is invalid")
    local_id = _host_id(payload["localHostId"], "local host id")
    policy = payload["sshPolicy"]
    hosts_value = payload["hosts"]
    if not isinstance(policy, Mapping) or not isinstance(hosts_value, list):
        raise HostMeshError("invalid_mesh", "Host Mesh response is malformed")
    _required(
        policy,
        "executable",
        "connectTimeoutSeconds",
        "connectionAttempts",
        "routeHealthTtlSeconds",
    )
    executable = _token(policy["executable"], "SSH executable")
    timeout = policy["connectTimeoutSeconds"]
    attempts = policy["connectionAttempts"]
    route_ttl = policy["routeHealthTtlSeconds"]
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= 60
        or isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 1 <= attempts <= 10
        or isinstance(route_ttl, bool)
        or not isinstance(route_ttl, int)
        or not 1 <= route_ttl <= 86400
        or not 1 <= len(hosts_value) <= MAX_HOSTS
    ):
        raise HostMeshError("invalid_mesh", "Host Mesh SSH policy is invalid")

    owners: dict[str, str] = {}
    seen_ids: set[str] = set()
    for value in hosts_value:
        if not isinstance(value, Mapping):
            raise HostMeshError("invalid_mesh", "Host Mesh host is invalid")
        _required(value, "id", "display", "local", "aliases", "routes")
        host_id = _host_id(value["id"])
        if host_id in seen_ids:
            raise HostMeshError("ambiguous_mesh", "Host Mesh identities are ambiguous")
        seen_ids.add(host_id)
        owners[host_id] = host_id

    hosts: list[MeshHost] = []
    for value in hosts_value:
        assert isinstance(value, Mapping)
        host_id = _host_id(value["id"])
        display = _display(value["display"])
        local = value["local"]
        aliases_value = value["aliases"]
        routes_value = value["routes"]
        if (
            not isinstance(local, bool)
            or not isinstance(aliases_value, list)
            or not isinstance(routes_value, list)
        ):
            raise HostMeshError("invalid_mesh", "Host Mesh host is invalid")
        aliases: list[str] = []
        for alias in aliases_value:
            token = _token(alias, "host alias")
            key = token.casefold()
            owner = owners.setdefault(key, host_id)
            if owner != host_id:
                raise HostMeshError("ambiguous_mesh", "Host Mesh identities are ambiguous")
            aliases.append(token)
        routes: list[Route] = []
        route_indices: set[int] = set()
        for route_value in routes_value:
            if not isinstance(route_value, Mapping):
                raise HostMeshError("invalid_mesh", "Host Mesh route is invalid")
            _required(
                route_value,
                "destination",
                "configuredIndex",
                "lastReachableAt",
                "lastUnreachableAt",
            )
            destination = _token(route_value["destination"], "route")
            index = route_value["configuredIndex"]
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                or index in route_indices
            ):
                raise HostMeshError("invalid_mesh", "Host Mesh route is invalid")
            route_indices.add(index)
            owner = owners.setdefault(destination.casefold(), host_id)
            if owner != host_id:
                raise HostMeshError("ambiguous_mesh", "Host Mesh identities are ambiguous")
            routes.append(
                Route(
                    destination,
                    index,
                    _timestamp(route_value["lastReachableAt"], "route timestamp", nullable=True),
                    _timestamp(route_value["lastUnreachableAt"], "route timestamp", nullable=True),
                )
            )
        if route_indices and route_indices != set(range(len(route_indices))):
            raise HostMeshError("invalid_mesh", "Host Mesh route order is invalid")
        if (local and routes) or (not local and not routes):
            raise HostMeshError("invalid_mesh", "Host Mesh local/remote routes are invalid")
        hosts.append(MeshHost(host_id, display, local, tuple(aliases), tuple(routes)))
    if sum(item.local for item in hosts) != 1 or hosts[0].host_id != local_id or not hosts[0].local:
        raise HostMeshError("invalid_mesh", "Host Mesh must declare the local host first")
    try:
        validate_string_bounds(payload, limit=MAX_FIELD)
    except WireError as error:
        raise HostMeshError(
            "invalid_mesh", "Host Mesh response contains an oversized string"
        ) from error
    return Mesh(revision, executable, timeout, attempts, tuple(hosts))


def parse_mesh_document(raw: bytes) -> Mesh:
    try:
        return parse_mesh(decode_document(raw, limit=MAX_STDOUT))
    except WireError as error:
        raise HostMeshError("invalid_mesh", "Host Mesh returned malformed JSON") from error


def local_only_mesh() -> Mesh:
    """Mirror SSH Plus's documented local-only identity if its CLI is absent."""

    short_source = socket.gethostname()
    raw_short = short_source.split(".", 1)[0] or "localhost"
    raw_full = socket.getfqdn() or raw_short
    host_id = raw_short.casefold() if _HOST_ID.fullmatch(raw_short) else "localhost"
    aliases = tuple(
        dict.fromkeys(
            candidate
            for candidate in (raw_full, raw_short)
            if candidate
            and candidate.strip() == candidate
            and not candidate.startswith("-")
            and not any(
                char.isspace() or unicodedata.category(char).startswith("C") for char in candidate
            )
        )
    )
    display = (
        raw_short[:255]
        if raw_short
        and raw_short.strip() == raw_short
        and not any(unicodedata.category(char).startswith("C") for char in raw_short)
        else host_id
    )
    return Mesh(
        None,
        "",
        1,
        1,
        (MeshHost(host_id, display, True, aliases or (host_id,), ()),),
    )


def resolve_self_mesh(
    executable: str | None = None,
    *,
    runner: ProcessRunner,
    env: Mapping[str, str] | None = None,
) -> Mesh:
    """Resolve the already-selected SSH Plus command, falling back only if absent."""

    if executable is None:
        return local_only_mesh()
    try:
        result = runner(
            (executable, "mesh", "list", "--json"),
            timeout=3.0,
            stdout_limit=MAX_STDOUT,
            stderr_limit=MAX_STDERR,
            env=None if env is None else dict(env),
        )
    except Exception as error:
        raise HostMeshError("mesh_unavailable", "configured Host Mesh command failed") from error
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    if returncode != 0:
        raise HostMeshError("mesh_unavailable", "configured Host Mesh command failed")
    if not isinstance(stdout, bytes):
        raise HostMeshError("mesh_unavailable", "configured Host Mesh returned no bounded bytes")
    return parse_mesh_document(stdout)
