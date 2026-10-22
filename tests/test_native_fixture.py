"""Native-fixture cleanup must refuse changed ownership and process identity."""

import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class NativeFixtureTest(unittest.TestCase):
    def setUp(self):
        self.api = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "scripts/native-fixture")
        )

    def fixture(self, root):
        value = {
            "root": str(root),
            "uid": os.getuid(),
            "provider": "codex",
            "processes": [],
            "credentials": [],
            "ordinaryHashes": {},
        }
        (root / "fixture.json").write_text(json.dumps(value))
        return value

    def test_replaced_runtime_cannot_be_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = self.fixture(root)
            value["processes"] = [{"pid": os.getpid(), "ticks": "wrong", "boot": "wrong"}]
            (root / "fixture.json").write_text(json.dumps(value))
            with mock.patch("subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "incarnation_changed"):
                    self.api["cleanup"](root)
                run.assert_not_called()

    def test_nonprivate_root_cannot_be_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            root.chmod(0o755)
            with mock.patch("subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "not_private"):
                    self.api["cleanup"](root)
                run.assert_not_called()

    def test_changed_root_scope_cannot_be_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = self.fixture(root)
            value["root"] = "/another-fixture"
            (root / "fixture.json").write_text(json.dumps(value))
            with mock.patch("subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "ownership_changed"):
                    self.api["cleanup"](root)
                run.assert_not_called()

    def test_prepare_refuses_an_existing_home(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("shutil.copyfile") as copy:
                with self.assertRaises(FileExistsError):
                    self.api["prepare"](root, "codex", "proof", Path("/candidate/helper"))
                copy.assert_not_called()

    def test_prepare_failure_after_credential_copy_removes_its_new_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ordinary = base / "ordinary"
            (ordinary / ".claude").mkdir(parents=True)
            (ordinary / ".claude/.credentials.json").write_text('{"synthetic":true}')
            (ordinary / ".claude.json").write_text("invalid json")
            root = base / "new-owned-fixture"
            with (
                mock.patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(ordinary))),
                mock.patch("subprocess.run", return_value=SimpleNamespace(stdout="2.1.306")),
            ):
                with self.assertRaises(json.JSONDecodeError):
                    self.api["prepare"](root, "claude", "proof", Path("/candidate/helper"))
            self.assertFalse(root.exists())
            self.assertTrue((ordinary / ".claude/.credentials.json").exists())
