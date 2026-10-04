"""Host Mesh/Tmux routing plus Agent Observer session discovery backend.

Provider facts arrive through one host-scoped Agent Observer JSON command.
Tmux Plus remains a separate inventory; this module does not correlate its
provider options or process records with Observer sessions. Tmux Plus is
required; when SSH Plus is absent it synthesizes the documented local-only
identity rather than reviving an Agent-owned SSH or tmux path.
"""

from __future__ import annotations

import errno
import os
import re
import secrets
import selectors
import shlex
import shutil
import signal
import socket
import subprocess
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any

from . import engine
from .observer_contract import ObserverContractError, ObserverSnapshot, parse_snapshot
from .observer_write import (
    MAX_WRITE_BYTES,
    WriteBoundaryError,
    completed,
    encoded,
    prepared,
    rejection_code,
)
from .wire import WireError, decode_document, validate_string_bounds

_MAX_STDOUT = 1 << 20
_MAX_STDERR = 1 << 16
_MAX_HOST_STDOUT = 8 * 1024 * 1024
_MAX_INVENTORY_STDOUT = 1 << 20
_MAX_FIELD = 16 * 1024
_MAX_HOSTS = 128
_DEFAULT_PROVIDERS = ("codex", "claude")
_PROVIDER_STAGE = {"codex": "threads", "claude": "claude"}
_HOST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", re.ASCII)
_SESSION_ID = re.compile(r"^\$[0-9]+$", re.ASCII)
_PANE_ID = re.compile(r"^%[0-9]+$", re.ASCII)
_REVISION = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$", re.ASCII)
_MARKER_PREFIX = "\x1eROFI_PLUS_REACHED_V1:"
_MARKER_SUFFIX = "\x1f\n"
_TRANSPORT_TEXT = (
    "could not resolve hostname",
    "name or service not known",
    "temporary failure in name resolution",
    "connection refused",
    "connection timed out",
    "network is unreachable",
    "no route to host",
    "connection reset",
    "operation timed out",
    "kex_exchange_identification",
)
_OPTIONS = (
    "@codex_thread_id",
    "@codex_name",
    "@claude_session_id",
    "@claude_name",
    "@agent_picker_waiting",
)
_MAX_HOST_WORKERS = 8
_WHOLE_REFRESH_MIN_SECONDS = 12.0
_WHOLE_REFRESH_MAX_SECONDS = 30.0


class ContractError(engine.PickerError):
    """Visible public-contract failure; never a fallback trigger."""


class WriteOperationError(ContractError):
    def __init__(self, message: str, *, effect: str, result: dict[str, object] | None = None):
        super().__init__(message)
        self.effect = effect
        self.result = result


class StaleMeshError(ContractError):
    """A structured producer stale-mesh envelope, never a text heuristic."""


@dataclass(frozen=True)
class CommandOutput:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    # The text attributes preserve the pre-contract provider/probe API.  JSON
    # contract consumers use these exact bytes so a test double cannot hide
    # missing final LF, replacement decoding, or other wire damage.
    stdout_bytes: bytes | None = None
    stderr_bytes: bytes | None = None


@dataclass
class _ReportFlight:
    """One route-health update currently owned by a domain command."""

    completed: Event
    stale: StaleMeshError | None = None


def _bounded_text(value: object, limit: int = _MAX_FIELD) -> str:
    text = str(value)
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in text)
    return text[:limit]


def _run_bounded(
    argv: Sequence[str],
    *,
    input_data: bytes | None = None,
    cwd: str | None = None,
    timeout: float,
    stdout_limit: int = _MAX_STDOUT,
    stderr_limit: int = _MAX_STDERR,
    own_process_group: bool = True,
) -> CommandOutput:
    """Run a process without unbounded ``communicate`` capture.

    Output is drained incrementally.  Any timeout or cap breach terminates and
    reaps the exact child before a visible error is raised.
    """

    poller: selectors.BaseSelector | None = None
    process: subprocess.Popen[bytes] | None = None

    def terminate(*, force_group: bool = False) -> None:
        if process is None or (process.poll() is not None and not force_group):
            return
        try:
            # Read/transport commands own their process group. A public write
            # client owns only its launcher; provider descendants survive.
            if own_process_group:
                os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                process.kill()
        except (ProcessLookupError, PermissionError):
            if process.poll() is None:
                process.kill()
        process.wait()

    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as error:
        raise ContractError(f"cannot run contract command: {_bounded_text(error)}") from error
    try:
        assert process.stdout is not None and process.stderr is not None
        poller = selectors.DefaultSelector()
        poller.register(process.stdout, selectors.EVENT_READ, "stdout")
        poller.register(process.stderr, selectors.EVENT_READ, "stderr")
        pending_input = memoryview(input_data or b"")
        if pending_input:
            assert process.stdin is not None
            os.set_blocking(process.stdin.fileno(), False)
            poller.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        elif process.stdin is not None:
            process.stdin.close()
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        while poller.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            for key, _events in poller.select(remaining):
                if key.data == "stdin":
                    try:
                        written = os.write(key.fileobj.fileno(), pending_input)
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        written = 0
                    except OSError as error:
                        if error.errno != errno.EPIPE:
                            raise
                        written = 0
                    if written == 0:
                        poller.unregister(key.fileobj)
                        key.fileobj.close()
                        pending_input = memoryview(b"")
                        continue
                    pending_input = pending_input[written:]
                    if not pending_input:
                        poller.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    poller.unregister(key.fileobj)
                    continue
                buffers[key.data].extend(chunk)
                cap = stdout_limit if key.data == "stdout" else stderr_limit
                if len(buffers[key.data]) > cap:
                    raise BufferError(key.data)
        try:
            returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise TimeoutError from error
        try:
            stdout = buffers["stdout"].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContractError("contract command returned invalid UTF-8 stdout") from error
        return CommandOutput(
            tuple(str(value) for value in argv),
            returncode,
            stdout,
            buffers["stderr"].decode("utf-8", "replace"),
            False,
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
        )
    except (TimeoutError, BufferError) as error:
        terminate(force_group=True)
        if isinstance(error, BufferError):
            raise ContractError(f"contract command exceeded {error.args[0]} limit") from error
        raise ContractError("contract command timed out") from error
    finally:
        terminate()
        if poller is not None:
            poller.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


