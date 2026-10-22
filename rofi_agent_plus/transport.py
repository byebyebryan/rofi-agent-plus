"""Bounded Host Mesh inventory and one-route native entry transport."""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import sys
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .host_mesh import MESH_COMMAND, HostMeshError, Mesh, MeshHost, Route, resolve_self_mesh
from .model import (
    MAX_REFERENCE_BYTES,
    SUPPORTED_PROVIDERS,
    Inventory,
    RecordError,
    decode_document_json,
    decode_reference_json,
    parse_inventory,
)
from .process import ProcessError, ProcessResult, run_bounded

FLEET_TIMEOUT = 20.0
HOST_TIMEOUT = 12.0
MAX_HOST_WORKERS = 4
MAX_HOST_OUTPUT = 1 << 20
MAX_HOST_STDERR = 64 << 10
_MARKER_PREFIX = "\x1eROFI_PLUS_REACHED_V1:"
_MARKER_SUFFIX = "\x1f"
_REMOTE_MARKER = r'''printf '\036ROFI_PLUS_REACHED_V1:%s\037\n' "$1" >&2
shift
exec "$@"'''


class TransportError(RuntimeError):
    """A stable failure in current Host Mesh resolution or transport setup."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TransportDiagnostic:
    code: str
    message: str
    stage: str = "transport"

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "stage": self.stage, "message": self.message[:4096]}


@dataclass(frozen=True)
class HostResult:
    """One host's current catalog result; errors never masquerade as empty hosts."""

    host_id: str
    display: str
    local: bool
    status: str
    observed_at: int
    route: str | None
    inventory: Inventory | None
    error: TransportDiagnostic | None = None


@dataclass(frozen=True)
class FleetSnapshot:
    mesh_revision: str | None
    observed_at: int
    hosts: tuple[HostResult, ...]


