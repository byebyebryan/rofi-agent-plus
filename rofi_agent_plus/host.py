"""Headless direct host inventory and native manager entry commands."""

from __future__ import annotations

import argparse
import os
import pwd
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import (
    ConfigError,
    DeclarationIssue,
    DesktopConfig,
    DirectConfig,
    EnvironmentDeclaration,
    expand_native_home,
    load_direct_config,
)
from .host_mesh import MESH_COMMAND, HostMeshError, resolve_self_mesh
from .model import (
    HostDiagnostic,
    Inventory,
    LaunchCapability,
    Observation,
    ProviderCoverage,
    RecordError,
    Target,
    TargetKey,
    UserScope,
    canonical_revision,
    decode_reference_json,
    encode_document,
    parse_entry_reference,
)
from .process import ProcessError, ProcessResult, passive_probe_signals, run_bounded
from .providers import ADAPTERS
from .providers.common import ProbeObservation, probe_capability, resolve_executable

INVENTORY_TIMEOUT_SECONDS = 12.0
PROVIDER_STATUS_TIMEOUT_SECONDS = 3.0
MAX_TARGET_COUNT = 128
MAX_ERROR_COUNT = 256
MAX_PROBE_STDOUT = 256 << 10
MAX_PROBE_STDERR = 64 << 10


class HostError(RuntimeError):
    """A bounded, stable failure from a headless host operation."""

    def __init__(self, code: str, stage: str, message: str):
        super().__init__(message)
        self.code = code
        self.stage = stage


@dataclass
class _Candidate:
    provider: str
    home: str
    label: str
    is_default: bool
    declared_labels: set[str]
    conflicting: bool = False


@dataclass(frozen=True)
class EntryPlan:
    executable: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    cwd: str
    target: Target


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _user_identity(
    user_scope: UserScope | None, user_home: Path | str | None
) -> tuple[UserScope, Path]:
    if user_scope is None:
        uid = os.geteuid()
        try:
            account = pwd.getpwuid(uid)
        except KeyError as error:
            raise HostError(
                "user_identity_unavailable", "user_identity", "effective user has no account"
            ) from error
        scope = UserScope(uid, account.pw_name)
        home = Path(user_home) if user_home is not None else Path(account.pw_dir)
    else:
        scope = user_scope
        if user_home is not None:
            home = Path(user_home)
        else:
            try:
                home = Path(pwd.getpwuid(scope.uid).pw_dir)
            except KeyError as error:
                raise HostError(
                    "user_identity_unavailable", "user_identity", "effective user has no account"
                ) from error
    if not home.is_absolute():
        raise HostError("user_home_invalid", "user_identity", "owning user home is not absolute")
    return scope, home.resolve(strict=False)


def _append_error(
    errors: list[HostDiagnostic],
    code: str,
    stage: str,
    message: str,
    *,
    provider: str | None = None,
    home: str | None = None,
) -> None:
    if len(errors) >= MAX_ERROR_COUNT:
        return
    errors.append(HostDiagnostic(code, stage, message[:4096], provider, home))


def _issue_error(issue: DeclarationIssue) -> HostDiagnostic:
    return HostDiagnostic(
        issue.code,
        "configuration",
        issue.message[:4096],
        issue.provider,
    )


def _load_configuration(
    config_path: Path | str | None,
    *,
    env: Mapping[str, str],
    user_home: Path,
    errors: list[HostDiagnostic],
) -> DirectConfig:
    try:
        config = load_direct_config(config_path, env=env, user_home=user_home)
    except ConfigError:
        _append_error(
            errors,
            "configuration_invalid",
            "configuration",
            "direct configuration is invalid; installed defaults remain visible",
        )
        return DirectConfig(desktop=DesktopConfig(), environments=())
    errors.extend(_issue_error(issue) for issue in config.issues if len(errors) < MAX_ERROR_COUNT)
    return config


