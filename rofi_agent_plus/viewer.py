"""Foreground native-client wrapper owned by a dedicated Kitty terminal."""

from __future__ import annotations

import argparse
import os
import pwd
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .desktop import DesktopError, ProcessIdentity, observe_process
from .model import (
    MAX_REFERENCE_BYTES,
    RecordError,
    encode_document,
    parse_entry_reference,
    parse_target_key,
)
from .registry import Instance, Registry, RegistryError
from .transport import PinnedTransport, Transport, TransportError

MAX_DIAGNOSTIC = 4096


class ViewerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _bounded(error: BaseException) -> str:
    return (str(error) or "native client could not be entered")[:MAX_DIAGNOSTIC]


def _code(error: BaseException) -> str:
    value = getattr(error, "code", None)
    return value[:64] if isinstance(value, str) and value else "viewer_failed"


def _record_target(record: Instance):
    value = record.value.get("target")
    if not isinstance(value, dict) or set(value) != {
        "key",
        "label",
        "specRevision",
        "capability",
        "daemon",
    }:
        raise ViewerError("invalid_target", "saved target record is invalid")
    key = parse_target_key(value.get("key"))
    label, revision, capability, daemon = (
        value.get("label"),
        value.get("specRevision"),
        value.get("capability"),
        value.get("daemon"),
    )
    if (
        not isinstance(label, str)
        or len(label) > 256
        or not isinstance(revision, str)
        or not isinstance(capability, dict)
        or capability.get("status") != "supported"
        or not isinstance(daemon, dict)
    ):
        raise ViewerError("invalid_target", "saved target contract is invalid")
    return key, revision


def _read_instance(registry: Registry, target_id: str, instance_id: str) -> Instance:
    try:
        matches = [item for item in registry.records(target_id) if item.instance_id == instance_id]
    except RegistryError:
        raise
    if len(matches) != 1:
        raise ViewerError("instance_missing", "saved direct-window instance is unavailable")
    return matches[0]


def _hold_for_dismissal(stream_in: object, stream_err: object, *, is_tty: bool) -> None:
    if not is_tty:
        return
    prior = signal.getsignal(signal.SIGINT)
    print("\nPress Enter or Ctrl+C to close this error window.", file=stream_err, flush=True)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        stream_in.readline()  # type: ignore[attr-defined]
    except (KeyboardInterrupt, OSError):
        pass
    finally:
        signal.signal(signal.SIGINT, prior)


def _report_failure(
    registry: Registry,
    target_id: str,
    instance_id: str,
    error: BaseException,
    *,
    stream_err: object,
    stream_in: object,
    is_tty: bool,
) -> int:
    code, message = _code(error), _bounded(error)
    try:
        registry.update(
            target_id,
            instance_id,
            state="failed",
            error={"code": code, "message": message},
        )
    except RegistryError:
        pass
    print(f"Agent Plus could not start this native client [{code}]: {message}", file=stream_err)
    _hold_for_dismissal(stream_in, stream_err, is_tty=is_tty)
    return 1


def _report_uncertain(
    registry: Registry,
    target_id: str,
    instance_id: str,
    error: BaseException,
    *,
    stream_err: object,
    stream_in: object,
    is_tty: bool,
) -> int:
    code, message = _code(error), _bounded(error)
    try:
        registry.update(
            target_id,
            instance_id,
            state="uncertain",
            error={"code": code, "message": message},
        )
    except RegistryError:
        pass
    print(
        f"Agent Plus could not confirm this native client's exit [{code}]: {message}",
        file=stream_err,
    )
    _hold_for_dismissal(stream_in, stream_err, is_tty=is_tty)
    return 1