@dataclass(frozen=True)
class PinnedTransport:
    """Strict runtime descriptor for the single route selected for entry."""

    kind: str
    host_id: str
    mesh_revision: str | None
    helper: str | None = None
    python: str | None = None
    config: str | None = None
    route: str | None = None
    ssh_executable: str | None = None
    connect_timeout: int | None = None
    attempts: int | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schemaVersion": 1,
            "kind": self.kind,
            "hostId": self.host_id,
            "meshRevision": self.mesh_revision,
        }
        if self.kind == "local":
            if self.helper is not None:
                value["helper"] = self.helper
            if self.python is not None:
                value["python"] = self.python
            if self.config is not None:
                value["config"] = self.config
        else:
            value.update(
                {
                    "route": self.route,
                    "sshExecutable": self.ssh_executable,
                    "connectTimeoutSeconds": self.connect_timeout,
                    "connectionAttempts": self.attempts,
                    "helper": self.helper,
                }
            )
            if self.config is not None:
                value["config"] = self.config
        return value

    @classmethod
    def parse(cls, value: object) -> PinnedTransport:
        if not isinstance(value, dict):
            raise TransportError("invalid_transport", "saved transport descriptor is invalid")
        base = {"schemaVersion", "kind", "hostId", "meshRevision"}
        if value.get("schemaVersion") != 1 or type(value.get("schemaVersion")) is not int:
            raise TransportError("unsupported_transport", "saved transport version is unsupported")
        kind = value.get("kind")
        if kind == "local":
            required, optional = base, {"helper", "python", "config"}
        elif kind == "ssh":
            required = base | {
                "route",
                "sshExecutable",
                "connectTimeoutSeconds",
                "connectionAttempts",
                "helper",
            }
            optional = {"config"}
        else:
            raise TransportError("invalid_transport", "saved transport kind is invalid")
        if not required <= set(value) or set(value) - required - optional:
            raise TransportError("invalid_transport", "saved transport fields are invalid")
        host_id = value.get("hostId")
        revision = value.get("meshRevision")
        if (
            not isinstance(host_id, str)
            or not host_id
            or len(host_id) > 256
            or host_id.casefold() != host_id
            or not host_id[0].isalnum()
            or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in host_id)
        ):
            raise TransportError("invalid_transport", "saved transport host is invalid")
        if revision is not None and (
            not isinstance(revision, str)
            or len(revision) != 71
            or not revision.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in revision[7:])
        ):
            raise TransportError("invalid_transport", "saved mesh revision is invalid")
        fields = {key: value.get(key) for key in ("helper", "python", "config")}
        if any(item is not None and not _bounded_text(item, 4096) for item in fields.values()):
            raise TransportError("invalid_transport", "saved transport path is invalid")
        for key in ("helper", "python", "config"):
            item = fields[key]
            if item is not None and key != "helper" and not Path(item).is_absolute():
                raise TransportError("invalid_transport", "saved transport path is not absolute")
        if kind == "local":
            if fields["helper"] is not None and not Path(fields["helper"]).is_absolute():
                raise TransportError("invalid_transport", "local helper path is not absolute")
            if fields["helper"] is None and fields["python"] is None:
                raise TransportError("invalid_transport", "local helper command is missing")
            return cls(
                kind,
                host_id,
                revision,
                fields["helper"],
                fields["python"],
                fields["config"],
            )
        route = value.get("route")
        ssh = value.get("sshExecutable")
        helper = value.get("helper")
        timeout, attempts = value.get("connectTimeoutSeconds"), value.get("connectionAttempts")
        route_valid = (
            _bounded_text(route, 4096)
            and route == route.strip()
            and not route.startswith("-")
            and not any(char.isspace() for char in route)
        )
        helper_valid = _bounded_text(helper, 4096) and (
            helper == "rofi-agent-plus-host" or Path(helper).is_absolute()
        )
        if (
            not route_valid
            or not _bounded_text(ssh, 4096)
            or not Path(ssh).is_absolute()
            or not helper_valid
            or isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 60
            or isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or not 1 <= attempts <= 10
        ):
            raise TransportError("invalid_transport", "saved SSH transport is invalid")
        return cls(
            kind,
            host_id,
            revision,
            helper=helper,
            config=fields["config"],
            route=route,
            ssh_executable=ssh,
            connect_timeout=timeout,
            attempts=attempts,
        )