def _add_candidate(
    candidates: dict[str, _Candidate],
    *,
    provider: str,
    home: str,
    label: str,
    is_default: bool,
    declared: bool,
) -> None:
    candidate = candidates.get(home)
    if candidate is None:
        candidates[home] = _Candidate(
            provider,
            home,
            label,
            is_default,
            {label} if declared else set(),
        )
        return
    if declared:
        candidate.declared_labels.add(label)
        if len(candidate.declared_labels) == 1:
            candidate.label = label
        elif len(candidate.declared_labels) > 1:
            candidate.conflicting = True
            candidate.label = candidate.label if not candidate.is_default else label


def _provider_candidates(
    provider: str,
    declarations: Sequence[EnvironmentDeclaration],
    *,
    user_home: Path,
    env: Mapping[str, str],
    executable: str | None,
    errors: list[HostDiagnostic],
) -> list[_Candidate]:
    adapter = ADAPTERS[provider]
    try:
        default_path = expand_native_home(adapter.default_home(user_home, env), user_home=user_home)
    except ConfigError:
        default_path = None
        _append_error(
            errors,
            "default_home_invalid",
            "environment",
            "provider-selected default home cannot be resolved",
            provider=provider,
        )
    candidates: dict[str, _Candidate] = {}
    matching = [item for item in declarations if item.provider == provider]
    if default_path is not None and (executable is not None or matching):
        _add_candidate(
            candidates,
            provider=provider,
            home=default_path,
            label=_default_label(provider, default_path),
            is_default=True,
            declared=False,
        )
    for declaration in matching:
        try:
            home = expand_native_home(declaration.home, user_home=user_home)
        except ConfigError:
            _append_error(
                errors,
                "environment_home_invalid",
                "configuration",
                "declared native home cannot be resolved",
                provider=provider,
            )
            continue
        is_default = default_path is not None and home == default_path
        _add_candidate(
            candidates,
            provider=provider,
            home=home,
            label=declaration.label,
            is_default=is_default,
            declared=True,
        )
    return list(candidates.values())


def _default_label(provider: str, home: str) -> str:
    base = Path(home).name
    if provider == "codex" and base == ".codex":
        base = "Default"
    elif provider == "claude" and base == ".claude":
        base = "Default"
    return f"{provider.capitalize()} · {base or 'Home'}"


def _capability_probe(
    provider: str,
    executable: str | None,
    *,
    env: Mapping[str, str],
    user_home: Path,
    runner: Callable[..., ProcessResult],
    now_ms: int,
    deadline: float,
) -> ProbeObservation:
    if executable is None:
        return ProbeObservation("missing", "provider_executable_missing")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return ProbeObservation("unknown", "inventory_deadline")
    adapter = ADAPTERS[provider]
    probe_home = str((user_home / f".{provider}").resolve(strict=False))
    probe_env = adapter.entry_environment(env, probe_home)
    return probe_capability(
        provider,
        adapter.ADAPTER_REVISION,
        executable,
        adapter.capability_argv(executable),
        env=probe_env,
        user_home=user_home,
        runner=runner,
        now_ms=now_ms,
        timeout_seconds=remaining,
    )


def _coverage_status(capability: ProbeObservation) -> tuple[str, str | None]:
    if capability.status == "supported":
        return "complete", None
    if capability.status == "missing":
        return "missing", capability.reason
    if capability.status == "unsupported":
        return "unsupported", capability.reason
    return "unknown", capability.reason


def _capability(
    capability: ProbeObservation, home_problem: str | None, conflict: bool
) -> LaunchCapability:
    if conflict:
        return LaunchCapability("unavailable", "conflicting_declarations")
    if home_problem is not None:
        return LaunchCapability("unavailable", home_problem)
    if capability.status == "missing":
        return LaunchCapability("missing", capability.reason)
    if capability.status == "unsupported":
        return LaunchCapability("unsupported", capability.reason)
    if capability.status != "supported":
        return LaunchCapability("unknown", capability.reason or "capability_unknown")
    return LaunchCapability("supported")


