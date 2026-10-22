"""Dialog-scoped refresh tickets and terminal outcomes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rofi_agent_plus import app
from rofi_agent_plus.cache import CacheError
from rofi_agent_plus.registry import atomic_json, read_private_json
from rofi_agent_plus.startup_refresh import (
    LOCK_FD_ENV,
    STATUS_ENV,
    StartupRefresh,
    finish_refresh,
    read_status,
    refresh_worker,
)


class StartupRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.refresh = StartupRefresh(self.root, Path("/artifact/agent"), {})
        self.addCleanup(self.refresh.close)
        self.env = {STATUS_ENV: str(self.refresh.path)}

    def test_ticket_is_private_and_success_or_failure_is_terminal(self):
        self.assertEqual(self.refresh.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(read_status(self.env).pending)
        finish_refresh(self.env)
        self.assertEqual(read_status(self.env).state, "complete")
        self.assertFalse(read_status(self.env).pending)
        finish_refresh(self.env, "bounded failure")
        self.assertEqual(read_status(self.env).state, "failed")
        self.assertIn("bounded failure", read_status(self.env).message)

    def test_lost_helper_times_out_and_unsafe_or_malformed_tickets_do_not_poll(self):
        atomic_json(
            self.refresh.path,
            {
                "schemaVersion": 1,
                "state": "pending",
                "deadline": time.monotonic() - 1,
                "message": "",
            },
        )
        self.assertFalse(read_status(self.env).pending)
        self.assertIn("timed out", read_status(self.env).message)
        link = self.root / "link"
        link.symlink_to(self.refresh.path)
        self.assertIsNone(read_status({STATUS_ENV: str(link)}))
        self.refresh.path.write_text("malformed")
        self.assertIsNone(read_status(self.env))
        self.root.chmod(0o755)
        self.assertIsNone(read_status(self.env))

    def test_worker_launch_failure_publishes_failure_for_the_dialog(self):
        with patch("rofi_agent_plus.startup_refresh.subprocess.Popen", side_effect=OSError):
            self.refresh.start()
        self.assertEqual(read_status(self.env).state, "failed")
        self.refresh.stop()

    def test_unstarted_or_crashed_owner_does_not_leave_a_pending_lease(self):
        self.refresh.close()
        replacement = StartupRefresh(self.root, Path("/artifact/agent"), {})
        self.addCleanup(replacement.close)
        self.assertIsNotNone(replacement.descriptor)
        joined = StartupRefresh(self.root, Path("/artifact/agent"), {})
        self.addCleanup(joined.close)
        self.assertIsNone(joined.descriptor)
        self.assertEqual(replacement.path, joined.path)

    def _real_worker(self, *, delay=0.3, deadline=None):
        """An inert process exercises the real lock/worker lifetime contract."""

        self.refresh.close()
        helper = self.root / "worker"
        helper.write_text(
            f"#!{sys.executable} -B\n"
            "import os, sys, time\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            "from pathlib import Path\n"
            "from rofi_agent_plus.startup_refresh import refresh_worker, finish_refresh\n"
            "try:\n"
            " with refresh_worker(os.environ):\n"
            f"  time.sleep({delay!r})\n"
            f"  Path({str(self.root / 'published')!r}).write_text('complete')\n"
            "  finish_refresh(os.environ)\n"
            "except TimeoutError:\n"
            " finish_refresh(os.environ, 'controlled deadline')\n"
            " raise SystemExit(2)\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        worker = StartupRefresh(self.root, helper, dict(os.environ))
        self.addCleanup(worker.stop)
        if deadline is not None:
            value = read_private_json(worker.path)
            value["deadline"] = time.monotonic() + deadline
            atomic_json(worker.path, value)
        worker.start()
        return worker

    def test_refresh_finishes_after_launcher_release_and_reopen_joins(self):
        worker = self._real_worker()
        worker.close()
        reopened = StartupRefresh(self.root, Path("/must-not-launch"), dict(os.environ))
        self.addCleanup(reopened.close)
        self.assertIsNone(reopened.descriptor)
        self.assertTrue(read_status(reopened.env).pending)
        with patch("rofi_agent_plus.startup_refresh.subprocess.Popen") as launch:
            reopened.start()
        launch.assert_not_called()
        self.assertEqual(0, worker.process.wait(timeout=4))
        self.assertEqual("complete", (self.root / "published").read_text())
        self.assertEqual("complete", read_status(reopened.env).state)
        successor = StartupRefresh(self.root, Path("/next-worker"), {})
        self.addCleanup(successor.close)
        self.assertIsNotNone(successor.descriptor)

    def test_detached_worker_deadline_releases_the_lease_and_cannot_publish(self):
        worker = self._real_worker(delay=3, deadline=0.4)
        self.assertEqual(2, worker.process.wait(timeout=4))
        self.assertFalse((self.root / "published").exists())
        self.assertEqual("failed", read_status(worker.env).state)
        successor = StartupRefresh(self.root, Path("/next-worker"), {})
        self.addCleanup(successor.close)
        self.assertIsNotNone(successor.descriptor)

    def test_worker_crash_releases_the_lease_for_retry(self):
        worker = self._real_worker(delay=3)
        worker.process.send_signal(signal.SIGKILL)
        worker.process.wait(timeout=4)
        successor = StartupRefresh(self.root, Path("/next-worker"), {})
        self.addCleanup(successor.close)
        self.assertIsNotNone(successor.descriptor)

    def test_closed_launcher_refresh_publishes_catalog_and_reopened_picker_joins(self):
        from rofi_agent_plus.cache import CacheStore

        self.refresh.close()
        source = Path(__file__).resolve().parents[1]
        gate = self.root / "release-inventory"
        self.addCleanup(lambda: gate.touch(exist_ok=True))
        cache = CacheStore(self.root / "catalog")
        helper = self.root / "application-worker"
        helper.write_text(
            f"#!{sys.executable} -B\n"
            "import sys,time\n"
            "from pathlib import Path\n"
            "from types import SimpleNamespace\n"
            f"sys.path[:0] = {[str(source), str(source / 'tests')]!r}\n"
            "from rofi_agent_plus import app\n"
            "from rofi_agent_plus.cache import CacheStore\n"
            "from test_direct_ui import FakeTransport,fixture_inventory,host_result\n"
            "transport=FakeTransport((host_result(fixture_inventory()),))\n"
            "original=transport.read_fleet\n"
            "def read_fleet(**kwargs):\n"
            " deadline=time.monotonic()+3\n"
            f" while not Path({str(gate)!r}).exists():\n"
            "  if time.monotonic()>=deadline: raise TimeoutError('test gate expired')\n"
            "  time.sleep(.01)\n"
            " return original(**kwargs)\n"
            "transport.read_fleet=read_fleet\n"
            f"context=SimpleNamespace(cache=CacheStore(Path({str(cache.root)!r})),transport=transport)\n"
            "app.build_context=lambda:context\n"
            "raise SystemExit(app.diagnostic_main(['_direct-refresh']))\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        # The parent really exits. Only Rofi is replaced with an immediately
        # closed inert process; the detached helper executes the real app path.
        driver = (
            "import sys,subprocess\n"
            "from pathlib import Path\n"
            "from types import SimpleNamespace\n"
            f"sys.path.insert(0,{str(source)!r})\n"
            "from rofi_agent_plus import launcher\n"
            "from rofi_agent_plus.cache import CacheStore\n"
            "original=subprocess.Popen\n"
            "class ClosedRofi:\n"
            " def poll(self): return 0\n"
            " def wait(self): return 0\n"
            "def launch(argv,**kwargs):\n"
            " return ClosedRofi() if argv[0]=='rofi' else original(argv,**kwargs)\n"
            f"context=SimpleNamespace(cache=CacheStore(Path({str(cache.root)!r})),config=SimpleNamespace(desktop=SimpleNamespace(catalog_ttl_seconds=30)))\n"
            "launcher.build_context=lambda env:context\n"
            "launcher.run_rofi=lambda *args,**kwargs:print('inert frame')\n"
            f"launcher._agent_executable=lambda:Path({str(helper)!r})\n"
            "launcher.subprocess.Popen=launch\n"
            "raise SystemExit(launcher.main([]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", driver], capture_output=True, text=True, timeout=4
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNone(cache.load())
        joined = StartupRefresh(cache.root, Path("/must-not-launch"), dict(os.environ))
        self.addCleanup(joined.close)
        self.assertIsNone(joined.descriptor)
        self.assertTrue(read_status(joined.env).pending)
        gate.touch()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and read_status(joined.env).pending:
            time.sleep(0.01)
        self.assertEqual("complete", read_status(joined.env).state)
        self.assertTrue(cache.load().hosts[0].providers[0].targets)

    def test_lock_symlink_or_unsafe_file_is_refused(self):
        self.refresh.close()
        lock = self.root / "startup-refresh.lock"
        lock.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "not private"):
            StartupRefresh(self.root, Path("/artifact/agent"), {})
        lock.unlink()
        lock.symlink_to(self.root / "foreign")
        with self.assertRaises(OSError):
            StartupRefresh(self.root, Path("/artifact/agent"), {})

    def test_detached_worker_rejects_unrelated_inherited_descriptor(self):
        foreign = self.root / "foreign-lock"
        with foreign.open("w") as stream:
            foreign.chmod(0o600)
            env = dict(self.env, **{LOCK_FD_ENV: str(stream.fileno())})
            with self.assertRaisesRegex(ValueError, "does not match"):
                with refresh_worker(env):
                    self.fail("unrelated file descriptor admitted a detached worker")

    def test_refresh_command_publishes_success_and_failure(self):
        context = Mock()
        with (
            patch.object(app, "build_context", return_value=context),
            patch.dict(os.environ, self.env),
        ):
            self.assertEqual(app.diagnostic_main(["_direct-refresh"]), 0)
            self.assertEqual(read_status(self.env).state, "complete")
            context.cache.refresh.side_effect = CacheError("controlled refresh failure")
            with patch("sys.stderr"):
                self.assertEqual(app.diagnostic_main(["_direct-refresh"]), 1)
            self.assertEqual(read_status(self.env).state, "failed")
            self.assertIn("controlled refresh failure", read_status(self.env).message)


if __name__ == "__main__":
    unittest.main()