Runner = Callable[..., CommandOutput]


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
        or len(value) > _MAX_FIELD
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ContractError(f"Host Mesh {label} is invalid")
    return value


def _display(value: object, label: str = "host display") -> str:
    result = _text(value, label)
    if result.strip() != result:
        raise ContractError(f"Host Mesh {label} is invalid")
    return result


def _host_id(value: object, label: str = "host id") -> str:
    result = _text(value, label)
    if result.startswith("-") or not _HOST_ID.fullmatch(result):
        raise ContractError(f"Host Mesh {label} is invalid")
    return result.casefold()


def _mesh_token(value: object, label: str) -> str:
    """Match Host Mesh's non-identifier token rule for aliases/routes."""

    result = _text(value, label)
    if result.startswith("-") or result.strip() != result or any(char.isspace() for char in result):
        raise ContractError(f"Host Mesh {label} is invalid")
    return result


def _revision(value: object, label: str = "revision") -> str:
    result = _text(value, label)
    if not _REVISION.fullmatch(result):
        raise ContractError(f"Host Mesh {label} is invalid")
    return result


def _error_code(value: object, label: str = "error code") -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or not _ERROR_CODE.fullmatch(value)
    ):
        raise ContractError(f"Host Mesh {label} is invalid")
    return value


def _error_host_id(value: object) -> str:
    """Validate the optional Tmux error host identity at its smaller bound."""

    if not isinstance(value, str) or len(value) > 4096:
        raise ContractError("Tmux Session error host id is invalid")
    return _host_id(value, "Tmux Session error host id")


def _required(mapping: Mapping[str, object], *names: str) -> None:
    if any(name not in mapping for name in names):
        raise ContractError("Host Mesh response omitted a required field")


def _timestamp(value: object, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise ContractError(f"{label} is invalid")
    return value


def parse_mesh(payload: object) -> Mesh:
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != 1
        or isinstance(payload.get("schemaVersion"), bool)
        or not isinstance(payload.get("schemaVersion"), int)
    ):
        raise ContractError("Host Mesh returned an unsupported schema")
    _required(payload, "generatedAt", "meshRevision", "localHostId", "sshPolicy", "hosts")
    _timestamp(payload["generatedAt"], "Host Mesh generatedAt")
    revision = _revision(payload["meshRevision"])
    local_id = _host_id(payload["localHostId"], "local host id")
    policy = payload["sshPolicy"]
    hosts_value = payload["hosts"]
    if not isinstance(policy, Mapping) or not isinstance(hosts_value, list):
        raise ContractError("Host Mesh returned malformed inventory")
    _required(
        policy,
        "executable",
        "connectTimeoutSeconds",
        "connectionAttempts",
        "routeHealthTtlSeconds",
    )
    executable = _mesh_token(policy["executable"], "SSH executable")
    timeout = policy["connectTimeoutSeconds"]
    attempts = policy["connectionAttempts"]
    route_health_ttl = policy["routeHealthTtlSeconds"]
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= 60
        or isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 1 <= attempts <= 10
        or isinstance(route_health_ttl, bool)
        or not isinstance(route_health_ttl, int)
        or not 1 <= route_health_ttl <= 86400
        or not 1 <= len(hosts_value) <= _MAX_HOSTS
    ):
        raise ContractError("Host Mesh SSH policy is invalid")

    # Reserve all logical host IDs up front.  This makes an alias or route
    # belonging to an earlier host collide with an ID declared later too.
    owners: dict[str, str] = {}
    seen_host_ids: set[str] = set()
    for item in hosts_value:
        if not isinstance(item, Mapping):
            raise ContractError("Host Mesh host is invalid")
        _required(item, "id", "display", "local", "aliases", "routes")
        host_id = _host_id(item["id"])
        if host_id in seen_host_ids:
            raise ContractError("Host Mesh identities are ambiguous")
        seen_host_ids.add(host_id)
        owners[host_id] = host_id

    hosts: list[MeshHost] = []
    for item in hosts_value:
        assert isinstance(item, Mapping)
        host_id = _host_id(item["id"])
        display = _display(item["display"])
        local = item["local"]
        aliases = item["aliases"]
        routes_value = item["routes"]
        if (
            not isinstance(local, bool)
            or not isinstance(aliases, list)
            or not isinstance(routes_value, list)
        ):
            raise ContractError("Host Mesh host is invalid")
        for alias in aliases:
            key = _mesh_token(alias, "host alias").casefold()
            owner = owners.setdefault(key, host_id)
            if owner != host_id:
                raise ContractError("Host Mesh identities are ambiguous")
        routes: list[Route] = []
        route_indices: set[int] = set()
        for route_value in routes_value:
            if not isinstance(route_value, Mapping):
                raise ContractError("Host Mesh route is invalid")
            _required(
                route_value,
                "destination",
                "configuredIndex",
                "lastReachableAt",
                "lastUnreachableAt",
            )
            destination = _mesh_token(route_value["destination"], "route")
            index = route_value["configuredIndex"]
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                or index in route_indices
            ):
                raise ContractError("Host Mesh route is invalid")
            route_indices.add(index)
            key = destination.casefold()
            owner = owners.setdefault(key, host_id)
            if owner != host_id:
                raise ContractError("Host Mesh identities are ambiguous")
            routes.append(
                Route(
                    destination,
                    index,
                    _timestamp(
                        route_value["lastReachableAt"],
                        "Host Mesh route timestamp",
                        nullable=True,
                    ),
                    _timestamp(
                        route_value["lastUnreachableAt"],
                        "Host Mesh route timestamp",
                        nullable=True,
                    ),
                )
            )
        if route_indices and route_indices != set(range(len(route_indices))):
            raise ContractError("Host Mesh route order is invalid")
        if local and routes:
            raise ContractError("Host Mesh local host has routes")
        if not local and not routes:
            raise ContractError("Host Mesh remote host has no routes")
        hosts.append(MeshHost(host_id, display, local, tuple(aliases), tuple(routes)))
    if sum(host.local for host in hosts) != 1 or hosts[0].host_id != local_id or not hosts[0].local:
        raise ContractError("Host Mesh must declare its local host first")
    try:
        validate_string_bounds(payload, limit=_MAX_FIELD)
    except WireError as error:
        raise ContractError("Host Mesh response contains an oversized string") from error
    return Mesh(revision, executable, timeout, attempts, tuple(hosts))


