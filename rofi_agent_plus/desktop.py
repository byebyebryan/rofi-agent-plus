"""Exact current-desktop identity and the dedicated Kitty surface adapter."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .process import ProcessError, run_bounded


class DesktopError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: str
    boot_id: str

    @classmethod
    def parse(cls, value: object) -> ProcessIdentity:
        if not isinstance(value, dict) or set(value) != {"pid", "start_ticks", "boot_id"}:
            raise DesktopError("invalid_process_identity")
        pid, ticks, boot = value["pid"], value["start_ticks"], value["boot_id"]
        if type(pid) is not int or pid <= 0 or not isinstance(ticks, str) or not ticks.isdecimal():
            raise DesktopError("invalid_process_identity")
        if not isinstance(boot, str) or len(boot) != 36:
            raise DesktopError("invalid_process_identity")
        return cls(pid, ticks, boot)

    def wire(self) -> dict[str, object]:
        return asdict(self)

    def alive(self) -> bool:
        return observe_process(self.pid) == self


def observe_process(pid: int) -> ProcessIdentity | None:
    try:
        directory = Path(f"/proc/{pid}")
        if directory.stat().st_uid != os.getuid():
            raise DesktopError("process_owner_mismatch")
        fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return ProcessIdentity(
            pid, fields[19], Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        )
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, ValueError, IndexError) as error:
        raise DesktopError("process_observation_unavailable") from error


def run_json(argv: list[str], *, env: dict[str, str] | None = None, timeout: float = 1) -> Any:
    try:
        result = run_bounded(argv, env=env, timeout=timeout, stdout_limit=1024 * 1024)
        if result.returncode != 0:
            raise DesktopError("desktop_request_failed")
        return json.loads(result.stdout) if result.stdout.strip() else None
    except (OSError, ProcessError, UnicodeError, ValueError) as error:
        raise DesktopError("desktop_request_unavailable") from error


@dataclass(frozen=True)
class Window:
    window_id: int
    pid: int | None
    app_id: str
    title: str
    focused: bool


class Desktop:
    def __init__(self, env: dict[str, str] | None = None):
        self.env = dict(os.environ if env is None else env)
        name = self.env.get("NIRI_SOCKET", "")
        if not name or not Path(name).is_absolute():
            raise DesktopError("niri_socket_unavailable")
        self.socket_path = Path(name)
        self.generation = self._generation()

    def _generation(self) -> str:
        try:
            info = self.socket_path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise DesktopError("niri_socket_owner_mismatch")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(1)
                connection.connect(str(self.socket_path))
                pid, uid, _gid = struct.unpack(
                    "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
            if uid != os.getuid():
                raise DesktopError("niri_peer_owner_mismatch")
            process = observe_process(pid)
            if process is None:
                raise DesktopError("niri_process_unavailable")
            value = [str(self.socket_path), info.st_dev, info.st_ino, process.wire()]
            return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        except OSError as error:
            raise DesktopError("niri_identity_unavailable") from error

    def windows(self) -> tuple[Window, ...]:
        if self._generation() != self.generation:
            raise DesktopError("desktop_generation_changed")
        values = run_json(["niri", "msg", "--json", "windows"], env=self.env)
        if not isinstance(values, list) or len(values) > 1024:
            raise DesktopError("invalid_window_inventory")
        windows = []
        for value in values:
            if not isinstance(value, dict) or type(value.get("id")) is not int:
                raise DesktopError("invalid_window_inventory")
            pid = value.get("pid")
            if pid is not None and (type(pid) is not int or pid <= 0):
                raise DesktopError("invalid_window_inventory")
            title, app_id = value.get("title") or "", value.get("app_id") or ""
            if not isinstance(title, str) or not isinstance(app_id, str):
                raise DesktopError("invalid_window_inventory")
            windows.append(
                Window(
                    value["id"], pid, app_id[:512], title[:4096], value.get("is_focused") is True
                )
            )
        return tuple(windows)

    @staticmethod
    def match(windows: tuple[Window, ...], marker: str, terminal: ProcessIdentity) -> Window | None:
        matching = [w for w in windows if w.app_id == marker or w.pid == terminal.pid]
        if not matching:
            return None
        if len(matching) != 1 or matching[0].app_id != marker or matching[0].pid != terminal.pid:
            raise DesktopError("ambiguous_terminal_layout")
        if not terminal.alive():
            raise DesktopError("terminal_generation_changed")
        return matching[0]

    def focus(self, window_id: int, marker: str, terminal: ProcessIdentity) -> None:
        current = self.match(self.windows(), marker, terminal)
        if current is None or current.window_id != window_id:
            raise DesktopError("selected_window_disappeared")
        run_json(
            ["niri", "msg", "--json", "action", "focus-window", "--id", str(window_id)],
            env=self.env,
        )


def kitty_command(
    prefix: tuple[str, ...] | list[str], marker: str, control: Path, command: list[str], cwd: Path
) -> list[str]:
    if not prefix or Path(prefix[0]).name != "kitty":
        raise DesktopError("unsupported_terminal")
    executable = shutil.which(prefix[0])
    if not executable:
        raise DesktopError("kitty_unavailable")
    # Configuration overrides are accepted; identity/session/command flags belong
    # to this adapter. Fixed arguments come after any user-supplied overrides.
    extra = list(prefix[1:])
    index = 0
    while index < len(extra):
        option = extra[index]
        if option in ("-c", "--config", "-o", "--override"):
            if index + 1 >= len(extra):
                raise DesktopError("invalid_terminal_options")
            index += 2
        elif option.startswith(("--config=", "--override=")):
            index += 1
        else:
            raise DesktopError("unsupported_terminal_options")
    return [
        executable,
        *extra,
        "--single-instance=no",
        "--session",
        "none",
        "--class",
        marker,
        "--working-directory",
        str(cwd),
        "--override",
        "allow_remote_control=socket-only",
        "--listen-on",
        f"unix:{control}",
        *command,
    ]


def kitty_control_command(
    control: Path, arguments: list[str], executable: str = "kitty"
) -> list[str]:
    terminal = shutil.which(executable)
    sibling = Path(terminal).parent / "kitten" if terminal else Path("/nonexistent")
    command = str(sibling) if sibling.is_file() else shutil.which("kitten")
    if not command:
        raise DesktopError("kitty_control_unavailable")
    return [command, "@", "--to", f"unix:{control}", "--use-password", "never", *arguments]


def validate_kitty_layout(
    control: Path, wrapper: ProcessIdentity, client: ProcessIdentity, executable: str = "kitty"
) -> None:
    try:
        info = control.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise DesktopError("kitty_socket_unavailable")
        values = run_json(kitty_control_command(control, ["ls"], executable))
        if not isinstance(values, list) or len(values) != 1:
            raise DesktopError("ambiguous_terminal_layout")
        tabs = values[0].get("tabs", [])
        if len(tabs) != 1 or len(tabs[0].get("windows", [])) != 1:
            raise DesktopError("ambiguous_terminal_layout")
        pane = tabs[0]["windows"][0]
        foreground = pane.get("foreground_processes", [])
        if (
            pane.get("pid") != wrapper.pid
            or not wrapper.alive()
            or not client.alive()
            or client.pid not in {p.get("pid") for p in foreground}
        ):
            raise DesktopError("foreground_client_changed")
    except (OSError, KeyError, TypeError, AttributeError) as error:
        raise DesktopError("kitty_layout_unavailable") from error
