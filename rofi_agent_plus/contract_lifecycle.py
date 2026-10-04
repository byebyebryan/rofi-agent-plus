"""Public Tmux Session v1 lifecycle consumer for Agent Plus.

This module deliberately consumes only the public ``rofi-tmux-plus`` JSON
commands.  It owns no SSH, terminal, Niri, or raw tmux behavior; those remain
behind the Tmux Session boundary.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from . import engine
from .cache import CacheStore, PresentationContext
from .config import PickerConfig
from .contract_backend import CommandOutput, StaleMeshError
from .observer_write import WriteBoundaryError, encoded, request_for
from .session_identity import logical_identity
from .wire import WireError, decode_document, validate_string_bounds

_SCHEMA_VERSION = 1
_CAPABILITY = "host-mesh-v1+tmux-session-v1"
_MAX_FIELD = 4096
_MAX_OUTPUT = 256 * 1024
_LIFECYCLE_SECONDS = 60.0
_MAX_CREATE_COLLISIONS = 8
_SESSION_ID = re.compile(r"\$[0-9]+", re.ASCII)
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*", re.ASCII)
_REVISION = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
_GENERATION = re.compile(r"^[^\x00-\x1f\x7f-\x9f]+$", re.ASCII)
_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$", re.ASCII)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", re.ASCII)
_PROVIDER_OPTIONS = {
    "codex": ("@codex_thread_id", "@codex_name"),
    "claude": ("@claude_session_id", "@claude_name"),
}


class LifecycleError(engine.PickerError):
    """A bounded, typed failure returned by Tmux Session v1."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _ContractBackend(Protocol):
    tmux_command: str

    def _run(self, argv: Sequence[str], **kwargs: object) -> CommandOutput: ...


@dataclass(frozen=True)
class StableReference:
    host_id: str
    mesh_revision: str | None
    server_generation: str
    session_id: str
    created_at: int
    observed_name: str | None

    def payload(self) -> dict[str, object]:
        return {
            "meshRevision": self.mesh_revision,
            "serverGeneration": self.server_generation,
            "sessionId": self.session_id,
            "createdAt": self.created_at,
            "observedName": self.observed_name,
        }


