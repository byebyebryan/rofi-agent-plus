"""Selection restoration must use the same first frame Rofi displays."""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import app, launcher


class LauncherTest(unittest.TestCase):
    def test_installed_default_mode_uses_sibling_cli_from_same_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / "bin"
            commands.mkdir()
            agent = commands / "rofi-agent-plus"
            agent.write_text("#!/bin/sh\nexit 0\n")
            agent.chmod(0o700)
            entry = commands / "rofi-agent-plus-rofi"
            entry.write_text("#!/bin/sh\nexit 0\n")
            entry.chmod(0o700)
            with (
                mock.patch("sys.argv", [str(entry)]),
                mock.patch.object(launcher, "__file__", str(root / "site-packages/launcher.py")),
                mock.patch.object(launcher, "run_rofi", side_effect=lambda _env: print("row")),
                mock.patch.object(launcher.subprocess, "run") as run,
            ):
                run.return_value.returncode = 0
                self.assertEqual(0, launcher.main([]))
                argv = run.call_args.args[0]
                self.assertEqual(f"agent-plus:{agent}", argv[argv.index("-modes") + 1])

    def test_initial_frame_is_shared_and_removed_without_second_discovery(self) -> None:
        frame = "\x00new-selection\x1f2\n\x00delim\x1f\\t\na\tb\tc\t"
        path_seen: list[Path] = []

        def render(environment: dict[str, str]) -> int:
            self.assertEqual("0", environment["ROFI_RETV"])
            self.assertNotIn("ROFI_DATA", environment)
            print(frame, end="")
            return 0

        def launch(
            arguments: list[str], *, env: dict[str, str], check: bool
        ) -> subprocess.CompletedProcess:
            self.assertEqual(["rofi", "-selected-row", "2", "-filter", ""], arguments[:5])
            self.assertNotIn("ROFI_RETV", env)
            path = Path(env[launcher.INITIAL_FRAME_ENV])
            path_seen.append(path)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
            with (
                mock.patch.dict(os.environ, dict(env, ROFI_RETV="0"), clear=True),
                mock.patch("rofi_agent_plus.app.run_rofi") as rediscover,
                mock.patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                self.assertEqual(0, app.main([]))
                self.assertEqual(frame, output.getvalue())
                rediscover.assert_not_called()
            self.assertIsNone(launcher.initial_frame(dict(env, ROFI_RETV="11")))
            return subprocess.CompletedProcess(arguments, 0)

        with (
            mock.patch.dict(os.environ, {"ROFI_RETV": "1", "ROFI_DATA": "old"}),
            mock.patch("rofi_agent_plus.launcher.run_rofi", side_effect=render) as renderer,
            mock.patch("rofi_agent_plus.launcher.subprocess.run", side_effect=launch),
        ):
            self.assertEqual(0, launcher.main(["-show", "agent-plus"]))
        renderer.assert_called_once()
        self.assertFalse(path_seen[0].exists())

    def test_unsafe_or_missing_frame_uses_normal_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frame"
            path.write_text("frame")
            path.chmod(0o644)
            env = {"ROFI_RETV": "0", launcher.INITIAL_FRAME_ENV: str(path)}
            self.assertIsNone(launcher.initial_frame(env))
            path.chmod(0o600)
            self.assertEqual("frame", launcher.initial_frame(env))
            link = Path(temporary) / "link"
            link.symlink_to(path)
            self.assertIsNone(
                launcher.initial_frame(dict(env, ROFI_AGENT_PLUS_INITIAL_FRAME=str(link)))
            )
            path.unlink()
            self.assertIsNone(launcher.initial_frame(env))

    def test_rofi_launch_failure_cleans_the_private_frame(self) -> None:
        frames: list[Path] = []

        def failure(_arguments: list[str], *, env: dict[str, str], check: bool) -> None:
            frames.append(Path(env[launcher.INITIAL_FRAME_ENV]))
            raise OSError("synthetic launch failure")

        with (
            mock.patch("rofi_agent_plus.launcher.run_rofi", side_effect=lambda _env: print("row")),
            mock.patch("rofi_agent_plus.launcher.subprocess.run", side_effect=failure),
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            self.assertEqual(1, launcher.main(["-show", "agent-plus"]))
        self.assertFalse(frames[0].exists())


if __name__ == "__main__":
    unittest.main()