def _local_mesh(hostname: str | None = None) -> Mesh:
    """Mirror the suite's local-only identity without importing Tmux Plus."""

    short_source = hostname if hostname is not None else socket.gethostname()
    raw_short = short_source.split(".", 1)[0] or "localhost"
    raw_full = hostname if hostname is not None else socket.getfqdn() or raw_short
    host_id = raw_short.casefold() if _HOST_ID.fullmatch(raw_short) else "localhost"
    aliases = tuple(
        dict.fromkeys(
            value
            for value in (raw_full, raw_short)
            if (
                value
                and value.strip() == value
                and not value.startswith("-")
                and not any(
                    char.isspace() or unicodedata.category(char).startswith("C") for char in value
                )
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


def _command_bytes(command: CommandOutput, stream: str) -> bytes:
    raw = getattr(command, f"{stream}_bytes", None)
    if raw is not None:
        if not isinstance(raw, bytes):
            raise WireError(f"{stream} is not bytes")
        return raw
    value = getattr(command, stream)
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        try:
            return value.encode("utf-8", "strict")
        except UnicodeEncodeError as error:
            raise WireError(f"{stream} is not UTF-8") from error
    raise WireError(f"{stream} is not bytes")


def _json(
    command: CommandOutput,
    source: str,
    *,
    limit: int = _MAX_STDOUT,
) -> Mapping[str, object]:
    if command.timed_out:
        raise ContractError(f"{source} timed out")
    if command.returncode != 0:
        raise ContractError(f"{source} failed: {_bounded_text(command.stderr or command.stdout)}")
    try:
        payload = decode_document(_command_bytes(command, "stdout"), limit=limit)
        validate_string_bounds(payload, limit=_MAX_FIELD)
    except (WireError, UnicodeEncodeError) as error:
        raise ContractError(f"{source} returned invalid JSON") from error
    if not isinstance(payload, Mapping):
        raise ContractError(f"{source} returned invalid JSON")
    return payload


def _error_envelope_code(
    command: CommandOutput,
    *,
    limit: int = 512 * 1024,
    validate_host_id: bool = False,
) -> str | None:
    """Return only a structurally valid v1 error code.

    Producer diagnostics are untrusted text.  In particular, a sentence that
    happens to contain ``stale_mesh`` cannot change the refresh transaction.
    """

    if command.timed_out or command.returncode <= 0:
        return None
    try:
        payload = decode_document(_command_bytes(command, "stdout"), limit=limit)
        validate_string_bounds(payload, limit=_MAX_FIELD)
    except (WireError, UnicodeEncodeError):
        return None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != 1
        or isinstance(payload.get("schemaVersion"), bool)
        or not isinstance(payload.get("schemaVersion"), int)
        or payload.get("ok") is not False
        or not isinstance(payload.get("error"), Mapping)
    ):
        return None
    error = payload["error"]
    if "code" not in error or "message" not in error:
        return None
    try:
        if validate_host_id and "hostId" in error:
            _error_host_id(error["hostId"])
        message = error["message"]
        if (
            not isinstance(message, str)
            or not message
            or len(message) > 4096
            or any(unicodedata.category(char).startswith("C") for char in message)
        ):
            return None
        return _error_code(error["code"])
    except ContractError:
        return None


def _raise_command_failure(
    command: CommandOutput,
    source: str,
    *,
    limit: int = _MAX_HOST_STDOUT,
    validate_host_id: bool = False,
) -> None:
    if command.timed_out:
        raise ContractError(f"{source} timed out")
    code = _error_envelope_code(
        command,
        limit=limit,
        validate_host_id=validate_host_id,
    )
    if code == "stale_mesh":
        raise StaleMeshError("Host Mesh revision changed")
    suffix = f" [{code}]" if code else ""
    raise ContractError(
        f"{source} failed{suffix}: {_bounded_text(command.stderr or command.stdout)}"
    )


def _marker(nonce: str) -> str:
    return f"{_MARKER_PREFIX}{nonce}{_MARKER_SUFFIX}"


def parse_reached_marker(stderr: str, nonce: str) -> tuple[bool, str]:
    marker = _marker(nonce)
    return (True, stderr.replace(marker, "", 1)) if stderr.count(marker) == 1 else (False, stderr)


def _transport_failure(stderr: str, timed_out: bool = False) -> bool:
    return timed_out or any(token in stderr.casefold() for token in _TRANSPORT_TEXT)


def _report_argv(
    mesh_command: str,
    mesh: Mesh,
    host: MeshHost,
    route: Route,
    status: str,
    observed_at: int | None = None,
) -> list[str]:
    return [
        mesh_command,
        "mesh",
        "report-route",
        "--json",
        "--host",
        host.host_id,
        "--route",
        route.destination,
        "--status",
        status,
        "--source",
        "rofi-agent-plus",
        "--mesh-revision",
        mesh.revision,
        "--observed-at",
        str(observed_at if observed_at is not None else time.time_ns() // 1_000_000),
    ]


def _ssh_argv(
    mesh: Mesh,
    route: Route,
    nonce: str,
    remote_argv: Sequence[str] = ("python3", "-"),
) -> list[str]:
    wrapper = r'''printf '\036ROFI_PLUS_REACHED_V1:%s\037\n' "$1" >&2
shift
exec "$@"'''
    remote = " ".join(
        shlex.quote(value)
        for value in ("sh", "-c", wrapper, "rofi-plus-reached", nonce, *remote_argv)
    )
    return [
        mesh.executable,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"ConnectTimeout={mesh.connect_timeout}",
        "-o",
        f"ConnectionAttempts={mesh.attempts}",
        route.destination,
        remote,
    ]


def _inventory_args(tmux_command: str, mesh: Mesh, *, with_viewers: bool = False) -> list[str]:
    argv = [tmux_command, "inventory", "--json", "--panes"]
    if with_viewers:
        argv.append("--with-viewers")
    if mesh.revision is not None:
        argv.extend(("--mesh-revision", mesh.revision))
    for host in mesh.hosts:
        argv.extend(("--host", host.host_id))
    for option in _OPTIONS:
        argv.extend(("--session-option", option))
    return argv


def _inventory_text(value: object, label: str, *, empty: bool = False) -> str:
    return _text(value, f"Tmux Session {label}", empty=empty)


def _nonnegative(value: object, label: str, *, nullable: bool = False) -> int | None:
    try:
        return _timestamp(value, f"Tmux Session inventory {label}", nullable=nullable)
    except ContractError as error:
        raise ContractError(f"Tmux Session inventory {label} is invalid") from error


def _count(value: object, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**31 - 1:
        raise ContractError(f"Tmux Session inventory {label} is invalid")
    return value


def _generation(value: object, label: str = "server generation") -> str:
    result = _text(value, f"Tmux Session {label}")
    if any(unicodedata.category(char).startswith("C") for char in result):
        raise ContractError(f"Tmux Session {label} is invalid")
    return result


def _nullable_inventory_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _inventory_text(value, label)


def _validate_inventory_session(session: object, host: MeshHost, generation: str) -> None:
    if not isinstance(session, Mapping):
        raise ContractError("Tmux Session inventory session is invalid")
    _required(
        session,
        "hostId",
        "serverGeneration",
        "sessionId",
        "createdAt",
        "name",
        "activityAt",
        "lastAttachedAt",
        "attachedClients",
        "pending",
        "windowCount",
        "sessionPath",
        "currentWindow",
        "currentPath",
        "panes",
        "options",
    )
    if session["hostId"] != host.host_id or session["serverGeneration"] != generation:
        raise ContractError("Tmux Session inventory session is invalid")
    session_id = session.get("sessionId")
    if (
        not isinstance(session_id, str)
        or len(session_id) > 4096
        or not _SESSION_ID.fullmatch(session_id)
    ):
        raise ContractError("Tmux Session inventory session is invalid")
    _nonnegative(session["createdAt"], "createdAt")
    _nullable_inventory_text(session["name"], "session name")
    _nonnegative(session["activityAt"], "activityAt", nullable=True)
    _nonnegative(session["lastAttachedAt"], "lastAttachedAt", nullable=True)
    _count(session["attachedClients"], "attachedClients", nullable=True)
    _count(session["windowCount"], "windowCount", nullable=True)
    if not isinstance(session["pending"], bool):
        raise ContractError("Tmux Session inventory pending state is invalid")
    for field in ("sessionPath", "currentWindow", "currentPath"):
        _nullable_inventory_text(session[field], field)
    panes = session["panes"]
    options = session["options"]
    if not isinstance(panes, list) or not isinstance(options, Mapping):
        raise ContractError("Tmux Session inventory omitted requested pane metadata")
    if len(panes) > 512:
        raise ContractError("Tmux Session inventory has too many panes")
    seen_panes: set[str] = set()
    for pane in panes:
        if not isinstance(pane, Mapping):
            raise ContractError("Tmux Session pane is invalid")
        _required(pane, "paneId", "pid", "currentPath", "currentCommand")
        pane_id = pane.get("paneId")
        if (
            not isinstance(pane_id, str)
            or len(pane_id) > 4096
            or not _PANE_ID.fullmatch(pane_id)
            or pane_id in seen_panes
        ):
            raise ContractError("Tmux Session pane is invalid")
        seen_panes.add(pane_id)
        _count(pane["pid"], "pane pid", nullable=True)
        _nullable_inventory_text(pane["currentPath"], "pane path")
        _nullable_inventory_text(pane["currentCommand"], "pane command")
    for option in _OPTIONS:
        if option not in options:
            raise ContractError("Tmux Session inventory omitted requested option")
        if options[option] is not None:
            _inventory_text(options[option], "session option")
    for option, option_value in options.items():
        if (
            not isinstance(option, str)
            or len(option) > 4096
            or not re.fullmatch(r"@[A-Za-z0-9_.-]+", option, re.ASCII)
        ):
            raise ContractError("Tmux Session inventory option name is invalid")
        if option_value is not None:
            _inventory_text(option_value, "session option")


def _inventory(payload: object, mesh: Mesh) -> dict[str, dict[str, object]]:
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != 1
        or isinstance(payload.get("schemaVersion"), bool)
        or not isinstance(payload.get("schemaVersion"), int)
        or payload.get("meshRevision") != mesh.revision
    ):
        raise ContractError("Tmux Session inventory returned an unsupported schema")
    _required(payload, "generatedAt", "meshRevision", "hosts")
    _nonnegative(payload["generatedAt"], "generatedAt")
    hosts = payload.get("hosts")
    if (
        not isinstance(hosts, list)
        or not 1 <= len(hosts) <= _MAX_HOSTS
        or len(hosts) != len(mesh.hosts)
    ):
        raise ContractError("Tmux Session inventory host coverage is invalid")
    result: dict[str, dict[str, object]] = {}
    for expected, value in zip(mesh.hosts, hosts, strict=True):
        if not isinstance(value, Mapping):
            raise ContractError("Tmux Session inventory host coverage is invalid")
        _required(
            value,
            "hostId",
            "display",
            "local",
            "status",
            "observedAt",
            "nativeHostname",
            "serverGeneration",
            "route",
            "sessions",
        )
        if (
            value["hostId"] != expected.host_id
            or value["display"] != expected.display
            or value["local"] is not expected.local
        ):
            raise ContractError("Tmux Session inventory host coverage is invalid")
        status = value.get("status")
        sessions = value.get("sessions")
        if not isinstance(status, str) or not isinstance(sessions, list):
            raise ContractError("Tmux Session inventory host is invalid")
        if status not in {"ok", "unreachable", "error", "tmux_missing"}:
            raise ContractError("Tmux Session inventory host status is invalid")
        _nonnegative(value["observedAt"], "observedAt")
        _nullable_inventory_text(value["nativeHostname"], "native hostname")
        route = value["route"]
        if route is not None:
            _inventory_text(route, "route")
        if expected.local:
            if route is not None:
                raise ContractError("Tmux Session inventory local route is invalid")
        elif status == "unreachable":
            if route is not None:
                raise ContractError("Tmux Session inventory unreachable route is invalid")
        elif route is not None and route not in {item.destination for item in expected.routes}:
            raise ContractError("Tmux Session inventory route is invalid")
        generation_value = value["serverGeneration"]
        if status == "ok":
            generation = generation_value
            if generation is None and sessions:
                raise ContractError("Tmux Session server generation is invalid")
            if generation is not None and not isinstance(generation, str):
                raise ContractError("Tmux Session server generation is invalid")
            if isinstance(generation, str):
                generation = _generation(generation)
        else:
            if sessions or generation_value is not None:
                raise ContractError("Tmux Session inventory failure host is invalid")
            error = value.get("error")
            if not isinstance(error, Mapping):
                raise ContractError("Tmux Session inventory error is invalid")
            _required(error, "code", "message")
            try:
                _error_code(error["code"], "Tmux Session error code")
            except ContractError as exc:
                raise ContractError("Tmux Session inventory error is invalid") from exc
            message = error["message"]
            if (
                not isinstance(message, str)
                or not message
                or len(message) > 4096
                or any(unicodedata.category(char).startswith("C") for char in message)
            ):
                raise ContractError("Tmux Session inventory error is invalid")
        if status == "ok" and "error" in value:
            raise ContractError("Tmux Session inventory ok host is invalid")
        if len(sessions) > 256:
            raise ContractError("Tmux Session inventory has too many sessions")
        pane_total = 0
        if isinstance(generation := value.get("serverGeneration"), str):
            seen_refs: set[tuple[str, int]] = set()
            for session in sessions:
                _validate_inventory_session(session, expected, generation)
                assert isinstance(session, Mapping)
                pane_total += len(session["panes"])
                reference = (str(session["sessionId"]), int(session["createdAt"]))
                if reference in seen_refs:
                    raise ContractError("Tmux Session inventory duplicate session reference")
                seen_refs.add(reference)
        elif sessions:
            raise ContractError("Tmux Session server generation is invalid")
        if pane_total > 512:
            raise ContractError("Tmux Session inventory has too many panes for host")
        result[expected.host_id] = dict(value)
    try:
        validate_string_bounds(payload, limit=_MAX_FIELD)
    except WireError as error:
        raise ContractError("Tmux Session inventory contains an oversized string") from error
    return result


def _tmux_reference(session: Mapping[str, object], revision: str | None) -> dict[str, object]:
    """Validate and project a session into the only tmux action identity.

    The provider owns the primary row identity.  This deliberately keeps the
    tmux record subordinate, complete, and impossible to reconstruct from a
    display name later in the UI.
    """

    reference = (
        session.get("serverGeneration"),
        session.get("sessionId"),
        session.get("createdAt"),
    )
    if (
        not isinstance(reference[0], str)
        or not reference[0]
        or not isinstance(reference[1], str)
        or len(reference[1]) > 4096
        or not _SESSION_ID.fullmatch(reference[1])
        or isinstance(reference[2], bool)
        or not isinstance(reference[2], int)
        or reference[2] < 0
    ):
        raise ContractError("Tmux Session inventory session reference is invalid")
    generation = _generation(reference[0])
    created_at = _nonnegative(reference[2], "createdAt")
    assert created_at is not None
    observed_name = session.get("name")
    if observed_name is not None:
        _inventory_text(observed_name, "session name")
    return {
        "meshRevision": revision,
        "serverGeneration": generation,
        "sessionId": reference[1],
        "createdAt": created_at,
        "observedName": observed_name,
    }


class ContractBackend:
    """Contract-backed discovery with one bounded Mesh retry on stale state."""

    kind = "contract"

    def __init__(
        self,
        ssh_command: str | None,
        tmux_command: str,
        *,
        runner: Runner = _run_bounded,
        which: Callable[[str], str | None] = shutil.which,
        now_millis: Callable[[], int] | None = None,
    ) -> None:
        self.ssh_command = ssh_command
        self.tmux_command = tmux_command
        self._run = runner
        self._which = which
        self._now_millis = now_millis or (lambda: time.time_ns() // 1_000_000)
        self.mesh: Mesh | None = None
        self._report_errors: list[dict[str, str]] = []
        self._report_error_lock = Lock()
        self._report_state_lock = Lock()
        self._reported_hints: set[tuple[str, str, str]] = set()
        self._report_flights: dict[tuple[str, str, str], _ReportFlight] = {}
        self._stream_deadline: float | None = None
        self.viewer_inventory_result: Mapping[str, object] | None = None
        self.viewer_inventory_desktop: str | None = None
        self.viewer_inventory_started_at: int | None = None

    @property
    def identity(self) -> dict[str, object]:
        return {
            "kind": "contract",
            "capability": "host-mesh-v1+tmux-session-v1",
            "meshRevision": self.mesh.revision if self.mesh else None,
        }

    def prepare(self, *, deadline: float | None = None) -> None:
        """Observe Host Mesh without extending an enclosing action deadline.

        Browse preparation normally gets the regular refresh budget.  A
        lifecycle action, however, has already spent time revalidating its
        provider row; its final authority re-observation must consume the
        same absolute budget rather than quietly starting another one.
        """

        if deadline is not None:
            if self._stream_deadline is None or deadline < self._stream_deadline:
                self._stream_deadline = deadline
        elif self._stream_deadline is None:
            self._stream_deadline = time.monotonic() + _WHOLE_REFRESH_MAX_SECONDS
        if self.ssh_command is None:
            self.mesh = _local_mesh()
            return
        output = self._run(
            [self.ssh_command, "mesh", "list", "--json"],
            timeout=min(5.0, self._remaining(self._stream_deadline)),
            stdout_limit=_MAX_HOST_STDOUT,
            stderr_limit=_MAX_STDERR,
        )
        if output.returncode != 0:
            _raise_command_failure(output, "Host Mesh")
        self.mesh = parse_mesh(_json(output, "Host Mesh", limit=_MAX_HOST_STDOUT))

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ContractError("contract refresh timed out")
        return remaining

    def _inventory_document(
        self, mesh: Mesh, deadline: float
    ) -> tuple[Mapping[str, object], dict[str, dict[str, object]]]:
        from .viewer_state import desktop_context

        self.viewer_inventory_result = None
        self.viewer_inventory_desktop = desktop_context()
        self.viewer_inventory_started_at = self._now_millis()
        command = self._run(
            _inventory_args(self.tmux_command, mesh, with_viewers=True),
            timeout=min(15.0, self._remaining(deadline)),
            stdout_limit=_MAX_INVENTORY_STDOUT,
            stderr_limit=_MAX_STDERR,
        )
        if command.returncode != 0:
            _raise_command_failure(
                command,
                "Tmux Session inventory",
                limit=_MAX_INVENTORY_STDOUT,
                validate_host_id=True,
            )
        payload = _json(command, "Tmux Session inventory", limit=_MAX_INVENTORY_STDOUT)
        rows = _inventory(payload, mesh)
        return payload, rows

    def _viewer_fields(self, payload: Mapping[str, object]) -> None:
        from .viewer_state import ViewerStateError, observation

        assert self.mesh is not None
        endpoint = payload.get("viewerEndpoint")
        if not isinstance(endpoint, Mapping) or endpoint.get("hostId") != self.mesh.local.host_id:
            raise ContractError("Tmux viewer inventory endpoint is invalid")
        _nonnegative(endpoint.get("observedAt"), "viewer observedAt")
        try:
            for host in payload["hosts"]:
                for row in host["sessions"]:
                    observation(row.get("localViewer"))
        except ViewerStateError as error:
            raise ContractError(str(error)) from error

    def viewer_inventory(self) -> Mapping[str, object]:
        """Read one public bulk inventory without provider discovery or cache writes."""
        if self.mesh is None:
            self.prepare()
        assert self.mesh is not None
        deadline = min(self._stream_deadline or float("inf"), time.monotonic() + 15.0)
        payload, _rows = self._inventory_document(self.mesh, deadline)
        self._viewer_fields(payload)
        return payload

    def _report(
        self,
        host: MeshHost,
        route: Route,
        status: str,
        deadline: float,
    ) -> None:
        assert self.mesh is not None
        output = self._run(
            _report_argv(
                self.ssh_command,
                self.mesh,
                host,
                route,
                status,
                self._now_millis(),
            ),
            timeout=min(5.0, self._remaining(deadline)),
            stdout_limit=_MAX_HOST_STDOUT,
            stderr_limit=_MAX_STDERR,
        )
        if output.returncode != 0:
            _raise_command_failure(output, "Host Mesh route report")
        response = _json(output, "Host Mesh route report", limit=_MAX_HOST_STDOUT)
        if (
            response.get("schemaVersion") != 1
            or isinstance(response.get("schemaVersion"), bool)
            or not isinstance(response.get("schemaVersion"), int)
            or response.get("ok") is not True
            or not isinstance(response.get("accepted"), bool)
        ):
            raise ContractError("Host Mesh route report returned invalid data")

    def _record_report_error(self, host: MeshHost, error: ContractError) -> None:
        with self._report_error_lock:
            self._report_errors.append(
                {
                    "host": host.host_id,
                    "stage": "route-health",
                    "message": _bounded_text(error),
                }
            )

    def _reset_report_transaction(self) -> None:
        """Forget only successful hint dedupe at a refresh-attempt boundary."""

        with self._report_state_lock:
            self._reported_hints = set()
            self._report_flights = {}

    def _report_hint(self, host: MeshHost, route: Route, status: str, deadline: float) -> None:
        """Send one best-effort health hint without discarding domain truth.

        The composite and Codex domain commands run concurrently, so both can
        observe the same route.  A successful report is shared for this one
        refresh attempt.  A failed report is deliberately *not* remembered:
        the sibling remains allowed to retry and preserve the evidence.
        """

        key = (host.host_id, route.destination, status)
        while True:
            owner = False
            with self._report_state_lock:
                if key in self._reported_hints:
                    return
                flight = self._report_flights.get(key)
                if flight is None:
                    flight = _ReportFlight(Event())
                    self._report_flights[key] = flight
                    owner = True
            if not owner:
                if not flight.completed.wait(self._remaining(deadline)):
                    raise ContractError("contract refresh timed out")
                if flight.stale is not None:
                    raise flight.stale
                # A failed owner is diagnostic but intentionally not cached.
                # Loop so this domain command can make its own bounded retry.
                continue
            try:
                self._report(host, route, status, deadline)
            except StaleMeshError as error:
                with self._report_state_lock:
                    flight.stale = error
                raise
            except ContractError as error:
                self._record_report_error(host, error)
                return
            else:
                with self._report_state_lock:
                    self._reported_hints.add(key)
                return
            finally:
                with self._report_state_lock:
                    if self._report_flights.get(key) is flight:
                        self._report_flights.pop(key, None)
                    flight.completed.set()

    def _observer_snapshot(
        self,
        host: MeshHost,
        deadline: float,
        *,
        providers: tuple[str, ...] = _DEFAULT_PROVIDERS,
    ) -> tuple[str | None, ObserverSnapshot | ContractError]:
        """Read one host-scoped Observer document without provider fallbacks."""

        route: str | None = None
        argv = ["agent-observer", "snapshot", "--host-scope", host.host_id]
        if providers != _DEFAULT_PROVIDERS:
            for provider in providers:
                argv.extend(("--provider", provider))
        try:
            if host.local:
                command: CommandOutput | ContractError = self._run(
                    argv,
                    timeout=self._remaining(deadline),
                    stdout_limit=_MAX_HOST_STDOUT,
                    stderr_limit=_MAX_STDERR,
                )
            else:
                route, command = self._remote_command(
                    host,
                    deadline,
                    remote_argv=argv,
                    input_data=None,
                    label="Agent Observer snapshot",
                    stdout_limit=_MAX_HOST_STDOUT,
                )
            if isinstance(command, StaleMeshError):
                raise command
            if isinstance(command, ContractError):
                return route, command
            if command.timed_out or command.returncode != 0:
                return route, ContractError("Agent Observer snapshot command failed")
            try:
                return route, parse_snapshot(
                    _command_bytes(command, "stdout"),
                    expected_host_scope=host.host_id,
                    expected_providers=providers,
                    limit=_MAX_HOST_STDOUT,
                )
            except ObserverContractError as error:
                return route, ContractError(_bounded_text(error))
        except StaleMeshError:
            raise
        except ContractError as error:
            return route, ContractError(_bounded_text(error))

    def _remote_command(
        self,
        host: MeshHost,
        deadline: float,
        *,
        remote_argv: Sequence[str],
        input_data: bytes | None,
        label: str,
        stdout_limit: int = _MAX_STDOUT,
    ) -> tuple[str | None, CommandOutput | ContractError]:
        """Run one real remote domain command through every eligible route.

        A marker ends selection even on a domain failure.  Before a marker,
        only classified transport evidence receives an unavailable hint; a
        non-stale report failure remains diagnostic and never destroys a
        reached result or prevents the next candidate.
        """

        assert self.mesh is not None
        unclassified: str | None = None
        for route in host.routes:
            nonce = secrets.token_hex(16)
            try:
                output = self._run(
                    _ssh_argv(self.mesh, route, nonce, remote_argv),
                    input_data=input_data,
                    timeout=min(float(self.mesh.connect_timeout + 4), self._remaining(deadline)),
                    stdout_limit=stdout_limit,
                    stderr_limit=_MAX_STDERR,
                )
            except ContractError as error:
                if _transport_failure(str(error), "timed out" in str(error)):
                    self._report_hint(host, route, "unreachable", deadline)
                else:
                    unclassified = _bounded_text(error)
                continue
            reached, stderr = parse_reached_marker(output.stderr, nonce)
            if reached:
                self._report_hint(host, route, "reachable", deadline)
                # Deliberately return the completed domain command even when
                # nonzero: the marker makes it authoritative and terminal.
                stderr_bytes = output.stderr_bytes
                if stderr_bytes is not None:
                    marker_bytes = _marker(nonce).encode("utf-8")
                    if stderr_bytes.count(marker_bytes) == 1:
                        stderr_bytes = stderr_bytes.replace(marker_bytes, b"", 1)
                return route.destination, CommandOutput(
                    output.argv,
                    output.returncode,
                    output.stdout,
                    stderr,
                    output.timed_out,
                    output.stdout_bytes,
                    stderr_bytes,
                )
            if _transport_failure(output.stderr):
                self._report_hint(host, route, "unreachable", deadline)
            else:
                unclassified = _bounded_text(output.stderr or f"SSH exited {output.returncode}")
        return None, ContractError(unclassified or f"{label} could not reach host")

    def observer_write(self, request: dict[str, object], *, deadline: float) -> dict[str, object]:
        """Prepare through public read routing, then execute once on that route."""
        if self.mesh is None:
            raise ContractError("Host Mesh authority is unavailable")
        host = next(
            (item for item in self.mesh.hosts if item.host_id == request["hostScope"]), None
        )
        if host is None:
            raise StaleMeshError("selected host is no longer in the current Host Mesh")
        prepare_argv = ("agent-observer-write", "prepare", "--request", "-")
        route: str | None = None
        if host.local:
            command = self._run(
                prepare_argv,
                input_data=encoded(request),
                timeout=self._remaining(deadline),
                stdout_limit=MAX_WRITE_BYTES,
                stderr_limit=MAX_WRITE_BYTES,
            )
        else:
            route, command = self._remote_command(
                host,
                deadline,
                remote_argv=prepare_argv,
                input_data=encoded(request),
                label="Observer write preparation",
                stdout_limit=MAX_WRITE_BYTES,
            )
        if isinstance(command, ContractError):
            raise command
        if command.timed_out or command.returncode != 0:
            code = rejection_code(_command_bytes(command, "stderr"))
            raise ContractError(
                "Observer write preparation rejected (" + (code or "context_unavailable") + ")"
            )
        try:
            plan = prepared(_command_bytes(command, "stdout"), request)
        except WriteBoundaryError as error:
            raise ContractError(str(error)) from error
        # Execution can dispatch provider work. From this point no route,
        # transport, stale authority or malformed result causes redispatch.
        dispatched = False
        try:
            remaining = self._remaining(deadline)
            if remaining < 3:
                raise ContractError("Observer write deadline is exhausted before dispatch")
            execute_argv = (
                "agent-observer-write",
                "execute",
                "--plan",
                "-",
                "--timeout",
                str(min(30.0, max(1.0, (remaining - 2) / 2))),
            )
            if host.local:
                dispatched = True
                command = self._run(
                    execute_argv,
                    input_data=encoded(plan),
                    timeout=remaining,
                    stdout_limit=MAX_WRITE_BYTES,
                    stderr_limit=MAX_WRITE_BYTES,
                    own_process_group=False,
                )
            else:
                selected = next((item for item in host.routes if item.destination == route), None)
                if selected is None:
                    raise ContractError("Observer write prepared route is unavailable")
                nonce = secrets.token_hex(16)
                dispatched = True
                command = self._run(
                    _ssh_argv(self.mesh, selected, nonce, execute_argv),
                    input_data=encoded(plan),
                    timeout=remaining,
                    stdout_limit=MAX_WRITE_BYTES,
                    stderr_limit=_MAX_STDERR,
                    own_process_group=False,
                )
                reached, stderr = parse_reached_marker(command.stderr, nonce)
                if not reached:
                    raise ContractError("Observer write route outcome is unknown")
                command = CommandOutput(
                    command.argv,
                    command.returncode,
                    command.stdout,
                    stderr,
                    command.timed_out,
                    command.stdout_bytes,
                    stderr.encode(),
                )
            if command.timed_out:
                raise ContractError("Observer write command timed out")
            if command.returncode == 2:
                code = rejection_code(_command_bytes(command, "stderr"))
                if code is not None:
                    raise WriteOperationError(
                        "Observer write rejected (" + code + ")", effect="none"
                    )
            result = completed(_command_bytes(command, "stdout"), plan)
            expected_exit = 0 if result["status"] in {"prepared", "ready"} else 3
            if command.returncode != expected_exit:
                raise ContractError("Observer write result exit code disagrees")
            if result["status"] not in {"prepared", "ready"}:
                raise WriteOperationError(
                    "Observer write "
                    + result["status"]
                    + " ("
                    + result["reason"]
                    + "); no retry was attempted",
                    effect=result["effect"],
                    result=result,
                )
            return result
        except WriteOperationError:
            raise
        except (ContractError, WriteBoundaryError, OSError, WireError) as error:
            raise WriteOperationError(
                "Observer write outcome is uncertain; no retry was attempted"
                if dispatched
                else "Observer write dispatch unavailable",
                effect="uncertain" if dispatched else "none",
            ) from error

    def _once(
        self,
        config: Any,
        deadline: float | None = None,
        host_ids: Sequence[str] | None = None,
    ) -> list[dict[str, object]]:
        assert self.mesh is not None
        if host_ids is None:
            mesh = self.mesh
        else:
            requested = tuple(dict.fromkeys(host_ids))
            selected = tuple(host for host in self.mesh.hosts if host.host_id in requested)
            if len(selected) != len(requested):
                raise ContractError("selected host is not in the current Host Mesh")
            mesh = Mesh(
                self.mesh.revision,
                self.mesh.executable,
                self.mesh.connect_timeout,
                self.mesh.attempts,
                selected,
            )
        deadline = deadline or self._stream_deadline
        if deadline is None:
            whole_seconds = max(
                _WHOLE_REFRESH_MIN_SECONDS,
                min(
                    _WHOLE_REFRESH_MAX_SECONDS,
                    float(mesh.connect_timeout * mesh.attempts + 12),
                ),
            )
            deadline = time.monotonic() + whole_seconds
        self._remaining(deadline)
        with self._report_error_lock:
            self._report_errors = []
        self._reset_report_transaction()
        stages: dict[str, tuple[str | None, ObserverSnapshot | ContractError]] = {}

        def host_stage(
            host: MeshHost,
        ) -> tuple[str, tuple[str | None, ObserverSnapshot | ContractError]]:
            providers = config.providers_for(host.host_id)
            if providers == _DEFAULT_PROVIDERS:
                return host.host_id, self._observer_snapshot(host, deadline)
            return host.host_id, self._observer_snapshot(host, deadline, providers=providers)

        def inventory_stage() -> dict[str, dict[str, object]]:
            payload, rows = self._inventory_document(mesh, deadline)
            try:
                self._viewer_fields(payload)
            except ContractError:
                # Display enrichment cannot turn valid owner facts into a
                # provider/tmux discovery failure. The private viewer cache
                # records uncertainty independently after this transaction.
                pass
            else:
                self.viewer_inventory_result = payload
            return rows

        # Inventory does not depend on provider-native results.  Start it with
        # host probes, while retaining one shared deadline and joining every
        # child before returning so no post-refresh work survives this call.
        with ThreadPoolExecutor(
            max_workers=min(_MAX_HOST_WORKERS, len(mesh.hosts)) + 1,
            thread_name_prefix="rofi-agent-host",
        ) as pool:
            futures = {pool.submit(host_stage, host): host.host_id for host in mesh.hosts}
            inventory_future = pool.submit(inventory_stage)
            try:
                for future in as_completed(futures, timeout=self._remaining(deadline)):
                    host_id, stage = future.result()
                    stages[host_id] = stage
                tmux = inventory_future.result(timeout=self._remaining(deadline))
                tmux_error: ContractError | None = None
            except FuturesTimeoutError as error:
                for future in (*futures, inventory_future):
                    future.cancel()
                raise ContractError("contract refresh timed out") from error
            except StaleMeshError:
                raise
            except ContractError as error:
                tmux = {}
                tmux_error = error
        events: list[dict[str, object]] = [
            {
                "event": "refresh-started",
                "hosts": [host.host_id for host in mesh.hosts],
                "hostCatalog": [
                    {
                        "hostId": host.host_id,
                        "display": host.display,
                        "local": host.local,
                    }
                    # ``mesh`` may be a selected-host subset for lifecycle
                    # revalidation.  The presentation ring still needs the
                    # complete ordered authority from the current Mesh.
                    for host in self.mesh.hosts
                ],
                "backend": self.identity,
            }
        ]
        for host in mesh.hosts:
            route, observation = stages[host.host_id]
            if isinstance(observation, ContractError):
                sessions: list[dict[str, object]] = []
                errors = [
                    {
                        "host": host.host_id,
                        "stage": _PROVIDER_STAGE[provider],
                        "message": _bounded_text(observation),
                    }
                    for provider in config.providers_for(host.host_id)
                ]
            else:
                sessions = [
                    dict(row) for row in observation.sessions if row.get("threadKind") != "child"
                ]
                errors = [dict(error) for error in observation.errors]
            with self._report_error_lock:
                errors.extend(
                    error for error in self._report_errors if error["host"] == host.host_id
                )
            tmux_row = tmux.get(host.host_id)
            if tmux_error is not None:
                errors.append(
                    {"host": host.host_id, "stage": "tmux", "message": _bounded_text(tmux_error)}
                )
            elif not isinstance(tmux_row, Mapping):
                errors.append(
                    {
                        "host": host.host_id,
                        "stage": "tmux",
                        "message": "missing",
                    }
                )
            elif tmux_row.get("status") in {"unreachable", "error"}:
                errors.append(
                    {
                        "host": host.host_id,
                        "stage": "tmux",
                        "message": _bounded_text(tmux_row.get("status")),
                    }
                )
            elif tmux_row.get("status") == "tmux_missing":
                # Reached capability truth is authoritative: it clears an old
                # reference rather than making it look currently attachable.
                errors.append(
                    {
                        "host": host.host_id,
                        "stage": "tmux-missing",
                        "message": "tmux_missing",
                    }
                )
            for row in sessions:
                row["contractMode"] = True
                row["backend"] = dict(self.identity)
                row["host"] = host.display
                row["hostId"] = host.host_id
            events.append(
                {
                    "event": "host-complete",
                    "host": host.host_id,
                    "sessions": sessions,
                    "errors": errors,
                    "incompleteProviders": list(observation.incomplete_providers)
                    if isinstance(observation, ObserverSnapshot)
                    else list(config.providers_for(host.host_id)),
                    "backend": self.identity,
                }
            )
        events.append(
            {"event": "refresh-finished", "generatedAt": int(time.time()), "backend": self.identity}
        )
        return events

    def stream(
        self,
        config: Any,
        *,
        deadline: float | None = None,
        host_ids: Sequence[str] | None = None,
        retry_stale_mesh: bool = True,
    ) -> Sequence[dict[str, object]]:
        if deadline is not None:
            self._stream_deadline = deadline
        elif self._stream_deadline is None:
            self._stream_deadline = time.monotonic() + _WHOLE_REFRESH_MAX_SECONDS
        try:
            for attempt in range(2 if retry_stale_mesh else 1):
                try:
                    return self._once(config, self._stream_deadline, host_ids)
                except StaleMeshError:
                    if attempt or not retry_stale_mesh:
                        raise
                    self.prepare()
            raise AssertionError("unreachable")
        finally:
            self._stream_deadline = None


def select_backend(
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: Runner = _run_bounded,
) -> ContractBackend:
    """Select the mandatory Tmux contract and optional Host Mesh contract."""

    ssh_command = which("rofi-ssh-plus")
    tmux_command = which("rofi-tmux-plus")
    if tmux_command is None:
        raise ContractError("rofi-tmux-plus is required for Agent Plus")
    return ContractBackend(ssh_command, tmux_command, runner=runner, which=which)
