"""Owned selector restore and drift rejection; providers are never invoked."""

import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

loader = SourceFileLoader(
    "managed_selection",
    str(Path(__file__).resolve().parents[1] / "scripts/managed-selection-rollback"),
)
module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(module)


class ManagedSelectionTests(unittest.TestCase):
    def fixture(self, root):
        home, source = root / "home", root / "source"
        home.mkdir()
        source.mkdir()
        for name in module.SOURCES:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("prior\n")
        for name in module.LINKS:
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to("prior")
        backup = root / "backup"
        module.capture(backup, home, source, None)
        for name in module.SOURCES:
            (source / name).write_text("selected\n")
        for name in module.LINKS:
            path = home / name
            path.unlink()
            path.symlink_to("selected")
        module.arm(backup, home, source)
        return home, source, backup

    def test_restore_only_captured_selectors(self):
        with tempfile.TemporaryDirectory() as directory:
            home, source, backup = self.fixture(Path(directory))
            unrelated = home / "unrelated"
            unrelated.write_text("preserved\n")
            module.rollback(backup, home, source, False)
            self.assertEqual((source / module.SOURCES[0]).read_text(), "prior\n")
            self.assertEqual((home / module.LINKS[0]).readlink(), Path("prior"))
            self.assertEqual(unrelated.read_text(), "preserved\n")

    def test_drift_rejected_before_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            home, source, backup = self.fixture(Path(directory))
            (source / module.SOURCES[-1]).write_text("concurrent\n")
            with self.assertRaisesRegex(ValueError, "managed_sources_changed"):
                module.rollback(backup, home, source, False)
            self.assertEqual((source / module.SOURCES[0]).read_text(), "selected\n")

    def test_corrupt_backup_rejected_before_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            home, source, backup = self.fixture(Path(directory))
            (backup / "source" / module.SOURCES[-1]).write_text("corrupt\n")
            with self.assertRaisesRegex(ValueError, "backup_source_changed"):
                module.rollback(backup, home, source, False)
            self.assertEqual((source / module.SOURCES[0]).read_text(), "selected\n")
