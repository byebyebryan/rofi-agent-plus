"""Prepare one picker frame and restore selection before Rofi displays it."""

from __future__ import annotations

import io
import os
import signal
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from .cache import CacheError
from .process import passive_probe_signals
from .rofi import build_context, run_rofi
from .startup_refresh import STATUS_ENV, StartupRefresh
from .window_titles import TITLES_ENV, TITLES_NAME

INITIAL_FRAME_ENV = "ROFI_AGENT_PLUS_INITIAL_FRAME"
MAX_INITIAL_FRAME_BYTES = 16 * 1024 * 1024
ROFI_STOP_SECONDS = 1.0


def _agent_executable() -> Path:
    """Use the same artifact's sibling CLI for installed entry points."""
    invoked = Path(sys.argv[0])
    if invoked.name == "rofi-agent-plus-rofi":
        sibling = invoked.resolve().with_name("rofi-agent-plus")
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return sibling
    checkout = Path(__file__).resolve().parents[1] / "bin" / "rofi-agent-plus"
    if checkout.is_file() and os.access(checkout, os.X_OK):
        return checkout
    raise ValueError("matching Agent Plus executable is unavailable")


def initial_frame(environ: Mapping[str, str]) -> str | None:
    """Serve a launch-owned first frame; subsequent callbacks run normally."""

    if environ.get("ROFI_RETV") != "0" or not environ.get(INITIAL_FRAME_ENV):
        return None
    path = Path(environ[INITIAL_FRAME_ENV])
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAX_INITIAL_FRAME_BYTES
        ):
            return None
        with path.open("rb") as stream:
            content = stream.read(MAX_INITIAL_FRAME_BYTES + 1)
        if len(content) > MAX_INITIAL_FRAME_BYTES:
            return None
        return content.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        return None


def _selected_row(frame: str) -> int:
    """Read the renderer's bounded selection hint without parsing row text."""

    prefix = "\x00new-selection\x1f"
    for line in frame.split("\n"):
        if line.startswith(prefix):
            value = line[len(prefix) :]
            if value.isascii() and value.isdecimal() and len(value) <= 8:
                return int(value)
            break
    return 0


def _stop_rofi(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=ROFI_STOP_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait()
        except OSError:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    """Launch Rofi with its supported initial-row option and one shared frame."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        executable = _agent_executable()
    except ValueError as error:
        print(f"rofi-agent-plus-rofi: {error}", file=sys.stderr)
        return 1
    if not arguments:
        arguments = [
            "-show",
            "agent-plus",
            "-modes",
            f"agent-plus:{executable}",
            "-kb-custom-1",
            "Alt+r",
            "-kb-custom-7",
            "Tab",
            "-kb-custom-8",
            "ISO_Left_Tab",
            "-kb-element-next",
            "",
            "-kb-element-prev",
            "",
            "-kb-accept-custom",
            "",
            "-kb-delete-entry",
            "",
            "-kb-cancel",
            "Escape,Control+g",
            "-kb-move-char-forward",
            "Control+f",
            "-kb-move-char-back",
            "Control+b",
            "-eh",
            "2",
        ]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("ROFI_")}
    output = io.StringIO()
    try:
        with TemporaryDirectory(prefix="rofi-agent-plus-launch-") as temporary:
            directory = Path(temporary)
            frame_path = directory / "initial-frame"
            environment[TITLES_ENV] = str(directory / TITLES_NAME)
            context = build_context(environment)
            try:
                snapshot = context.cache.load()
            except CacheError:
                snapshot = None
            refresh = None
            if snapshot is None or snapshot.is_stale(context.config.desktop.catalog_ttl_seconds):
                refresh = StartupRefresh(directory, executable, environment)
                environment[STATUS_ENV] = str(refresh.path)
            with passive_probe_signals(), redirect_stdout(output):
                run_rofi(dict(environment, ROFI_RETV="0"), context=context)
            frame = output.getvalue()
            if len(frame.encode("utf-8")) > MAX_INITIAL_FRAME_BYTES:
                raise ValueError("initial picker frame is too large")
            frame_path.write_text(frame, encoding="utf-8")
            frame_path.chmod(0o600)
            environment[INITIAL_FRAME_ENV] = str(frame_path)
            # Initial script headers cannot select a row. Pass the same frame
            # to the first callback so this index cannot race another refresh.
            rofi = subprocess.Popen(
                [
                    "rofi",
                    "-selected-row",
                    str(_selected_row(frame)),
                    "-filter",
                    "",
                    "-no-sort",
                    *arguments,
                ],
                env=environment,
            )
            try:
                if refresh is not None:
                    refresh.start()
                return_code = rofi.wait()
                return int(return_code) if return_code is not None else 1
            except KeyboardInterrupt:
                _stop_rofi(rofi)
                return 130
            finally:
                _stop_rofi(rofi)
                if refresh is not None:
                    refresh.stop()
    except (OSError, ValueError) as error:
        print(f"rofi-agent-plus-rofi: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