def _text(value: object, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_FIELD
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise LifecycleError("operation_failed", f"Tmux Session {label} is invalid")
    return value


def _nonnegative(value: object, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise LifecycleError("operation_failed", f"Tmux Session {label} is invalid")
    return value


def _count(value: object, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**31 - 1:
        raise LifecycleError("operation_failed", f"Tmux Session {label} is invalid")
    return value


def _required(value: Mapping[str, object], *fields: str) -> None:
    if any(field not in value for field in fields):
        raise LifecycleError("operation_failed", "Tmux Session response omitted a required field")


def _output_bytes(output: CommandOutput) -> bytes:
    raw = output.stdout_bytes
    if raw is not None:
        if not isinstance(raw, bytes):
            raise WireError("stdout is not bytes")
        return raw
    value = output.stdout
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        raise WireError("stdout is not bytes")
    return value.encode("utf-8", "strict")


def _generation(value: object, label: str = "server generation") -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_FIELD
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise LifecycleError("operation_failed", f"Tmux Session {label} is invalid")
    return value


def _error_code(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or not _ERROR_CODE.fullmatch(value)
    ):
        raise LifecycleError("operation_failed", "Tmux Session error code is invalid")
    return value


def _backend_identity(value: object) -> dict[str, object]:
    if (
        not isinstance(value, Mapping)
        or value.get("kind") != "contract"
        or value.get("capability") != _CAPABILITY
    ):
        raise LifecycleError("operation_failed", "contract action lacks a current authority")
    revision = value.get("meshRevision")
    if revision is not None and not isinstance(revision, str):
        raise LifecycleError("operation_failed", "contract action has an invalid mesh revision")
    if isinstance(revision, str) and not _REVISION.fullmatch(revision):
        raise LifecycleError("operation_failed", "contract action has an invalid mesh revision")
    return {"kind": "contract", "capability": _CAPABILITY, "meshRevision": revision}


def _reference(value: object, host_id: str, revision: str | None) -> StableReference:
    if not isinstance(value, Mapping):
        raise LifecycleError("operation_failed", "session no longer has a current tmux reference")
    _required(value, "meshRevision", "serverGeneration", "sessionId", "createdAt")
    if value["meshRevision"] != revision:
        raise LifecycleError("operation_failed", "session no longer has a current tmux reference")
    generation = _generation(value["serverGeneration"])
    session_id = value.get("sessionId")
    created_at = _nonnegative(value["createdAt"], "creation time")
    observed_name = _text(value.get("observedName"), "observed name", nullable=True)
    if (
        not isinstance(session_id, str)
        or len(session_id) > 4096
        or not _SESSION_ID.fullmatch(session_id)
    ):
        raise LifecycleError("operation_failed", "session no longer has a valid tmux reference")
    assert generation is not None and created_at is not None
    return StableReference(host_id, revision, generation, session_id, created_at, observed_name)


def _provider_row(
    snapshot: Mapping[str, object],
    identity: Mapping[str, object],
    selection: Mapping[str, object],
    *,
    require_tmux_and_activity_evidence: bool = True,
) -> dict[str, object]:
    """Resolve one fresh provider row by authority, host, provider, and ID.

    Resume needs its old wrapper's current Tmux and activity evidence before
    it can focus, attach, or decide to create a provider-resume wrapper. A
    fresh native provider launch only needs the provider's current source
    identity and directory, so it deliberately does not inherit those old
    wrapper requirements.
    """

    if _backend_identity(selection.get("backend")) != dict(identity):
        raise LifecycleError(
            "operation_failed", "selected session belongs to an older Mesh revision"
        )
    host_id = selection.get("hostId")
    kind = selection.get("kind")
    identifier = selection.get("id")
    if (
        not isinstance(host_id, str)
        or len(host_id) > _MAX_FIELD
        or not _HOST_ID.fullmatch(host_id)
        or kind not in _PROVIDER_OPTIONS
        or not isinstance(identifier, str)
        or logical_identity(selection) is None
    ):
        raise LifecycleError("operation_failed", "selected contract session is invalid")
    if snapshot.get("backend") != dict(identity):
        raise LifecycleError("operation_failed", "contract refresh changed authority")
    rows = snapshot.get("sessions")
    if not isinstance(rows, list):
        raise LifecycleError("operation_failed", "contract refresh returned invalid rows")
    matched = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and row.get("contractMode") is True
        and row.get("backend") == dict(identity)
        and row.get("hostId") == host_id
        and row.get("kind") == kind
        and row.get("id") == identifier
        and logical_identity(row) == logical_identity(selection)
    ]
    if len(matched) != 1:
        raise LifecycleError(
            "operation_failed", "selected provider session is no longer unambiguous"
        )
    row = matched[0]
    errors = snapshot.get("errors")
    stale_stages = {"codex": "threads", "claude": "claude"}
    if isinstance(errors, list):
        host_stages = {
            error.get("stage")
            for error in errors
            if isinstance(error, Mapping) and error.get("host") == host_id
        }
        if stale_stages[str(kind)] in host_stages and not (
            isinstance(row.get("observerSource"), Mapping)
            and row.get("sourceObservation") == "current"
        ):
            raise LifecycleError("operation_failed", "selected session has stale provider state")
        if require_tmux_and_activity_evidence and not isinstance(
            row.get("observerSource"), Mapping
        ):
            # A normal ``tmux_missing`` observation is authoritative
            # capability data and a provider wrapper may safely be created
            # there. A generic tmux-stage error is not: an absent association
            # then means unknown, not proof that no compatible wrapper exists.
            if "tmux" in host_stages:
                raise LifecycleError("operation_failed", "selected host has unknown tmux inventory")
            if "active" in host_stages:
                raise LifecycleError(
                    "operation_failed", "selected session has stale provider state"
                )
    if require_tmux_and_activity_evidence and not isinstance(row.get("observerSource"), Mapping):
        if row.get("tmuxStale") or row.get("tmuxAmbiguous"):
            raise LifecycleError(
                "operation_failed", "selected session has stale or ambiguous tmux state"
            )
        if row.get("activityState") == "unknown":
            raise LifecycleError(
                "operation_failed", "selected session has unknown provider activity"
            )
    return row


def _descriptor(value: object, host_id: str, revision: str | None) -> StableReference:
    """Validate the public complete descriptor and project its stable ref."""

    if not isinstance(value, Mapping):
        raise LifecycleError("operation_failed", "Tmux Session response has an invalid descriptor")
    _required(
        value,
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
    )
    if value["hostId"] != host_id:
        raise LifecycleError("operation_failed", "Tmux Session response has an invalid descriptor")
    if (
        not isinstance(value["hostId"], str)
        or len(value["hostId"]) > _MAX_FIELD
        or not _HOST_ID.fullmatch(value["hostId"])
    ):
        raise LifecycleError("operation_failed", "Tmux Session response has an invalid descriptor")
    generation = _generation(value["serverGeneration"])
    session_id = value.get("sessionId")
    created_at = _nonnegative(value["createdAt"], "creation time")
    name = _text(value["name"], "session name", nullable=True)
    if (
        not isinstance(session_id, str)
        or len(session_id) > 4096
        or not _SESSION_ID.fullmatch(session_id)
    ):
        raise LifecycleError("operation_failed", "Tmux Session response has an invalid descriptor")
    for field in ("activityAt", "lastAttachedAt"):
        _nonnegative(value.get(field), field, nullable=True)
    for field in ("attachedClients", "windowCount"):
        _count(value.get(field), field, nullable=True)
    if not isinstance(value.get("pending"), bool):
        raise LifecycleError("operation_failed", "Tmux Session pending marker is invalid")
    for field in ("sessionPath", "currentWindow", "currentPath"):
        _text(value.get(field), field, nullable=True)
    if "panes" in value:
        panes = value["panes"]
        if not isinstance(panes, list) or len(panes) > 512:
            raise LifecycleError("operation_failed", "Tmux Session panes are invalid")
        for pane in panes:
            if not isinstance(pane, Mapping):
                raise LifecycleError("operation_failed", "Tmux Session pane is invalid")
            _required(pane, "paneId", "pid", "currentPath", "currentCommand")
            pane_id = pane["paneId"]
            if (
                not isinstance(pane_id, str)
                or len(pane_id) > 4096
                or not re.fullmatch(r"%[0-9]+", pane_id)
            ):
                raise LifecycleError("operation_failed", "Tmux Session pane is invalid")
            _count(pane["pid"], "pane pid", nullable=True)
            _text(pane["currentPath"], "pane path", nullable=True)
            _text(pane["currentCommand"], "pane command", nullable=True)
    if "options" in value:
        options = value["options"]
        if not isinstance(options, Mapping):
            raise LifecycleError("operation_failed", "Tmux Session options are invalid")
        for option, option_value in options.items():
            if (
                not isinstance(option, str)
                or len(option) > _MAX_FIELD
                or not re.fullmatch(r"@[A-Za-z0-9_.-]+", option)
            ):
                raise LifecycleError("operation_failed", "Tmux Session option is invalid")
            _text(option_value, "session option", nullable=True)
    assert created_at is not None
    return StableReference(host_id, revision, generation, session_id, created_at, name)


def _error_response(output: CommandOutput) -> LifecycleError:
    try:
        payload = decode_document(_output_bytes(output), limit=_MAX_OUTPUT)
        validate_string_bounds(payload, limit=_MAX_FIELD)
    except (WireError, UnicodeEncodeError):
        return LifecycleError("operation_failed", "Tmux Session command failed")
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != _SCHEMA_VERSION
        or isinstance(payload.get("schemaVersion"), bool)
        or not isinstance(payload.get("schemaVersion"), int)
        or payload.get("ok") is not False
        or not isinstance(payload.get("error"), Mapping)
    ):
        return LifecycleError("operation_failed", "Tmux Session command failed")
    error = payload["error"]
    if "code" not in error or "message" not in error:
        return LifecycleError("operation_failed", "Tmux Session command failed")
    try:
        code = _error_code(error["code"])
        clean = _text(error["message"], "error message")
        host_id = error.get("hostId")
        if host_id is not None:
            if (
                not isinstance(host_id, str)
                or not _HOST_ID.fullmatch(host_id)
                or len(host_id) > _MAX_FIELD
            ):
                raise LifecycleError("operation_failed", "Tmux Session error host is invalid")
    except LifecycleError:
        return LifecycleError("operation_failed", "Tmux Session command failed")
    assert clean is not None
    return LifecycleError(code, clean)


def _success_response(
    output: CommandOutput,
    host_id: str,
    revision: str | None,
    *,
    opening: bool,
) -> StableReference:
    try:
        payload = decode_document(_output_bytes(output), limit=_MAX_OUTPUT)
        validate_string_bounds(payload, limit=_MAX_FIELD)
    except (WireError, UnicodeEncodeError) as error:
        raise LifecycleError("operation_failed", "Tmux Session response is invalid") from error
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != _SCHEMA_VERSION
        or isinstance(payload.get("schemaVersion"), bool)
        or not isinstance(payload.get("schemaVersion"), int)
        or payload.get("ok") is not True
        or payload.get("meshRevision") != revision
    ):
        raise LifecycleError("operation_failed", "Tmux Session response is invalid")
    if "meshRevision" not in payload or "session" not in payload:
        raise LifecycleError("operation_failed", "Tmux Session response is invalid")
    reference = _descriptor(payload["session"], host_id, revision)
    if opening:
        if "focused" not in payload or "terminalLaunched" not in payload:
            raise LifecycleError("operation_failed", "Tmux Session open response is invalid")
        focused = payload.get("focused")
        launched = payload.get("terminalLaunched")
        if not isinstance(focused, bool) or not isinstance(launched, bool) or focused == launched:
            raise LifecycleError("operation_failed", "Tmux Session open response is invalid")
    return reference


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise LifecycleError("operation_failed", "contract lifecycle timed out")
    return value


