from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from rofi_agent_plus.rofi import PickerRow, order_rows
from rofi_agent_plus.usage import (
    SNAPSHOT_ENV,
    SNAPSHOT_NAME,
    WEEK_MS,
    Usage,
    UsageStore,
    frozen_statistics,
)

MOMENT = 10 * WEEK_MS


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = UsageStore(self.root / "state")

    def test_week_cutoff_future_events_and_retained_recency(self):
        self.assertEqual({}, self.store.statistics(at=MOMENT))
        self.assertFalse(self.store.path.exists())
        for index, at in enumerate((MOMENT + 1, MOMENT - WEEK_MS, MOMENT - WEEK_MS + 1, MOMENT)):
            self.assertTrue(self.store.record("a" * 64, f"{index:032x}", at, at=at))
        before = self.store.path.read_bytes()
        self.assertEqual(Usage(2, 0), self.store.statistics(at=MOMENT)["a" * 64])
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(
            Usage(0, MOMENT + 1), self.store.statistics(at=MOMENT + WEEK_MS + 1)["a" * 64]
        )

    def test_duplicate_and_expired_tokens_do_not_advance_history(self):
        token = "b" * 32
        self.assertTrue(self.store.record("a" * 64, token, MOMENT, at=MOMENT))
        before = self.store.path.read_bytes()
        self.assertFalse(self.store.record("a" * 64, token, MOMENT, at=MOMENT + 1))
        self.assertFalse(self.store.record("c" * 64, token, MOMENT, at=MOMENT + 1))
        self.assertFalse(self.store.record("a" * 64, token, MOMENT, at=MOMENT + WEEK_MS))
        self.assertEqual(before, self.store.path.read_bytes())

    def test_concurrent_updates_and_duplicate_requests(self):
        def record(index):
            return self.store.record("a" * 64, f"{index % 16:032x}", MOMENT, at=MOMENT)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(record, range(32)))
        self.assertEqual(16, sum(results))
        self.assertEqual(Usage(16, MOMENT), self.store.statistics(at=MOMENT)["a" * 64])
        self.assertEqual(0o600, self.store.path.stat().st_mode & 0o777)
        self.assertEqual(0o700, self.store.root.stat().st_mode & 0o777)

    def test_invalid_and_symlinked_history_is_preserved_and_nonfatal(self):
        self.store.root.mkdir(mode=0o700)
        self.store.path.write_text('{"schemaVersion":99}', encoding="utf-8")
        self.store.path.chmod(0o600)
        before = self.store.path.read_bytes()
        self.assertEqual({}, self.store.statistics(at=MOMENT))
        self.assertFalse(self.store.record("a" * 64, "b" * 32, MOMENT, at=MOMENT))
        self.assertEqual(before, self.store.path.read_bytes())
        original = self.root / "original"
        self.store.path.rename(original)
        self.store.path.symlink_to(original)
        self.assertEqual({}, self.store.statistics(at=MOMENT))
        self.assertFalse(self.store.record("a" * 64, "b" * 32, MOMENT, at=MOMENT))
        self.assertTrue(self.store.path.is_symlink())
        self.assertEqual(before, original.read_bytes())

    def test_bounds_refuse_writes_without_changing_valid_history(self):
        self.assertTrue(self.store.record("a" * 64, "b" * 32, MOMENT, at=MOMENT))
        before = self.store.path.read_bytes()
        with patch("rofi_agent_plus.usage.MAX_EVENTS", 1):
            self.assertFalse(self.store.record("a" * 64, "c" * 32, MOMENT, at=MOMENT))
        self.assertEqual(before, self.store.path.read_bytes())

    def test_snapshot_stays_frozen_until_another_dialog(self):
        self.assertTrue(self.store.record("a" * 64, "b" * 32, MOMENT, at=MOMENT))
        dialog = self.root / "dialog"
        dialog.mkdir(mode=0o700)
        env = {SNAPSHOT_ENV: str(dialog / SNAPSHOT_NAME)}
        with patch("rofi_agent_plus.usage.now_ms", return_value=MOMENT):
            first = frozen_statistics(self.store, env)
        self.assertTrue(self.store.record("c" * 64, "d" * 32, MOMENT + 1, at=MOMENT + 1))
        with patch("rofi_agent_plus.usage.now_ms", return_value=MOMENT + WEEK_MS + 1):
            self.assertEqual(first, frozen_statistics(self.store, env))
            fresh = frozen_statistics(self.store, {})
        self.assertEqual(Usage(0, MOMENT), fresh["a" * 64])
        self.assertIn("c" * 64, fresh)
        self.assertEqual(1, json.loads((dialog / SNAPSHOT_NAME).read_text())["schemaVersion"])

    def test_snapshot_cannot_write_to_an_arbitrary_environment_path(self):
        path = self.root / "ordinary-settings.json"
        self.assertEqual({}, frozen_statistics(self.store, {SNAPSHOT_ENV: str(path)}))
        self.assertFalse(path.exists())


class UsageOrderingTests(unittest.TestCase):
    def rows(self):
        return tuple(
            PickerRow(
                "instance",
                f"{target}:{number}",
                target,
                target,
                target_id=target * 64,
                instance_id=str(number),
                selectable=True,
            )
            for target, number in (("a", 1), ("a", 2), ("b", 1), ("c", 1))
        )

    def test_weekly_count_overrides_recency_and_keeps_target_windows_together(self):
        usage = {"a" * 64: Usage(1, 99), "b" * 64: Usage(3, 1), "c" * 64: Usage(1, 100)}
        self.assertEqual(
            ["b:1", "a:1", "a:2", "c:1"], [r.key for r in order_rows(self.rows(), usage)]
        )

    def test_all_visible_zero_falls_back_to_recency_but_hidden_usage_does_not_disable_it(self):
        usage = {"a" * 64: Usage(0, 10), "b" * 64: Usage(0, 20), "d" * 64: Usage(99, 30)}
        self.assertEqual(
            ["b:1", "a:1", "a:2", "c:1"], [r.key for r in order_rows(self.rows(), usage)]
        )
        self.assertEqual(self.rows(), order_rows(self.rows(), {}))

    def test_diagnostics_keep_their_nonselectable_slots(self):
        diagnostic = PickerRow("status", None, "offline", "offline")
        rows = (diagnostic, *self.rows())
        ranked = order_rows(rows, {"b" * 64: Usage(1, 1)})
        self.assertIs(diagnostic, ranked[0])
        self.assertEqual("b:1", ranked[1].key)


if __name__ == "__main__":
    unittest.main()