def _daemon_probe(
    provider: str,
    executable: str,
    home: str,
    *,
    env: Mapping[str, str],
    user_home: Path,
    runner: Callable[..., ProcessResult],
    deadline: float,
) -> ProbeObservation:
    adapter = ADAPTERS[provider]
    selected_env = adapter.entry_environment(env, home)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return ProbeObservation("unknown", "inventory_deadline")
    try:
        result = runner(
            adapter.status_argv(executable),
            timeout=min(PROVIDER_STATUS_TIMEOUT_SECONDS, remaining),
            stdout_limit=MAX_PROBE_STDOUT,
            stderr_limit=MAX_PROBE_STDERR,
            cwd=str(user_home),
            env=selected_env,
        )
    except ProcessError as error:
        return ProbeObservation("unknown", error.code)
    except OSError:
        return ProbeObservation("unknown", "status_probe_failed")
    return adapter.daemon_observation(result)


def build_inventory(
    config_path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., ProcessResult] = run_bounded,
    user_scope: UserScope | None = None,
    user_home: Path | str | None = None,
    mesh_executable: str | None = None,
    observed_at: int | None = None,
    include_status: bool = True,
) -> Inventory:
    """Build a finite inventory while preserving valid siblings on failure."""

    process_env = dict(os.environ if env is None else env)
    scope, home = _user_identity(user_scope, user_home)
    when = _now_ms() if observed_at is None else observed_at
    deadline = time.monotonic() + INVENTORY_TIMEOUT_SECONDS
    mesh_path = mesh_executable
    if mesh_path is None:
        mesh_path = shutil.which(MESH_COMMAND, path=process_env.get("PATH", ""))
    try:
        mesh = resolve_self_mesh(mesh_path, runner=runner, env=process_env)
    except HostMeshError as error:
        raise HostError(error.code, "host_identity", str(error)) from error
    errors: list[HostDiagnostic] = []
    config = _load_configuration(config_path, env=process_env, user_home=home, errors=errors)
    targets: list[Target] = []
    coverage: list[ProviderCoverage] = []
    provider_faults: set[str] = set()
    if any(issue.provider is None for issue in config.issues):
        provider_faults.update(ADAPTERS)
    provider_faults.update(issue.provider for issue in config.issues if issue.provider is not None)
    for diagnostic in errors:
        if diagnostic.provider is not None:
            provider_faults.add(diagnostic.provider)

    for provider in ("codex", "claude"):
        adapter = ADAPTERS[provider]
        executable = resolve_executable(adapter.EXECUTABLE, env=process_env)
        if executable is None and not any(
            declaration.provider == provider for declaration in config.environments
        ):
            # Installation absence is a complete discovery observation. Keep
            # coverage for cache invalidation without probing native commands
            # or reporting a fault for an unconfigured provider.
            coverage.append(
                ProviderCoverage(provider, "missing", when, "provider_executable_missing")
            )
            continue
        capability = _capability_probe(
            provider,
            executable,
            env=process_env,
            user_home=home,
            runner=runner,
            now_ms=when,
            deadline=deadline,
        )
        coverage_status, coverage_reason = _coverage_status(capability)
        if capability.status != "supported":
            provider_faults.add(provider)
            error_code = {
                "missing": "provider_executable_missing",
                "unsupported": "provider_manager_unsupported",
                "unknown": "provider_capability_unknown",
            }.get(capability.status)
            if error_code is not None:
                _append_error(
                    errors,
                    error_code,
                    "capability",
                    f"{provider.capitalize()} manager capability is {capability.status}",
                    provider=provider,
                )
        errors_before_candidates = len(errors)
        candidates = _provider_candidates(
            provider,
            config.environments,
            user_home=home,
            env=process_env,
            executable=executable,
            errors=errors,
        )
        if any(item.provider == provider for item in errors[errors_before_candidates:]):
            provider_faults.add(provider)
        if len(targets) + len(candidates) > MAX_TARGET_COUNT:
            _append_error(
                errors,
                "target_limit_exceeded",
                "inventory",
                "host target list exceeds its count limit",
                provider=provider,
            )
            candidates = candidates[: max(0, MAX_TARGET_COUNT - len(targets))]
            provider_faults.add(provider)

        for candidate in candidates:
            home_problem = adapter.home_problem(candidate.home, is_default=candidate.is_default)
            target_capability = _capability(capability, home_problem, candidate.conflicting)
            key = TargetKey(mesh.local.host_id, scope.uid, scope.account, provider, candidate.home)
            spec_revision = canonical_revision(adapter.launch_spec(executable, candidate.home))
            daemon_status = "unknown"
            daemon_reason: str | None
            if home_problem is not None:
                daemon_reason = "environment_unavailable"
                provider_faults.add(provider)
                _append_error(
                    errors,
                    home_problem,
                    "environment",
                    "declared native environment is unavailable or unprepared",
                    provider=provider,
                    home=candidate.home,
                )
            elif candidate.conflicting:
                daemon_reason = "conflicting_declarations"
                provider_faults.add(provider)
                _append_error(
                    errors,
                    "conflicting_declarations",
                    "configuration",
                    "multiple labels declare the same provider environment",
                    provider=provider,
                    home=candidate.home,
                )
            elif include_status and executable is not None and capability.status == "supported":
                observation = _daemon_probe(
                    provider,
                    executable,
                    candidate.home,
                    env=process_env,
                    user_home=home,
                    runner=runner,
                    deadline=deadline,
                )
                daemon_status = observation.status
                daemon_reason = observation.reason
                if daemon_status == "unknown":
                    provider_faults.add(provider)
                    _append_error(
                        errors,
                        observation.reason or "status_probe_failed",
                        "daemon_status",
                        f"{provider.capitalize()} daemon status is unknown",
                        provider=provider,
                        home=candidate.home,
                    )
            else:
                daemon_reason = (
                    "status_not_observed" if not include_status else "provider_unavailable"
                )
            targets.append(
                Target(
                    key,
                    candidate.label,
                    spec_revision,
                    target_capability,
                    Observation(daemon_status, when, daemon_reason),
                )
            )
        if provider in provider_faults and coverage_status == "complete":
            coverage_status, coverage_reason = "partial", "one_or_more_provider_observations_failed"
        coverage.append(ProviderCoverage(provider, coverage_status, when, coverage_reason))

    inventory = Inventory(
        mesh.local.host_id,
        scope,
        when,
        tuple(coverage),
        tuple(targets),
        tuple(errors[:MAX_ERROR_COUNT]),
    )
    return inventory