def _bounded_text(value: object, limit: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= limit
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _artifact_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _remote_command(helper: str, operation: str, *arguments: str) -> str:
    return shlex.join((helper, operation, *arguments))


class Transport:
    """Current Mesh view plus bounded passive inventory and pinned entry commands."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        runner: Callable[..., ProcessResult] = run_bounded,
        local_helper: str | Path | None = None,
        remote_helpers: Mapping[str, str | Path] | None = None,
        config_paths: Mapping[str, str | Path] | None = None,
        ssh_executable: str | None = None,
        mesh: Mesh | None = None,
    ):
        self.env = dict(os.environ if env is None else env)
        self.runner = runner
        self.local_helper = str(Path(local_helper).resolve()) if local_helper is not None else None
        self.remote_helpers = {
            key.casefold(): str(value) for key, value in (remote_helpers or {}).items()
        }
        self.config_paths = {
            key.casefold(): str(Path(value).resolve())
            for key, value in (config_paths or {}).items()
        }
        self.ssh_override = ssh_executable
        self._provided_mesh = mesh
        self._last_mesh_revision: str | None = None

    @property
    def current_mesh_revision(self) -> str | None:
        """Revision used by the most recent passive fleet read, if available."""

        return self._last_mesh_revision

    def _current_mesh(self) -> Mesh:
        # An injected snapshot is useful for controlled callers/tests. Normal
        # callers resolve the public authority per operation, so a long-lived
        # picker never pins a route from an earlier mesh revision.
        if self._provided_mesh is not None:
            return self._provided_mesh
        executable = shutil.which(MESH_COMMAND, path=self.env.get("PATH", ""))
        try:
            mesh = resolve_self_mesh(executable, runner=self.runner, env=self.env)
        except HostMeshError as error:
            raise TransportError(error.code, str(error)) from error
        return mesh

    def _ssh(self, mesh: Mesh) -> str:
        name = self.ssh_override or mesh.executable
        executable = shutil.which(name, path=self.env.get("PATH", ""))
        if executable is None:
            raise TransportError("ssh_unavailable", "configured SSH executable is unavailable")
        return executable

    def _local_command(
        self, mesh: Mesh, operation: str, *arguments: str
    ) -> tuple[list[str], dict[str, str]]:
        if self.local_helper is not None:
            return [self.local_helper, operation, *arguments], dict(self.env)
        # Resolving a venv's python symlink escapes the selected artifact.
        python = sys.executable
        source_wrapper = _artifact_root() / "bin" / "rofi-agent-plus-host"
        if source_wrapper.is_file():
            return [python, str(source_wrapper), operation, *arguments], dict(self.env)
        return (
            [python, "-I", "-m", "rofi_agent_plus.host", operation, *arguments],
            dict(self.env),
        )

    def _helper(self, host: MeshHost) -> str:
        if host.local:
            return self.local_helper or ""
        return self.remote_helpers.get(host.host_id, "rofi-agent-plus-host")

    def _config(self, host: MeshHost) -> str | None:
        return self.config_paths.get(host.host_id)

    @staticmethod
    def _inventory_arguments(config: str | None) -> tuple[str, ...]:
        return ("inventory", "--json", *(("--config", config) if config else ()))

    def _ssh_inventory_argv(
        self, mesh: Mesh, route: Route, nonce: str, helper: str, config: str | None
    ) -> list[str]:
        arguments = self._inventory_arguments(config)
        remote = shlex.join(
            ("sh", "-c", _REMOTE_MARKER, "rofi-plus-reached", nonce, helper, *arguments)
        )
        ssh = self._ssh(mesh)
        return [
            ssh,
            "-T",
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

    def _read_host(self, mesh: Mesh, host: MeshHost, fleet_deadline: float) -> HostResult:
        started = time.monotonic()
        host_deadline = min(fleet_deadline, started + HOST_TIMEOUT)
        if host.local:
            try:
                argv, child_env = self._local_command(
                    mesh, *self._inventory_arguments(self._config(host))
                )
                output = self.runner(
                    argv,
                    timeout=max(0.001, host_deadline - time.monotonic()),
                    stdout_limit=MAX_HOST_OUTPUT,
                    stderr_limit=MAX_HOST_STDERR,
                    env=child_env,
                )
                return self._host_result(host, output, None)
            except (OSError, ProcessError, RecordError, TransportError) as error:
                return _failed_host(host, _error_code(error), _bounded_message(error), None)

        helper = self._helper(host)
        config = self._config(host)
        for route in host.routes:
            if time.monotonic() >= host_deadline:
                return _failed_host(
                    host, "inventory_timeout", "host inventory deadline expired", None
                )
            nonce = secrets.token_hex(16)
            try:
                argv = self._ssh_inventory_argv(mesh, route, nonce, helper, config)
                output = self.runner(
                    argv,
                    timeout=max(0.001, host_deadline - time.monotonic()),
                    stdout_limit=MAX_HOST_OUTPUT,
                    stderr_limit=MAX_HOST_STDERR,
                    env=self.env,
                )
            except (OSError, ProcessError) as error:
                # A timeout or output-limit error may have happened after the
                # remote marker reached the stream. Since the bounded runner
                # cannot prove otherwise, never dispatch the helper on another
                # route after an incomplete SSH observation.
                return _failed_host(
                    host,
                    _error_code(error),
                    "SSH inventory observation ended before reachability was confirmed",
                    None,
                )
            stderr = _as_bytes(output.stderr)
            marker = (_MARKER_PREFIX + nonce + _MARKER_SUFFIX).encode("ascii")
            if stderr.count(marker) != 1:
                continue
            clean = ProcessResult(
                output.argv,
                output.returncode,
                _as_bytes(output.stdout),
                stderr.replace(marker + b"\n", b"", 1).replace(marker, b"", 1),
            )
            return self._host_result(host, clean, route.destination)
        return _failed_host(
            host, "ssh_unreachable", "no configured SSH route reached the host", None
        )

    def _host_result(self, host: MeshHost, output: ProcessResult, route: str | None) -> HostResult:
        now = _now_ms()
        try:
            if output.returncode != 0:
                diagnostic = _helper_error(output.stdout)
                if diagnostic is None:
                    diagnostic = TransportDiagnostic(
                        "helper_failed", "host inventory helper failed or is incompatible"
                    )
                return HostResult(
                    host.host_id, host.display, host.local, "error", now, route, None, diagnostic
                )
            inventory = parse_inventory(decode_document_json(_as_bytes(output.stdout)))
        except (RecordError, UnicodeError, OSError):
            return _failed_host(
                host,
                "invalid_inventory",
                "host returned invalid or unsupported inventory",
                route,
                stage="inventory",
            )
        if inventory.host_id != host.host_id:
            return _failed_host(
                host,
                "host_identity_mismatch",
                "helper self identity does not match Host Mesh",
                route,
                stage="identity",
            )
        covered = {item.provider for item in inventory.provider_coverage}
        partial = (
            bool(inventory.errors)
            or covered != set(SUPPORTED_PROVIDERS)
            or any(item.status in {"partial", "unknown"} for item in inventory.provider_coverage)
        )
        return HostResult(
            host.host_id,
            host.display,
            host.local,
            "partial" if partial else "complete",
            inventory.observed_at,
            route,
            inventory,
        )

    def read_fleet(self, *, on_result: Callable[[HostResult], None] | None = None) -> FleetSnapshot:
        """Read every current host and optionally publish each result as it lands.

        The callback runs in this caller thread, once per host, including bounded
        failure/timeout results. It is intentionally absent from ``pin_entry``
        and should only persist passive inventory state.
        """

        mesh = self._current_mesh()
        self._last_mesh_revision = mesh.revision
        deadline = time.monotonic() + FLEET_TIMEOUT
        workers = min(MAX_HOST_WORKERS, len(mesh.hosts))
        results: dict[str, HostResult] = {}

        def publish(result: HostResult) -> None:
            results[result.host_id] = result
            if on_result is not None:
                on_result(result)

        with ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix="agent-plus-host"
        ) as pool:
            futures = {
                pool.submit(self._read_host, mesh, host, deadline): host for host in mesh.hosts
            }
            try:
                for future in as_completed(futures, timeout=FLEET_TIMEOUT):
                    host = futures[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        result = _failed_host(
                            host, _error_code(error), _bounded_message(error), None
                        )
                    publish(result)
            except TimeoutError:
                for future in futures:
                    future.cancel()
                for host in futures.values():
                    if host.host_id not in results:
                        publish(
                            _failed_host(
                                host, "inventory_timeout", "fleet inventory deadline expired", None
                            )
                        )
        return FleetSnapshot(
            mesh.revision,
            _now_ms(),
            tuple(results[host.host_id] for host in mesh.hosts),
        )

    def pin_entry(self, host_id: str) -> PinnedTransport:
        mesh = self._current_mesh()
        normalized = host_id.casefold() if isinstance(host_id, str) else ""
        host = next((item for item in mesh.hosts if item.host_id == normalized), None)
        if host is None:
            raise TransportError("host_missing", "host is not in the current Host Mesh")
        config = self._config(host)
        if host.local:
            if self.local_helper is not None:
                pin = PinnedTransport(
                    "local", host.host_id, mesh.revision, helper=self.local_helper, config=config
                )
            else:
                python = sys.executable
                wrapper = _artifact_root() / "bin" / "rofi-agent-plus-host"
                pin = (
                    PinnedTransport(
                        "local",
                        host.host_id,
                        mesh.revision,
                        helper=str(wrapper),
                        python=python,
                        config=config,
                    )
                    if wrapper.is_file()
                    else PinnedTransport(
                        "local", host.host_id, mesh.revision, python=python, config=config
                    )
                )
            PinnedTransport.parse(pin.to_json())
            return pin
        if not host.routes:
            raise TransportError("route_unavailable", "host has no current SSH route")
        helper = self._helper(host)
        if not _bounded_text(helper, 4096):
            raise TransportError("helper_unavailable", "host helper path is invalid")
        pin = PinnedTransport(
            "ssh",
            host.host_id,
            mesh.revision,
            helper=helper,
            config=config,
            route=host.routes[0].destination,
            ssh_executable=self._ssh(mesh),
            connect_timeout=mesh.connect_timeout,
            attempts=mesh.attempts,
        )
        PinnedTransport.parse(pin.to_json())
        return pin

    def entry_environment(self, pinned: PinnedTransport | dict[str, object]) -> dict[str, str]:
        PinnedTransport.parse(pinned.to_json() if isinstance(pinned, PinnedTransport) else pinned)
        return dict(self.env)

    def entry_argv(
        self, reference_json: str, pinned: PinnedTransport | dict[str, object]
    ) -> tuple[str, ...]:
        pin = PinnedTransport.parse(
            pinned.to_json() if isinstance(pinned, PinnedTransport) else pinned
        )
        reference = decode_reference_json(reference_json)
        if reference.expected_host_id != pin.host_id:
            raise TransportError(
                "reference_host_mismatch", "entry reference host does not match route"
            )
        if len(reference_json.encode("utf-8", "strict")) > MAX_REFERENCE_BYTES:
            raise TransportError("reference_too_large", "entry reference exceeds its byte limit")
        arguments = ["enter", "--reference-json", reference_json]
        if pin.config:
            arguments.extend(("--config", pin.config))
        if pin.kind == "local":
            if pin.helper is not None:
                if pin.python is not None:
                    return tuple((pin.python, pin.helper, *arguments))
                return tuple((pin.helper, *arguments))
            assert pin.python is not None
            return tuple((pin.python, "-I", "-m", "rofi_agent_plus.host", *arguments))
        assert pin.route and pin.ssh_executable and pin.helper
        remote = _remote_command(pin.helper, *arguments)
        return (
            pin.ssh_executable,
            "-tt",
            "-o",
            "BatchMode=no",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={pin.connect_timeout}",
            "-o",
            f"ConnectionAttempts={pin.attempts}",
            pin.route,
            remote,
        )


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", "strict")
    return b""


def _helper_error(raw: object) -> TransportDiagnostic | None:
    try:
        value = decode_document_json(_as_bytes(raw))
    except RecordError:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "ok", "error"}
        or type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
        or value.get("ok") is not False
    ):
        return None
    error = value.get("error")
    if not isinstance(error, dict) or set(error) != {"code", "stage", "message"}:
        return None
    code, stage, message = error.get("code"), error.get("stage"), error.get("message")
    if (
        not _bounded_text(code, 64)
        or not code[0].isalpha()
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for char in code
        )
        or not _bounded_text(stage, 64)
        or not stage[0].isalpha()
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for char in stage
        )
        or not _bounded_text(message, 4096)
    ):
        return None
    return TransportDiagnostic(code, message, stage)


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    return code if _bounded_text(code, 64) else "transport_failed"


def _bounded_message(error: BaseException) -> str:
    return str(error)[:4096] or "host transport failed"


def _failed_host(
    host: MeshHost, code: str, message: str, route: str | None, *, stage: str = "transport"
) -> HostResult:
    return HostResult(
        host.host_id,
        host.display,
        host.local,
        "error",
        _now_ms(),
        route,
        None,
        TransportDiagnostic(code[:64], message[:4096], stage),
    )