def _run_json(
    backend: _ContractBackend,
    argv: Sequence[str],
    deadline: float,
    host_id: str,
    revision: str | None,
    *,
    opening: bool,
) -> StableReference:
    try:
        output = backend._run(
            argv,
            timeout=min(15.0, _remaining(deadline)),
            stdout_limit=_MAX_OUTPUT,
            stderr_limit=64 * 1024,
        )
    except (engine.PickerError, OSError) as error:
        raise LifecycleError("operation_failed", "Tmux Session command failed") from error
    if output.timed_out:
        raise LifecycleError("operation_failed", "Tmux Session command timed out")
    if output.returncode < 0:
        raise LifecycleError("operation_failed", "Tmux Session command was terminated by a signal")
    if output.returncode != 0:
        raise _error_response(output)
    return _success_response(output, host_id, revision, opening=opening)


def _open_reference(
    backend: _ContractBackend,
    reference: StableReference,
    deadline: float,
    *,
    required_option: tuple[str, str] | None = None,
    expected_name: bool = False,
) -> StableReference:
    """Open one stable reference through the public Tmux contract.

    ``required_option`` is used only by the discovery-proven fast path.  The
    producer checks it before focusing or launching, so a missing or changed
    provider option is a typed no-action failure.  The ordinary lifecycle path
    intentionally keeps its historical no-precondition open call because it
    has already performed a fresh selected-host revalidation.
    """

    argv = [
        backend.tmux_command,
        "open",
        "--json",
        "--host",
        reference.host_id,
    ]
    if reference.mesh_revision is not None:
        argv.extend(("--mesh-revision", reference.mesh_revision))
    argv.extend(
        (
            "--server-generation",
            reference.server_generation,
            "--session-id",
            reference.session_id,
            "--created-at",
            str(reference.created_at),
        )
    )
    if expected_name and reference.observed_name is not None:
        argv.extend(("--expected-name", reference.observed_name))
    if required_option is not None:
        option_name, option_value = required_option
        argv.extend(("--require-option", f"{option_name}={option_value}"))
    result = _run_json(
        backend,
        argv,
        deadline,
        reference.host_id,
        reference.mesh_revision,
        opening=True,
    )
    # Open may legitimately observe a new name, but it must never return a
    # different stable tmux identity than the one this typed provider row
    # selected.  Otherwise a malformed or confused producer response could be
    # reconciled into the wrong provider cache entry.
    if (
        result.host_id,
        result.mesh_revision,
        result.server_generation,
        result.session_id,
        result.created_at,
    ) != (
        reference.host_id,
        reference.mesh_revision,
        reference.server_generation,
        reference.session_id,
        reference.created_at,
    ):
        raise LifecycleError("operation_failed", "Tmux Session open returned another session")
    return result