def _find_target(inventory: Inventory, target_key: TargetKey) -> Target | None:
    return next((target for target in inventory.targets if target.key == target_key), None)


def validate_entry_reference(
    reference: object,
    config_path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., ProcessResult] = run_bounded,
    user_scope: UserScope | None = None,
    user_home: Path | str | None = None,
    mesh_executable: str | None = None,
    observed_at: int | None = None,
) -> EntryPlan:
    """Resolve a stale-resistant entry reference against fresh local facts."""

    if isinstance(reference, str):
        try:
            parsed = decode_reference_json(reference)
        except RecordError as error:
            raise HostError("invalid_reference", "reference", str(error)) from error
    else:
        try:
            parsed = parse_entry_reference(reference)
        except RecordError as error:
            raise HostError("invalid_reference", "reference", str(error)) from error
    inventory = build_inventory(
        config_path,
        env=env,
        runner=runner,
        user_scope=user_scope,
        user_home=user_home,
        mesh_executable=mesh_executable,
        observed_at=observed_at,
        include_status=False,
    )
    if parsed.expected_host_id != inventory.host_id:
        raise HostError(
            "wrong_host", "reference", "entry reference belongs to a different logical host"
        )
    if parsed.expected_user != inventory.user:
        raise HostError(
            "wrong_user", "reference", "entry reference belongs to a different user scope"
        )
    target = _find_target(inventory, parsed.target_key)
    if target is None:
        raise HostError(
            "target_missing", "reference", "referenced native environment is no longer available"
        )
    if target.spec_revision != parsed.spec_revision:
        raise HostError(
            "stale_specification", "reference", "native entry specification has changed"
        )
    if target.capability.status != "supported":
        raise HostError(
            "entry_unavailable",
            "reference",
            f"native manager cannot be entered: {target.capability.reason or target.capability.status}",
        )
    executable = resolve_executable(
        ADAPTERS[target.key.provider].EXECUTABLE, env=dict(os.environ if env is None else env)
    )
    if executable is None:
        raise HostError(
            "provider_executable_missing", "entry", "native provider executable is unavailable"
        )
    adapter = ADAPTERS[target.key.provider]
    return EntryPlan(
        executable,
        adapter.entry_argv(executable),
        adapter.entry_environment(dict(os.environ if env is None else env), target.key.home),
        str(_user_identity(inventory.user, user_home)[1]),
        target,
    )


