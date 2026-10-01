"""Strict consumer for Tmux Plus's endpoint viewer operations.

Agent Plus owns no terminal, compositor, SSH, or raw tmux behavior here.
Malformed replies stop a batch; typed target failures can be reported without
retrying an operation whose outcome may be ambiguous.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from . import engine
from .contract_backend import CommandOutput
from .contract_lifecycle import StableReference, _descriptor, _output_bytes
from .wire import WireError, decode_document, validate_string_bounds

MAX_VIEWERS = 512
MAX_OUTPUT = 256 * 1024
MAX_FIELD = 4096
STATUSES = frozenset({"none", "verified", "unverified", "ambiguous", "unsupported"})
_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", re.ASCII)


class ViewerError(engine.PickerError):
    """A target failure, with authority/protocol failures distinguished."""

    def __init__(self, code: str, message: str, *, stop_batch: bool = False) -> None:
        self.code = code
        self.stop_batch = stop_batch
        super().__init__(message)


class ViewerBackend(Protocol):
    tmux_command: str

    def _run(self, argv: list[str], **kwargs: object) -> CommandOutput: ...


@dataclass(frozen=True)
class Viewer:
    viewer_id: str
    window_id: int


@dataclass(frozen=True)
class ViewerInspection:
    status: str
    viewers: tuple[Viewer, ...]
    close_safe: bool
    reason: str = ""


def _invalid() -> ViewerError:
    return ViewerError("invalid_response", "Tmux Plus viewer response is invalid", stop_batch=True)


def _text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_FIELD
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise _invalid()
    return value


def _ref_tuple(reference: StableReference) -> tuple[object, ...]:
    return (
        reference.host_id,
        reference.server_generation,
        reference.session_id,
        reference.created_at,
    )


def _reference_reply(value: object, reference: StableReference) -> None:
    if not isinstance(value, Mapping):
        raise _invalid()
    created_at = value.get("createdAt")
    if isinstance(created_at, bool) or not isinstance(created_at, int):
        raise _invalid()
    if (
        value.get("hostId"),
        value.get("serverGeneration"),
        value.get("sessionId"),
        created_at,
    ) != _ref_tuple(reference):
        raise _invalid()


def _arguments(
    backend: ViewerBackend,
    command: str,
    reference: StableReference,
    required_option: tuple[str, str] | None,
) -> list[str]:
    arguments = [backend.tmux_command, command, "--json", "--host", reference.host_id]
    if reference.mesh_revision is not None:
        arguments.extend(("--mesh-revision", reference.mesh_revision))
    arguments.extend(
        (
            "--server-generation",
            reference.server_generation,
            "--session-id",
            reference.session_id,
            "--created-at",
            str(reference.created_at),
        )
    )
    if reference.observed_name is not None:
        arguments.extend(("--expected-name", reference.observed_name))
    if required_option is not None:
        arguments.extend(("--require-option", "=".join(required_option)))
    return arguments


def _response(
    backend: ViewerBackend,
    arguments: list[str],
    reference: StableReference,
    timeout: float,
) -> Mapping[str, object]:
    try:
        output = backend._run(
            arguments,
            timeout=timeout,
            stdout_limit=MAX_OUTPUT,
            stderr_limit=64 * 1024,
        )
    except (engine.PickerError, OSError) as error:
        raise ViewerError("operation_failed", "Tmux Plus viewer command failed") from error
    if output.timed_out or output.returncode < 0:
        raise ViewerError("operation_failed", "Tmux Plus viewer command ended without a result")
    try:
        payload = decode_document(_output_bytes(output), limit=MAX_OUTPUT)
        validate_string_bounds(payload, limit=MAX_FIELD)
    except (WireError, UnicodeError, RecursionError) as error:
        raise _invalid() from error
    if (
        not isinstance(payload, Mapping)
        or type(payload.get("schemaVersion")) is not int
        or payload.get("schemaVersion") != 1
        or not isinstance(payload.get("ok"), bool)
    ):
        raise _invalid()
    if output.returncode != 0:
        error = payload.get("error")
        if payload["ok"] is not False or not isinstance(error, Mapping):
            raise _invalid()
        code = _text(error.get("code"))
        if not _ERROR_CODE.fullmatch(code):
            raise _invalid()
        message = _text(error.get("message"))
        if error.get("hostId") not in (None, reference.host_id):
            raise _invalid()
        raise ViewerError(code, message, stop_batch=code == "stale_mesh")
    if (
        payload["ok"] is not True
        or "meshRevision" not in payload
        or payload["meshRevision"] != reference.mesh_revision
    ):
        raise _invalid()
    return payload


def inspect_viewers(
    backend: ViewerBackend,
    reference: StableReference,
    *,
    required_option: tuple[str, str] | None = None,
    timeout: float = 15,
) -> ViewerInspection:
    """Inspect viewers here for one current, guarded existing session."""
    payload = _response(
        backend, _arguments(backend, "viewers", reference, required_option), reference, timeout
    )
    _reference_reply(payload.get("sessionRef"), reference)
    status = payload.get("status")
    raw = payload.get("viewers")
    if (
        not isinstance(status, str)
        or status not in STATUSES
        or not isinstance(raw, list)
        or len(raw) > MAX_VIEWERS
        or not isinstance(payload.get("closeSafe"), bool)
    ):
        raise _invalid()
    viewers: list[Viewer] = []
    seen: set[str] = set()
    windows: set[int] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise _invalid()
        identifier = _text(item.get("viewerId"))
        window = item.get("windowId")
        if (
            identifier in seen
            or type(window) is not int
            or not 0 <= window <= 2**63 - 1
            or window in windows
        ):
            raise _invalid()
        seen.add(identifier)
        windows.add(window)
        viewers.append(Viewer(identifier, window))
    if (status == "verified") != bool(viewers):
        raise _invalid()
    reason = _text(payload["reason"]) if "reason" in payload else ""
    return ViewerInspection(status, tuple(viewers), payload["closeSafe"], reason)


def close_viewer(
    backend: ViewerBackend,
    reference: StableReference,
    viewer_id: str,
    *,
    required_option: tuple[str, str] | None = None,
    timeout: float = 15,
) -> bool:
    """Close one frozen viewer; return true when this request closed it."""
    arguments = _arguments(backend, "close-viewer", reference, required_option)
    arguments.extend(("--viewer-id", _text(viewer_id)))
    payload = _response(backend, arguments, reference, timeout)
    _reference_reply(payload.get("sessionRef"), reference)
    closed, already = payload.get("closed"), payload.get("alreadyClosed")
    if (
        payload.get("viewerId") != viewer_id
        or not isinstance(closed, bool)
        or not isinstance(already, bool)
        or closed == already
    ):
        raise _invalid()
    return closed


def open_existing_viewer(
    backend: ViewerBackend,
    reference: StableReference,
    *,
    required_option: tuple[str, str] | None = None,
    timeout: float = 15,
) -> bool:
    """Strictly reuse/attach an existing session, never create a provider."""
    arguments = _arguments(backend, "open", reference, required_option)
    arguments.append("--verified-viewer")
    payload = _response(backend, arguments, reference, timeout)
    try:
        result = _descriptor(payload.get("session"), reference.host_id, reference.mesh_revision)
    except engine.PickerError as error:
        raise _invalid() from error
    focused, launched = payload.get("focused"), payload.get("terminalLaunched")
    if (
        _ref_tuple(result) != _ref_tuple(reference)
        or not isinstance(focused, bool)
        or not isinstance(launched, bool)
        or focused == launched
    ):
        raise _invalid()
    if "viewerId" in payload:
        _text(payload["viewerId"])
    return launched