def fast_open_selection(
    selection: Mapping[str, object],
    *,
    backend: _ContractBackend | None = None,
    timeout: float = _LIFECYCLE_SECONDS,
) -> None:
    """Require a fresh native observation before selecting an attachment.

    Historical launch options cannot establish the conversation displayed by
    a TUI after native /new or resume navigation.
    """
    raise LifecycleError(
        "unsupported_binding", "current conversation binding requires fresh revalidation"
    )


def _safe_wrapper_name(display_name: object, kind: str, identifier: str, attempt: int) -> str:
    cleaned = (
        re.sub(r"[^A-Za-z0-9_-]+", "-", display_name.strip()).strip("-_")
        if isinstance(display_name, str)
        and len(display_name) <= _MAX_FIELD
        and not any(unicodedata.category(char).startswith("C") for char in display_name)
        else ""
    )
    suffix = identifier[:12].casefold()
    base = cleaned[:48] or f"agent-{kind}-{suffix}"
    candidate = base if attempt == 0 else f"{base}-{attempt + 1}"
    if not _NAME.fullmatch(candidate):
        raise LifecycleError("operation_failed", "cannot build a safe tmux wrapper name")
    return candidate


def _display_name(row: Mapping[str, object], identifier: str) -> str:
    value = row.get("name")
    if (
        isinstance(value, str)
        and value
        and len(value) <= _MAX_FIELD
        and not any(unicodedata.category(char).startswith("C") for char in value)
    ):
        return value
    return identifier


