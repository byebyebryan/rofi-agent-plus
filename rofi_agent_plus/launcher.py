"""Prepare one picker frame and restore selection before Rofi displays it."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from .rofi import run_rofi

INITIAL_FRAME_ENV = "ROFI_AGENT_PLUS_INITIAL_FRAME"
MAX_INITIAL_FRAME_BYTES = 16 * 1024 * 1024


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


def main(argv: Sequence[str] | None = None) -> int:
    """Launch Rofi with its supported initial-row option and one shared frame."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        executable = Path(__file__).resolve().parents[1] / "bin" / "rofi-agent-plus"
        arguments = [
            "-show",
            "agent-plus",
            "-modes",
            f"agent-plus:{executable}",
            "-kb-custom-1",
            "Alt+r",
            "-kb-custom-2",
            "Right",
            "-kb-custom-3",
            "Left",
            "-kb-custom-4",
            "Alt+a",
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
        with redirect_stdout(output):
            run_rofi(dict(environment, ROFI_RETV="0"))
        frame = output.getvalue()
        if len(frame.encode("utf-8")) > MAX_INITIAL_FRAME_BYTES:
            raise ValueError("initial picker frame is too large")
        with TemporaryDirectory(prefix="rofi-agent-plus-launch-") as temporary:
            path = Path(temporary) / "initial-frame"
            path.write_text(frame, encoding="utf-8")
            path.chmod(0o600)
            environment[INITIAL_FRAME_ENV] = str(path)
            # Initial script headers cannot select a row. Pass the same frame
            # to the first callback so this index cannot race another refresh.
            return subprocess.run(
                ["rofi", "-selected-row", str(_selected_row(frame)), "-filter", "", *arguments],
                env=environment,
                check=False,
            ).returncode
    except (OSError, ValueError) as error:
        print(f"rofi-agent-plus-rofi: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
