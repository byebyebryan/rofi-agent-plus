from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rofi_agent_plus.desktop import Window
from rofi_agent_plus.window_titles import TITLES_ENV, TITLES_NAME, freeze_titles


class WindowTitleTests(unittest.TestCase):
    def test_titles_stay_steady_but_presence_and_identity_remain_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {TITLES_ENV: str(root / TITLES_NAME)}
            original = Window(1, 42, "org.agentplus." + "a" * 32, "⠋ Working", False)
            self.assertEqual((original,), freeze_titles((original,), env))
            current = replace(original, title="⠙ Working", focused=True)
            self.assertEqual(
                (replace(current, title=original.title),), freeze_titles((current,), env)
            )
            self.assertEqual((), freeze_titles((), env))
            replacement = replace(current, pid=43, title="Replacement client")
            self.assertEqual((replacement,), freeze_titles((replacement,), env))
            # Reopening uses a fresh launch directory and samples the new title.
            fresh = root / "next-dialog"
            fresh.mkdir(mode=0o700)
            self.assertEqual(
                (current,), freeze_titles((current,), {TITLES_ENV: str(fresh / TITLES_NAME)})
            )

    def test_untrusted_title_files_are_ignored_without_rewriting_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / TITLES_NAME
            env = {TITLES_ENV: str(path)}
            window = Window(1, 42, "org.agentplus." + "a" * 32, "Current title", False)
            path.write_text("malformed")
            path.chmod(0o600)
            self.assertEqual((window,), freeze_titles((window,), env))
            self.assertEqual("malformed", path.read_text())
            path.unlink()
            other = root / "other"
            other.write_text("preserve")
            path.symlink_to(other)
            self.assertEqual((window,), freeze_titles((window,), env))
            self.assertTrue(path.is_symlink())
            self.assertEqual("preserve", other.read_text())


if __name__ == "__main__":
    unittest.main()
