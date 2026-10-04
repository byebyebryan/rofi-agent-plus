"""Public evidence stays independent through consumer cache failures."""

from __future__ import annotations

import copy
import unittest

from test_contract_lifecycle import row

from rofi_agent_plus.cache import _merge_host_snapshot
from rofi_agent_plus.rofi import _workspace_label


class ObserverCacheTest(unittest.TestCase):
    def test_partial_inventory_keeps_missing_row_stale_without_renewing_clocks(self):
        original = row()
        stamp = original["phase"]["observedAt"]
        previous = {"generatedAt": 1, "sessions": [original], "errors": []}
        current = {"generatedAt": 2, "sessions": [], "errors": [], "incompleteProviders": ["codex"]}
        retained = _merge_host_snapshot(previous, current)["sessions"][0]
        self.assertEqual("retained", retained["sourceObservation"])
        self.assertIsNone(retained["active"])
        self.assertEqual("unknown", retained["phase"]["value"])
        self.assertEqual("waiting", retained["phase"]["lastKnownValue"])
        self.assertEqual(stamp, retained["phase"]["observedAt"])
        self.assertEqual("current", original["phase"]["health"])
        self.assertIsNone(retained["recencyAt"])
        complete = {**current, "incompleteProviders": []}
        self.assertEqual([], _merge_host_snapshot(previous, complete)["sessions"])

    def test_history_or_provider_warning_does_not_replace_current_dimensions(self):
        old = row()
        fresh = copy.deepcopy(old)
        fresh["phase"]["value"] = "blocked"
        fresh["phase"]["observedAt"] += 5000
        fresh["activityState"] = "blocked"
        current = {
            "generatedAt": 2,
            "sessions": [fresh],
            "errors": [{"stage": "threads", "message": "partial"}],
            "incompleteProviders": ["codex"],
        }
        merged = _merge_host_snapshot({"generatedAt": 1, "sessions": [old], "errors": []}, current)[
            "sessions"
        ][0]
        self.assertEqual("blocked", merged["phase"]["value"])
        self.assertEqual(fresh["phase"]["observedAt"], merged["phase"]["observedAt"])
        self.assertEqual("current", merged["sourceObservation"])

    def test_explicit_workspace_context_is_display_metadata(self):
        selected = row()
        selected["workspace"] = {
            "health": "current",
            "rootKey": "code",
            "relativePath": "agent-observer",
            "projectKey": "observer",
        }
        self.assertEqual("code:agent-observer", _workspace_label(selected))
        selected["workspace"]["health"] = "unavailable"
        self.assertEqual("/work", _workspace_label(selected))
