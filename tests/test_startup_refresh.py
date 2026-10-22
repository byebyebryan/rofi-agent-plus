"""Dialog-scoped refresh tickets and terminal outcomes."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rofi_agent_plus import app
from rofi_agent_plus.cache import CacheError
from rofi_agent_plus.registry import atomic_json
from rofi_agent_plus.startup_refresh import (
    STATUS_ENV,
    StartupRefresh,
    finish_refresh,
    read_status,
)


class StartupRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.refresh = StartupRefresh(self.root, Path("/artifact/agent"), {})
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
