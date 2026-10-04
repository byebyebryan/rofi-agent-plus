"""The picker delegates observation through its prepared public CLI backend."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from rofi_agent_plus import app, engine

ID = "11111111-1111-1111-1111-111111111111"


class ActiveCommandTest(unittest.TestCase):
    def test_active_uses_full_host_inventory_and_preserves_namespace_identity(self):
        rows = [
            {
                "hostId": "snap",
                "kind": "codex",
                "id": ID,
                "namespace": namespace,
                "nativeIdKind": "thread",
                "active": True,
            }
            for namespace in ("config-a", "config-b")
        ]
        unknown = {**rows[0], "id": "22222222-2222-2222-2222-222222222222", "active": None}
        snapshot = {
            "sessions": rows[:1],
            "hosts": {"snap": {"sessions": [*rows, unknown]}},
            "errors": [],
        }
        with (
            patch.object(app, "CacheStore") as store,
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            store.return_value.refresh.return_value = snapshot
            self.assertEqual(0, app.diagnostic_main(["active"]))
        result = json.loads(output.getvalue())
        self.assertEqual(["config-a", "config-b"], [row["namespace"] for row in result["sessions"]])
        store.return_value.refresh.assert_called_once()

    def test_engine_has_no_provider_or_generic_lifecycle_surface(self):
        for name in (
            "provider_active_snapshot",
            "merge_provider_results",
            "_process_table",
            "_thread_id_for_process",
            "HostTarget",
            "SshPolicy",
            "launch_attach",
            "focus_existing_window",
        ):
            self.assertFalse(hasattr(engine, name), name)