def run_viewer(
    registry_root: str | Path,
    generation: str,
    target_id: str,
    instance_id: str,
    *,
    env: Mapping[str, str] | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    observe: Callable[[int], ProcessIdentity | None] = observe_process,
    stdin: object | None = None,
    stderr: object | None = None,
    is_tty: bool | None = None,
) -> int:
    """Acknowledge this wrapper, exec one local/SSH client, and retire on exit."""

    process_env = dict(os.environ if env is None else env)
    input_stream = sys.stdin if stdin is None else stdin
    error_stream = sys.stderr if stderr is None else stderr
    tty = bool(getattr(input_stream, "isatty", lambda: False)()) if is_tty is None else is_tty
    registry: Registry | None = None
    try:
        registry = Registry(generation, Path(registry_root))
        record = _read_instance(registry, target_id, instance_id)
        if record.state not in {"dispatching", "opening", "reserved"}:
            raise ViewerError(
                "instance_not_opening", "saved direct-window request is no longer opening"
            )
        wrapper = observe(os.getpid())
        if wrapper is None:
            raise ViewerError(
                "wrapper_identity_unavailable", "viewer process identity is unavailable"
            )
        try:
            reference = parse_entry_reference(record.value["entryReference"])
            key, revision = _record_target(record)
            if (
                key != reference.target_key
                or revision != reference.spec_revision
                or record.target_id != target_id
            ):
                raise ViewerError(
                    "stale_reference", "saved entry reference no longer matches target"
                )
            raw_reference = encode_document(reference)
            if len(raw_reference) > MAX_REFERENCE_BYTES:
                raise ViewerError("reference_too_large", "saved entry reference exceeds its limit")
            reference_json = raw_reference.decode("utf-8").rstrip("\n")
            pinned = PinnedTransport.parse(record.value["transport"])
            if pinned.host_id != reference.expected_host_id:
                raise ViewerError(
                    "transport_host_mismatch", "saved transport belongs to another host"
                )
            registry.update(target_id, instance_id, wrapper=wrapper.wire())
            transport = Transport(env=process_env)
            argv = transport.entry_argv(reference_json, pinned)
            child_env = transport.entry_environment(pinned)
            try:
                # The host helper changes to the owning account's home before
                # exec; remote SSH begins in the account's remote login shell.
                # This local cwd is still the desktop owner's home.
                cwd = Path(pwd.getpwuid(os.getuid()).pw_dir)
            except (KeyError, OSError) as error:
                raise ViewerError(
                    "user_home_unavailable", "owning desktop home is unavailable"
                ) from error
            if not cwd.is_absolute():
                raise ViewerError("user_home_unavailable", "owning desktop home is invalid")
        except (
            RecordError,
            RegistryError,
            TransportError,
            DesktopError,
            OSError,
            ValueError,
        ) as error:
            return _report_failure(
                registry,
                target_id,
                instance_id,
                error,
                stream_err=error_stream,
                stream_in=input_stream,
                is_tty=tty,
            )

        previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda _signum, _frame: None)
        try:
            try:
                child = popen(
                    argv,
                    cwd=str(cwd),
                    env=child_env,
                    close_fds=True,
                    start_new_session=False,
                )
            except OSError:
                return _report_failure(
                    registry,
                    target_id,
                    instance_id,
                    ViewerError(
                        "client_exec_failed", "native manager or SSH client could not be started"
                    ),
                    stream_err=error_stream,
                    stream_in=input_stream,
                    is_tty=tty,
                )
            try:
                client = observe(child.pid)
            except DesktopError:
                client = None
            if client is not None:
                try:
                    registry.update(target_id, instance_id, client=client.wire())
                except RegistryError:
                    # Do not kill or supervise native work if the local record
                    # becomes unavailable after entry.
                    pass
            try:
                returncode = child.wait()
            except (OSError, ChildProcessError):
                return _report_uncertain(
                    registry,
                    target_id,
                    instance_id,
                    ViewerError("client_wait_failed", "native client exit could not be observed"),
                    stream_err=error_stream,
                    stream_in=input_stream,
                    is_tty=tty,
                )
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        if returncode == 0:
            try:
                registry.update(target_id, instance_id, state="exited")
            except RegistryError:
                pass
            return 0
        return _report_failure(
            registry,
            target_id,
            instance_id,
            ViewerError("client_exit_status", f"native client exited with status {returncode}"),
            stream_err=error_stream,
            stream_in=input_stream,
            is_tty=tty,
        )
    except (RegistryError, ViewerError, ValueError, OSError) as error:
        if registry is None:
            print(
                f"Agent Plus viewer could not open its private record [{_code(error)}]: {_bounded(error)}",
                file=error_stream,
            )
            _hold_for_dismissal(input_stream, error_stream, is_tty=tty)
            return 1
        return _report_failure(
            registry,
            target_id,
            instance_id,
            error,
            stream_err=error_stream,
            stream_in=input_stream,
            is_tty=tty,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rofi-agent-plus-viewer")
    parser.add_argument("--registry-root", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--instance-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_viewer(args.registry_root, args.generation, args.target_id, args.instance_id)


if __name__ == "__main__":
    raise SystemExit(main())