def _absolute_provider_cwd(value: object) -> str | None:
    """Accept only the exact absolute cwd reported by a provider refresh."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_FIELD
        or any(unicodedata.category(char).startswith("C") for char in value)
        or not PurePosixPath(value).is_absolute()
    ):
        return None
    return value


def _new_session_wrapper_name(cwd: str, kind: str, attempt: int) -> str:
    """Build a bounded ``directory-provider[-N]`` Tmux wrapper name."""

    basename = PurePosixPath(cwd).name or "root"
    stem = re.sub(r"[^A-Za-z0-9]+", "-", basename).strip("-") or "cwd"
    suffix = "" if attempt == 0 else f"-{attempt + 1}"
    maximum_stem = 96 - len(kind) - len(suffix) - 1
    candidate = f"{stem[:maximum_stem]}-{kind}{suffix}"
    if not _NAME.fullmatch(candidate):
        raise LifecycleError("operation_failed", "cannot build a safe tmux wrapper name")
    return candidate


class ContractLifecycle:
    """Revalidate and hand an Agent Plus selection to Tmux Plus exactly once."""

    def __init__(
        self,
        store: CacheStore,
        config: PickerConfig,
        context: PresentationContext,
        *,
        timeout: float = _LIFECYCLE_SECONDS,
    ) -> None:
        if context.error is not None or context.selected is None:
            raise LifecycleError("operation_failed", "contract action has no selected authority")
        self.store = store
        self.config = config
        self.context = context
        self.identity = _backend_identity(context.backend)
        self.backend = context.selected
        # Protocols are intentionally static-only here.  Validate the public
        # executable seam at runtime instead of trusting an arbitrary object.
        if not (
            isinstance(getattr(self.backend, "tmux_command", None), str)
            and callable(getattr(self.backend, "_run", None))
        ):
            raise LifecycleError("operation_failed", "contract lifecycle backend is unavailable")
        self.deadline = time.monotonic() + timeout

    def _refresh_row(
        self,
        selection: Mapping[str, object],
        *,
        whole_mesh: bool = False,
        propagate_stale_mesh: bool = False,
        source_only: bool = False,
    ) -> dict[str, object]:
        try:
            host_id = selection.get("hostId")
            if (
                not isinstance(host_id, str)
                or len(host_id) > _MAX_FIELD
                or not _HOST_ID.fullmatch(host_id)
            ):
                raise LifecycleError("operation_failed", "selected contract session is invalid")
            context = self.context
            if whole_mesh:
                try:
                    self.backend.prepare(deadline=self.deadline)  # type: ignore[attr-defined]
                except TypeError:
                    # Keep narrow fake backends and older injected adapters
                    # usable while the real ContractBackend consumes the
                    # enclosing action deadline.
                    self.backend.prepare()  # type: ignore[attr-defined]
                self.identity = _backend_identity(self.backend.identity)  # type: ignore[attr-defined]
                context = PresentationContext(
                    self.config.fingerprint,
                    dict(self.identity),
                    selected=self.backend,
                )
                self.context = context
            snapshot = self.store.refresh(
                self.config,
                force=True,
                require_fresh=True,
                context=context,
                deadline=self.deadline,
                host_ids=None if whole_mesh else (host_id,),
                retry_stale_mesh=not propagate_stale_mesh,
            )
        except StaleMeshError as error:
            raise LifecycleError("stale_mesh", str(error)) from error
        except engine.PickerError as error:
            raise LifecycleError("operation_failed", str(error)) from error
        lookup = dict(selection)
        if whole_mesh:
            lookup["backend"] = dict(self.identity)
        return _provider_row(
            snapshot,
            self.identity,
            lookup,
            require_tmux_and_activity_evidence=not source_only,
        )

    def _create_handoff(
        self, row: Mapping[str, object], result: Mapping[str, object]
    ) -> StableReference:
        """Present a validated public TTY plan; never construct provider argv."""
        plan = result["handoff"]
        request = plan["request"]
        host_id, kind, cwd = request["hostScope"], request["provider"], request["cwd"]
        actual = result["resultingIdentity"]
        identifier = actual["nativeId"] if actual is not None else None
        revision = self.identity["meshRevision"]
        for collision in range(_MAX_CREATE_COLLISIONS):
            wrapper_name = (
                _safe_wrapper_name(row.get("name"), kind, identifier, collision)
                if identifier
                else _new_session_wrapper_name(cwd, kind, collision)
            )
            argv = [self.backend.tmux_command, "create", "--json", "--host", host_id]
            if revision is not None:
                argv.extend(("--mesh-revision", str(revision)))
            argv.extend(("--name", wrapper_name, "--cwd", cwd))
            if identifier is not None:
                option_id, option_name = _PROVIDER_OPTIONS[kind]
                argv.extend(
                    (
                        "--set-option",
                        f"{option_id}={identifier}",
                        "--set-option",
                        f"{option_name}={_display_name(row, identifier)}",
                    )
                )
            argv.extend(
                (
                    "--defer-until-attached",
                    "--open",
                    "--",
                    "agent-observer-write",
                    "enter",
                    "--plan-json",
                    encoded(plan).decode(),
                )
            )
            try:
                return _run_json(self.backend, argv, self.deadline, host_id, revision, opening=True)
            except LifecycleError as error:
                # A typed name collision is a pre-create rejection. Only the
                # viewer attempt repeats, using the same plan and provider effect.
                if error.code == "session_exists" and collision + 1 < _MAX_CREATE_COLLISIONS:
                    continue
                raise
        raise LifecycleError(
            "operation_failed", "could not choose a collision-free tmux wrapper name"
        )

    def _perform(self, row: Mapping[str, object], operation: str) -> None:
        writer = getattr(self.backend, "observer_write", None)
        if not callable(writer):
            raise LifecycleError("operation_failed", "Observer write client is unavailable")
        try:
            request = request_for(row, operation)
            result = writer(request, deadline=self.deadline)
        except StaleMeshError as error:
            # ContractBackend exposes stale authority only during passive
            # preparation. Provider execution converts all failures to terminal
            # effects, so this can safely refresh authority before one dispatch.
            raise LifecycleError("stale_mesh", str(error)) from error
        except (engine.PickerError, WriteBoundaryError, OSError) as error:
            raise LifecycleError("operation_failed", str(error)) from error
        try:
            self._create_handoff(row, result)
        except (LifecycleError, engine.PickerError, OSError) as error:
            effect = result["effect"]
            message = (
                "Session created or resumed; attachment failed and provider work was preserved"
                if effect == "confirmed"
                else "Native entry handoff failed; no write retry was attempted"
            )
            # No outer stale-Mesh recovery after execution or TTY presentation.
            raise LifecycleError("operation_failed", message) from error

    def _new_session_row(
        self, selection: Mapping[str, object], *, whole_mesh: bool = False
    ) -> dict[str, object]:
        selected_cwd = _absolute_provider_cwd(selection.get("cwd"))
        if selected_cwd is None:
            raise LifecycleError("operation_failed", "selected session has no absolute directory")
        row = self._refresh_row(
            selection, whole_mesh=whole_mesh, propagate_stale_mesh=True, source_only=True
        )
        if (
            row.get("sourceObservation") != "current"
            or _absolute_provider_cwd(row.get("cwd")) != selected_cwd
        ):
            raise LifecycleError(
                "operation_failed", "selected directory context changed; choose it again"
            )
        return row

    def new_session_here(self, selection: Mapping[str, object]) -> None:
        def attempt(whole_mesh: bool) -> None:
            row = self._new_session_row(selection, whole_mesh=whole_mesh)
            self._perform(row, "new")

        try:
            attempt(False)
        except LifecycleError as error:
            if error.code != "stale_mesh":
                raise
            attempt(True)

    def open_or_create(self, selection: Mapping[str, object]) -> None:
        def attempt(whole_mesh: bool) -> None:
            row = self._refresh_row(
                selection, whole_mesh=whole_mesh, propagate_stale_mesh=True, source_only=True
            )
            self._perform(row, "resume")

        try:
            attempt(False)
        except LifecycleError as error:
            if error.code != "stale_mesh":
                raise
            attempt(True)
