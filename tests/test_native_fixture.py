"""Ownership guards for explicit disposable native-proof tooling."""

import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class NativeFixtureTest(unittest.TestCase):
    def setUp(self):
        self.api = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "scripts/native-fixture")
        )

    def test_restart_rejects_unstopped_runtime_before_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.json").write_text(json.dumps({"pid": 1}))
            with mock.patch("subprocess.Popen") as spawn:
                with self.assertRaisesRegex(RuntimeError, "must_be_stopped"):
                    self.api["start"](root, "snap")
                spawn.assert_not_called()

    def test_replaced_runtime_cannot_be_signalled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.json").write_text(
                json.dumps({"pid": os.getpid(), "ticks": "wrong", "namespaces": {}})
            )
            with mock.patch("os.kill") as kill:
                with self.assertRaisesRegex(RuntimeError, "incarnation_changed"):
                    self.api["stop"](root)
                kill.assert_not_called()

    def test_stopped_runtime_cannot_be_entered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.json").write_text(json.dumps({"stopped": True}))
            with mock.patch("os.execv") as enter:
                with self.assertRaisesRegex(RuntimeError, "fixture_stopped"):
                    self.api["enter"](root, ["true"])
                enter.assert_not_called()
