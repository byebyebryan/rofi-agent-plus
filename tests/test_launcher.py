"""Launch frame selection and exact Rofi child lifetime."""

from __future__ import annotations

import io
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import app, launcher


class FakeProcess:
    def __init__(self, values=(0,)):
        self.values = list(values)
        self.returncode = None
        self.signals: list[int] = []
        self.waited = False

    def poll(self):
        if self.returncode is None and self.values:
            value = self.values.pop(0)
            if value is not None:
                self.returncode = value
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = 128 + value

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.returncode = -9


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.context = mock.Mock()
        self.context.cache.load.return_value.is_stale.return_value = False
        patcher = mock.patch.object(launcher, "build_context", return_value=self.context)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_bindings_select_flat_open_and_native_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "rofi-agent-plus"
            executable.touch()
            rofi = FakeProcess()
            calls = []

            def render(environment, **_kwargs):
                self.assertEqual("0", environment["ROFI_RETV"])
                print("\x00new-selection\x1f1\nOne\x00info\x1f{}")

            def popen(argv, *, env, **kwargs):
                calls.append((argv, env, kwargs))
                self.assertTrue(argv[0] in {"rofi", str(executable)})
                return rofi

            with (
                mock.patch.object(launcher, "_agent_executable", return_value=executable),
                mock.patch.object(launcher, "run_rofi", side_effect=render),
                mock.patch.object(launcher.subprocess, "Popen", side_effect=popen),
                mock.patch.dict(os.environ, {"ROFI_RETV": "19", "ROFI_DATA": "old"}),
            ):
                self.assertEqual(0, launcher.main([]))
            argv = calls[0][0]
            self.assertEqual("Alt+r", argv[argv.index("-kb-custom-1") + 1])
            self.assertEqual("Tab", argv[argv.index("-kb-custom-7") + 1])
            self.assertEqual("ISO_Left_Tab", argv[argv.index("-kb-custom-8") + 1])
            self.assertNotIn("-kb-custom-2", argv)
            self.assertIn("-no-sort", argv)
            self.assertIn("Control+f", argv)
            self.assertIn("Control+b", argv)
            self.assertNotIn("ROFI_RETV", calls[0][1])

    def test_first_frame_is_shared_and_selected_row_is_passed_to_rofi(self):
        frame = "\x00new-selection\x1f2\n\x00delim\x1f\\t\nOne\tTwo\t"
        rofi = FakeProcess((0,))

        def render(environment, **_kwargs):
            self.assertEqual("0", environment["ROFI_RETV"])
            self.assertNotIn("ROFI_DATA", environment)
            print(frame, end="")

        calls = []

        def launch(argv, *, env, **kwargs):
            calls.append((argv, env, kwargs))
            self.assertEqual(["rofi", "-selected-row", "2", "-filter", ""], argv[:5])
            frame_path = Path(env[launcher.INITIAL_FRAME_ENV])
            self.assertEqual(0o600, frame_path.stat().st_mode & 0o777)
            self.assertEqual(0o700, frame_path.parent.stat().st_mode & 0o777)
            self.assertEqual(frame, launcher.initial_frame(dict(env, ROFI_RETV="0")))
            return rofi

        with (
            mock.patch.object(
                launcher, "_agent_executable", return_value=Path("/artifact/bin/rofi-agent-plus")
            ),
            mock.patch.object(launcher, "run_rofi", side_effect=render),
            mock.patch.object(launcher.subprocess, "Popen", side_effect=launch),
            mock.patch.dict(os.environ, {"ROFI_RETV": "1", "ROFI_DATA": "stale"}),
        ):
            self.assertEqual(0, launcher.main(["-show", "agent-plus"]))
        self.assertEqual(1, len(calls))
        self.assertNotIn("ROFI_AGENT_PLUS_REFRESH_REQUEST", calls[0][1])
        self.assertNotIn("ROFI_AGENT_PLUS_REFRESH_STATUS", calls[0][1])
        self.assertTrue(rofi.waited)
        self.assertFalse(Path(calls[0][1][launcher.INITIAL_FRAME_ENV]).exists())

    def test_keyboard_interrupt_cancels_only_the_rofi_child(self):
        class InterruptedProcess(FakeProcess):
            def wait(self, timeout=None):
                if not self.signals:
                    raise KeyboardInterrupt
                return super().wait(timeout)

        rofi = InterruptedProcess(values=())
        launched = []

        def render(_environment, **_kwargs):
            print("frame")

        def launch(argv, **kwargs):
            launched.append(argv)
            return rofi

        with (
            mock.patch.object(
                launcher, "_agent_executable", return_value=Path("/artifact/bin/rofi-agent-plus")
            ),
            mock.patch.object(launcher, "run_rofi", side_effect=render),
            mock.patch.object(launcher.subprocess, "Popen", side_effect=launch),
        ):
            self.assertEqual(130, launcher.main(["-show", "agent-plus"]))

        self.assertEqual(["rofi", "-selected-row", "0", "-filter", ""], launched[0][:5])
        self.assertEqual([signal.SIGINT], rofi.signals)
        self.assertTrue(rofi.waited)

    def test_initial_frame_rejects_wrong_owner_mode_symlink_and_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "frame"
            path.write_text("frame", encoding="utf-8")
            env = {"ROFI_RETV": "0", launcher.INITIAL_FRAME_ENV: str(path)}
            self.assertIsNone(launcher.initial_frame(env))
            path.chmod(0o600)
            self.assertEqual("frame", launcher.initial_frame(env))
            link = root / "link"
            link.symlink_to(path)
            self.assertIsNone(
                launcher.initial_frame({**env, launcher.INITIAL_FRAME_ENV: str(link)})
            )
            self.assertIsNone(launcher.initial_frame({**env, "ROFI_RETV": "1"}))

    def test_frame_or_rofi_failure_cleans_the_private_session_directory(self):
        frames: list[Path] = []

        def render(environment, **_kwargs):
            print("frame")

        def failure(_argv, *, env, **_kwargs):
            frames.append(Path(env[launcher.INITIAL_FRAME_ENV]))
            raise OSError("controlled Rofi failure")

        with (
            mock.patch.object(
                launcher, "_agent_executable", return_value=Path("/artifact/bin/agent")
            ),
            mock.patch.object(launcher, "run_rofi", side_effect=render),
            mock.patch.object(launcher.subprocess, "Popen", side_effect=failure),
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            self.assertEqual(1, launcher.main(["-show", "agent-plus"]))
        self.assertFalse(frames[0].exists())

    def test_stale_open_launches_rofi_before_refresh_and_cancels_owned_helper(self):
        from rofi_agent_plus.startup_refresh import STATUS_ENV, read_status

        self.context.cache.load.return_value.is_stale.return_value = True
        rofi = FakeProcess()
        worker = FakeProcess((None,))
        calls = []

        def render(environment, **kwargs):
            self.assertIs(kwargs["context"], self.context)
            self.assertTrue(read_status(environment).pending)
            print("frame")

        def launch(argv, *, env, **kwargs):
            calls.append((argv, env, kwargs))
            return rofi if argv[0] == "rofi" else worker

        with (
            mock.patch.object(
                launcher, "_agent_executable", return_value=Path("/artifact/bin/agent")
            ),
            mock.patch.object(launcher, "run_rofi", side_effect=render),
            mock.patch.object(launcher.subprocess, "Popen", side_effect=launch),
        ):
            self.assertEqual(0, launcher.main([]))
        self.assertEqual(calls[0][0][0], "rofi")
        self.assertEqual(calls[1][0], ["/artifact/bin/agent", "_direct-refresh"])
        self.assertTrue(calls[1][2]["start_new_session"])
        self.assertEqual(worker.signals, [signal.SIGTERM])
        self.assertTrue(worker.waited)
        self.assertFalse(Path(calls[0][1][STATUS_ENV]).exists())

    def test_app_reuses_the_prepared_first_frame_without_discovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frame"
            path.write_text("prepared", encoding="utf-8")
            path.chmod(0o600)
            env = {"ROFI_RETV": "0", launcher.INITIAL_FRAME_ENV: str(path)}
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("rofi_agent_plus.app.run_rofi") as rediscover,
                mock.patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                self.assertEqual(0, app.main([]))
            self.assertEqual("prepared", output.getvalue())
            rediscover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