def enter_reference(
    reference_json: str,
    config_path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., ProcessResult] = run_bounded,
    user_scope: UserScope | None = None,
    user_home: Path | str | None = None,
    mesh_executable: str | None = None,
    is_tty: bool | None = None,
    chdir: Callable[[str], object] = os.chdir,
    execve: Callable[[str, Sequence[str], Mapping[str, str]], object] = os.execve,
) -> EntryPlan | None:
    """Validate the exact target, require a real TTY, then replace this process."""

    tty_ready = (
        sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()
        if is_tty is None
        else is_tty
    )
    if not tty_ready:
        raise HostError("tty_required", "entry", "native manager entry requires an attached TTY")
    plan = validate_entry_reference(
        reference_json,
        config_path,
        env=env,
        runner=runner,
        user_scope=user_scope,
        user_home=user_home,
        mesh_executable=mesh_executable,
    )
    try:
        chdir(plan.cwd)
        execve(plan.executable, plan.argv, plan.environment)
    except OSError as error:
        raise HostError(
            "provider_exec_failed", "entry", "native manager could not be started"
        ) from error
    if execve is os.execve:
        raise HostError(
            "provider_exec_returned", "entry", "native manager process replacement returned"
        )
    return plan


def _error_document(
    error: HostError | RecordError | ConfigError | ProcessError,
) -> dict[str, object]:
    if isinstance(error, HostError):
        code, stage, message = error.code, error.stage, str(error)
    elif isinstance(error, ProcessError):
        code, stage, message = error.code, "process", str(error)
    elif isinstance(error, ConfigError):
        code, stage, message = "configuration_invalid", "configuration", str(error)
    else:
        code, stage, message = "invalid_reference", "reference", str(error)
    return {
        "schemaVersion": 1,
        "ok": False,
        "error": {"code": code, "stage": stage, "message": message[:4096]},
    }


def _write_document(stream: object, value: object) -> None:
    raw = encode_document(value)
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(raw)
        binary.flush()
    else:
        stream.write(raw.decode("utf-8"))
        stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rofi-agent-plus-host")
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory", help="inspect local native environments")
    inventory.add_argument(
        "--json", action="store_true", required=True, help="emit the v1 JSON inventory"
    )
    inventory.add_argument("--config", type=Path, help="read a private direct.toml fixture")
    enter = commands.add_parser("enter", help="enter one freshly validated native manager")
    enter.add_argument("--reference-json", required=True, help="bounded v1 entry reference JSON")
    enter.add_argument("--config", type=Path, help="read a private direct.toml fixture")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "inventory":
            with passive_probe_signals():
                inventory = build_inventory(arguments.config)
                _write_document(sys.stdout, inventory)
            return 0
        enter_reference(arguments.reference_json, arguments.config)
        return 0
    except (HostError, RecordError, ConfigError, ProcessError) as error:
        try:
            _write_document(
                sys.stderr if arguments.command == "enter" else sys.stdout, _error_document(error)
            )
        except (OSError, UnicodeError):
            pass
        return 2
    except OSError:
        error = HostError("host_operation_failed", arguments.command, "host operation failed")
        _write_document(
            sys.stderr if arguments.command == "enter" else sys.stdout, _error_document(error)
        )
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
