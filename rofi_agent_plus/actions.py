"""Replay-safe direct Open and focus actions for dedicated Kitty windows."""

from __future__ import annotations

import os
import pwd
import re
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .desktop import (
    Desktop,
    DesktopError,
    ProcessIdentity,
    Window,
    kitty_command,
    observe_process,
    validate_kitty_layout,
)
from .model import EntryReference, RecordError, Target, parse_entry_reference
from .registry import Instance, Registry, RegistryError, target_storage_key
from .transport import Transport, TransportError

_REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")


class ActionError(RuntimeError):
    """A stable failure before or during local terminal dispatch."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _artifact_root() -> Path:
    return Path(__file__).resolve().parent.parent


class Actions:
    """Open a host target or focus one validated existing local instance."""

    def __init__(
        self,
        desktop: Desktop,
        registry: Registry,
        transport: Transport,
        *,
        terminal: Sequence[str] = ("kitty",),
        env: Mapping[str, str] | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        observe: Callable[[int], ProcessIdentity | None] = observe_process,
    ):
        self.desktop = desktop
        self.registry = registry
        self.transport = transport
        self.terminal = tuple(terminal)
        self.env = dict(desktop.env if env is None else env)
        self.popen = popen
        self.observe = observe
        if not self.terminal:
            raise ActionError("terminal_unavailable", "configured Kitty command is empty")
        if desktop.generation != registry.generation:
            raise ActionError(
                "desktop_generation_changed", "desktop and registry generations differ"
            )

    @staticmethod
    def target_id(target: Target) -> str:
        try:
            return target_storage_key(target.key.identity)
        except RegistryError as error:
            raise ActionError(error.args[0], "target identity is invalid") from error

    def _validated_reference(self, target: Target, reference: EntryReference) -> EntryReference:
        try:
            parsed = parse_entry_reference(reference.wire)
        except RecordError as error:
            raise ActionError("invalid_reference", "target entry reference is invalid") from error
        if (
            parsed.target_key != target.key
            or parsed.expected_host_id != target.key.host_id
            or parsed.expected_user.uid != target.key.uid
            or parsed.expected_user.account != target.key.account
            or parsed.spec_revision != target.spec_revision
        ):
            raise ActionError("stale_reference", "target and entry reference do not match")
        if target.capability.status != "supported":
            raise ActionError("entry_unavailable", "native manager is not currently launchable")
        return parsed

    def _focus(self, record: Instance, windows: tuple[Window, ...]) -> Instance:
        terminal, wrapper, client = (
            record.process(role) for role in ("terminal", "wrapper", "client")
        )
        if terminal is None or wrapper is None or client is None:
            return record
        try:
            window = self.desktop.match(windows, record.marker, terminal)
            if window is None:
                raise DesktopError("selected_window_disappeared")
            control = self.registry.control_path(record.instance_id)
            validate_kitty_layout(control, wrapper, client, self.terminal[0])
            self.desktop.focus(window.window_id, record.marker, terminal)
            return record
        except (DesktopError, RegistryError) as error:
            try:
                self.registry.update(record.target_id, record.instance_id, state="uncertain")
            except RegistryError:
                pass
            raise ActionError(_error_code(error), _bounded(error)) from error

    def _existing(self, records: list[Instance], windows: tuple[Window, ...]) -> Instance | None:
        if not records:
            return None
        if len(records) > 1:
            raise ActionError("multiple_instances_refresh", "refresh the target's instance rows")
        record = records[0]
        if record.state == "uncertain":
            raise ActionError("instance_uncertain", "existing launch evidence is uncertain")
        if record.state == "live":
            self._focus(record, windows)
            return record
        return record

    @staticmethod
    def _reject_retired(record: Instance) -> None:
        if record.state == "failed":
            saved = record.value.get("error")
            if isinstance(saved, dict):
                raise ActionError(
                    str(saved.get("code", "previous_launch_failed")),
                    str(saved.get("message", "previous native entry failed")),
                )
            raise ActionError("previous_launch_failed", "previous native entry failed")
        if record.state == "exited":
            raise ActionError(
                "request_finished", "the previous window has closed; refresh and select again"
            )

    def _local_home(self) -> Path:
        try:
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (KeyError, OSError) as error:
            raise ActionError(
                "user_home_unavailable", "owning desktop user's home is unavailable"
            ) from error
        if not home.is_absolute():
            raise ActionError("user_home_unavailable", "owning desktop user's home is invalid")
        return home

    def _viewer_argv(self, target_id: str, record: Instance) -> list[str]:
        wrapper = _artifact_root() / "bin" / "rofi-agent-plus-viewer"
        command = (
            [sys.executable, str(wrapper)]
            if wrapper.is_file()
            else [sys.executable, "-I", "-m", "rofi_agent_plus.viewer"]
        )
        return [
            *command,
            "--registry-root",
            str(self.registry.root),
            "--generation",
            self.registry.generation,
            "--target-id",
            target_id,
            "--instance-id",
            record.instance_id,
        ]

    def _dispatch(self, target_id: str, record: Instance) -> Instance:
        try:
            control = self.registry.control_path(record.instance_id)
            viewer = self._viewer_argv(target_id, record)
            cwd = self._local_home()
            command = kitty_command(
                self.terminal,
                record.marker,
                control,
                viewer,
                cwd,
            )
            self.registry.update(target_id, record.instance_id, state="dispatching")
        except (ActionError, DesktopError, RegistryError, OSError, ValueError) as error:
            try:
                self.registry.update(
                    target_id,
                    record.instance_id,
                    state="failed",
                    error={"code": _error_code(error), "message": _bounded(error)},
                )
            except RegistryError:
                pass
            raise ActionError(_error_code(error), _bounded(error)) from error
        try:
            process = self.popen(
                command,
                cwd=str(cwd),
                env=dict(self.env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as error:
            try:
                self.registry.update(
                    target_id,
                    record.instance_id,
                    state="failed",
                    error={"code": "terminal_spawn_failed", "message": _bounded(error)},
                )
            except RegistryError:
                pass
            raise ActionError("terminal_spawn_failed", _bounded(error)) from error
        try:
            terminal = self.observe(process.pid)
        except DesktopError as error:
            raise ActionError(_error_code(error), _bounded(error)) from error
        if terminal is None:
            raise ActionError(
                "terminal_identity_unavailable",
                "dedicated terminal started without a verifiable process identity",
            )
        # The viewer can already have acknowledged itself or exited. Merge only
        # this field so the picker cannot regress its lifecycle state.
        try:
            return self.registry.update(target_id, record.instance_id, terminal=terminal.wire())
        except RegistryError as error:
            raise ActionError(_error_code(error), _bounded(error)) from error

    def open_target(
        self,
        target: Target,
        reference: EntryReference,
        *,
        additional: bool = False,
        request_id: str | None = None,
    ) -> Instance:
        parsed = self._validated_reference(target, reference)
        target_id = self.target_id(target)
        request = request_id or uuid.uuid4().hex
        if not isinstance(request, str) or not _REQUEST_ID.fullmatch(request):
            raise ActionError(
                "invalid_request_id", "request ID must be a lowercase 32-character UUID"
            )
        try:
            windows = self.desktop.windows()
            records = self.registry.reconcile(target_id, self.desktop, windows)
        except (DesktopError, RegistryError) as error:
            raise ActionError(_error_code(error), _bounded(error)) from error
        if request_id is not None:
            try:
                replay = self.registry.find_request(target_id, request)
            except RegistryError as error:
                raise ActionError(_error_code(error), _bounded(error)) from error
            if replay is not None:
                current = next(
                    (item for item in records if item.instance_id == replay.instance_id), replay
                )
                self._reject_retired(current)
                if current.state == "uncertain":
                    raise ActionError("instance_uncertain", "existing launch evidence is uncertain")
                if current.state == "live":
                    self._focus(current, windows)
                return current
        if records and not additional:
            existing = self._existing(records, windows)
            if existing is not None:
                return existing

        # Pinning consults the current public mesh but does not perform SSH or
        # provider work. The entry helper repeats target validation at dispatch.
        try:
            pinned = self.transport.pin_entry(target.key.host_id)
        except TransportError as error:
            raise ActionError(error.code, _bounded(error)) from error
        try:
            owner = self.observe(os.getpid())
        except DesktopError as error:
            raise ActionError(_error_code(error), _bounded(error)) from error
        if owner is None:
            raise ActionError(
                "owner_identity_unavailable", "picker process identity is unavailable"
            )
        try:
            record, created = self.registry.reserve(
                target_id=target_id,
                target=target.wire,
                reference=parsed.wire,
                request_id=request,
                additional=additional,
                owner=owner,
                transport=pinned.to_json(),
            )
        except RegistryError as error:
            raise ActionError(_error_code(error), _bounded(error)) from error
        if not created:
            # Reservation coalesced with a concurrent first Open or replayed a
            # request. Re-observe local evidence and never dispatch a second one.
            try:
                current_windows = self.desktop.windows()
                refreshed = self.registry.reconcile(target_id, self.desktop, current_windows)
            except (DesktopError, RegistryError) as error:
                raise ActionError(_error_code(error), _bounded(error)) from error
            match = next(
                (item for item in refreshed if item.instance_id == record.instance_id), record
            )
            self._reject_retired(match)
            if match.state == "uncertain":
                raise ActionError("instance_uncertain", "existing launch evidence is uncertain")
            if match.state == "live":
                self._focus(match, current_windows)
            return match
        # _dispatch persists dispatching immediately before Kitty spawn; any
        # failure leaves a terminal failed record, never replay.
        dispatched = self._dispatch(target_id, record)
        self._reject_retired(dispatched)
        return dispatched

    def focus_instance(self, target_id: str, instance_id: str) -> Instance:
        try:
            storage_id = target_storage_key(target_id)
            windows = self.desktop.windows()
            records = self.registry.reconcile(storage_id, self.desktop, windows)
        except (DesktopError, RegistryError) as error:
            raise ActionError(_error_code(error), _bounded(error)) from error
        record = next((item for item in records if item.instance_id == instance_id), None)
        if record is None:
            raise ActionError("instance_missing", "selected local window is no longer active")
        if record.state != "live":
            raise ActionError("instance_not_live", "selected window is still opening or uncertain")
        self._focus(record, windows)
        return record


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and code[:64] else "action_failed"


def _bounded(error: BaseException) -> str:
    message = str(error)[:4096]
    return message or "direct window action failed"
