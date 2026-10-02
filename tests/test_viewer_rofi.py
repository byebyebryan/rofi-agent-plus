"""Viewer timers and short state labels do not change session actions."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import rofi
from rofi_agent_plus.cache import CacheStore, PresentationContext
from rofi_agent_plus.config import PickerConfig
from rofi_agent_plus.contract_backend import ContractBackend, _tmux_reference, parse_mesh
from rofi_agent_plus.view_preferences import ViewPreference, ViewPreferenceStore

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


class ViewerRofiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = PickerConfig()
        self.store = CacheStore(Path(self.temporary.name) / "cache")
        self.backend = ContractBackend("ssh-plus", "tmux-plus")
        self.backend.mesh = parse_mesh(json.loads((FIXTURES / "mesh-v1.json").read_text()))
        self.context = PresentationContext(
            self.config.fingerprint, self.backend.identity, selected=self.backend
        )
        self.inventory = json.loads((FIXTURES / "tmux-inventory-v1.json").read_text())
        reference = self.inventory["hosts"][0]["sessions"][0]
        self.row = {
            "kind": "codex",
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Viewer state",
            "host": "alpha",
            "hostId": "alpha",
            "cwd": "/work",
            "recencyAt": 100,
            "updatedAt": 100,
            "active": True,
            "activityState": "active",
            "sourceObservation": "current",
            "contractMode": True,
            "backend": self.backend.identity,
            "tmuxProviderOptionVerified": True,
            "tmux": _tmux_reference(reference, self.backend.mesh.revision),
        }
        stages = {
            stage: {"outcome": "ok", "lastAttemptAt": 100, "lastSuccessAt": 100}
            for stage in ("codex", "claude", "opencode", "activity", "tmux")
        }
        self.snapshot = {
            "version": 4,
            "fingerprint": self.config.fingerprint,
            "backend": self.backend.identity,
            "generatedAt": 100,
            "lastRefresh": {"attemptedAt": 100, "completedAt": 100, "outcome": "complete"},
            "hostCatalog": [
                {"hostId": "alpha", "display": "Alpha", "local": True},
                {"hostId": "beta", "display": "Beta", "local": False},
            ],
            "hosts": {"alpha": {"sessions": [self.row], "observations": stages, "errors": []}},
            "sessions": [self.row],
            "errors": [],
        }
        self.store.write(self.snapshot)
        self.provider_bytes = self.store.snapshot_path.read_bytes()
        self.scope = self.store.viewer_scope(self.config, self.context)
        self.preferences = ViewPreferenceStore(Path(self.temporary.name) / "preferences")
        clock = mock.patch("rofi_agent_plus.rofi.time.time", return_value=100)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        millis = mock.patch("rofi_agent_plus.viewer_state._millis", return_value=100_000)
        self.millis = millis.start()
        self.addCleanup(millis.stop)

    def callback(self, retv, **environment):
        with mock.patch.object(self.store, "presentation_context", return_value=self.context):
            with mock.patch.object(
                self.store, "refresh", side_effect=AssertionError("provider discovery forbidden")
            ):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(
                        0,
                        rofi.run_rofi(
                            {"ROFI_RETV": str(retv), **environment},
                            store=self.store,
                            config=self.config,
                            preference_store=self.preferences,
                        ),
                    )
                    return output.getvalue()

    def header(self, frame, key):
        return frame.split(f"\x00{key}\x1f", 1)[1].split("\t", 1)[0].split("\n", 1)[0]

    def data(self, frame):
        return self.header(frame, "data")

    def publish(self):
        self.inventory["viewerEndpoint"] = {"hostId": "alpha", "observedAt": 100_000}
        self.inventory["hosts"][0]["sessions"][0]["localViewer"] = {
            "state": "open",
            "confidence": "confirmed",
        }
        viewers = self.store.viewer_store()
        pending = viewers.pending(self.scope)
        if pending is None:
            viewers.request(self.scope, lambda identifier: [], spawn=False)
            pending = viewers.pending(self.scope)
        self.assertTrue(viewers.publish(pending["requestId"], self.scope, self.inventory))

    def test_short_mapping_keeps_unknown_activity_and_waiting(self):
        cases = [
            (True, "active", {"state": "none"}, "Active"),
            (False, "idle", {"state": "none"}, "Inactive"),
            (True, "active", {"state": "open", "confidence": "confirmed"}, "Open"),
            (True, "active", {"state": "open", "confidence": "matched"}, "Open?"),
            (False, "idle", {"state": "open", "confidence": "matched"}, "Inactive · Open?"),
            (True, "active", {"state": "unknown"}, "Active · ?"),
            (True, "waiting", {"state": "open", "confidence": "confirmed"}, "Waiting · Open"),
            (False, "unknown", {"state": "none"}, "Activity unknown"),
        ]
        for active, activity, viewer, label in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    label,
                    rofi._activity_label(
                        {
                            **self.row,
                            "active": active,
                            "activityState": activity,
                            "localViewer": viewer,
                        }
                    ),
                )
        self.assertEqual(
            "Activity unknown",
            rofi._activity_label(
                {
                    **self.row,
                    "sourceObservation": "retained",
                    "localViewer": {"state": "open", "confidence": "confirmed"},
                }
            ),
        )

    def test_cached_initial_and_timed_completion_keep_action_selection_and_provider_time(self):
        with mock.patch("rofi_agent_plus.viewer_state.subprocess.Popen") as spawn:
            initial = self.callback(0)
            spawn.assert_called_once()
            self.assertEqual("_viewer-refresh", spawn.call_args.args[0][-2])
            self.assertIn("Active · ?", initial)
            self.assertIsNone(rofi._parse_continuation_state(self.data(initial)).refresh_deadline)
            self.publish()
            next_frame = self.callback(
                rofi.ROFI_RETV_CUSTOM_19,
                ROFI_DATA=self.data(initial),
                ROFI_INFO=rofi.selection_payload(self.row),
            )
            spawn.assert_called_once()
        self.assertIn("Open", next_frame)
        self.assertTrue(
            any("Open" in value.split("\x1f", 1)[0] for value in next_frame.split("meta\x1f")[1:])
        )
        self.assertIn("\x00new-selection\x1f1", next_frame)
        self.assertIn("\x00keep-filter\x1ftrue", next_frame)
        self.assertEqual(
            rofi._action_message(rofi.ACTION_RESUME, ""),
            self.header(next_frame, "message"),
        )
        self.assertEqual(self.provider_bytes, self.store.snapshot_path.read_bytes())

    def test_tab_and_pages_are_cache_only_and_expiry_changes_only_display(self):
        self.publish()
        with mock.patch.object(
            self.store, "presentation_context", side_effect=AssertionError("prepare forbidden")
        ):
            with mock.patch("rofi_agent_plus.viewer_state.subprocess.Popen") as spawn:
                for retv in (rofi.ROFI_RETV_CUSTOM_7, rofi.ROFI_RETV_CUSTOM_2):
                    with mock.patch("sys.stdout", new_callable=io.StringIO):
                        self.assertEqual(
                            0,
                            rofi.run_rofi(
                                {"ROFI_RETV": str(retv)},
                                store=self.store,
                                config=self.config,
                                preference_store=self.preferences,
                            ),
                        )
                spawn.assert_not_called()
        self.clock.return_value = 110
        self.millis.return_value = 110_000
        with mock.patch("rofi_agent_plus.viewer_state.subprocess.Popen") as spawn:
            expired = self.callback(rofi.ROFI_RETV_CUSTOM_7)
            self.assertIn("Active · ?", expired)
            spawn.assert_not_called()
            timed = self.callback(rofi.ROFI_RETV_CUSTOM_19, ROFI_DATA=self.data(expired))
            self.callback(rofi.ROFI_RETV_CUSTOM_19, ROFI_DATA=self.data(timed))
            spawn.assert_called_once()
        self.assertEqual(self.provider_bytes, self.store.snapshot_path.read_bytes())

    def test_failed_observation_has_ten_second_retry_cadence(self):
        self.store.viewer_store().request(self.scope, lambda identifier: [], spawn=False)
        pending = self.store.viewer_store().pending(self.scope)
        self.store.viewer_store().publish(pending["requestId"], self.scope, error="fixture_failure")
        with mock.patch("rofi_agent_plus.viewer_state.subprocess.Popen") as spawn:
            frame = self.callback(rofi.ROFI_RETV_CUSTOM_19)
            self.callback(rofi.ROFI_RETV_CUSTOM_19, ROFI_DATA=self.data(frame))
            spawn.assert_not_called()
            self.clock.return_value = 110
            self.millis.return_value = 110_000
            self.callback(rofi.ROFI_RETV_CUSTOM_19, ROFI_DATA=self.data(frame))
            spawn.assert_called_once()

    def test_unknown_cold_frame_defers_provider_discovery_and_keeps_mesh_pages(self):
        self.store.snapshot_path.unlink()
        with mock.patch.object(self.store, "spawn_background", return_value=True) as provider:
            with mock.patch("rofi_agent_plus.viewer_state.subprocess.Popen") as viewer:
                frame = self.callback(0)
        provider.assert_called_once()
        viewer.assert_not_called()
        self.assertIn("Checking sessions", frame)
        self.assertIn("No sessions", frame)
        self.assertIn("Agents", frame)
        self.assertIsNotNone(rofi._parse_continuation_state(self.data(frame)).refresh_deadline)
        self.assertFalse(self.store.snapshot_path.exists())
        control = frame.split("All active sessions", 1)[1].split("\t", 1)[0]
        self.assertIn("nonselectable\x1ftrue", control)

    def test_cold_completion_restores_last_conversation_instead_of_batch_control(self):
        second = {**self.row, "id": "22222222-2222-2222-2222-222222222222", "name": "Z saved"}
        self.preferences.save(ViewPreference(last_used=rofi._session_identity(second)))
        self.store.snapshot_path.unlink()
        with mock.patch.object(self.store, "spawn_background", return_value=True):
            initial = self.callback(0)
        self.snapshot["sessions"].append(second)
        self.snapshot["hosts"]["alpha"]["sessions"].append(second)
        self.store.write(self.snapshot)
        with mock.patch("rofi_agent_plus.viewer_state.subprocess.Popen"):
            completed = self.callback(rofi.ROFI_RETV_CUSTOM_19, ROFI_DATA=self.data(initial))
        self.assertIn("\x00new-selection\x1f2", completed)

    def test_observation_completion_preserves_frozen_preview_count_tint_and_action_bar(self):
        target = {
            "hostId": "alpha",
            "host": "Alpha",
            "kind": "codex",
            "id": self.row["id"],
            "name": self.row["name"],
            "reference": {
                "hostId": self.row["hostId"],
                "meshRevision": self.backend.mesh.revision,
                **{
                    key: self.row["tmux"][key]
                    for key in ("serverGeneration", "sessionId", "createdAt")
                },
            },
            "requiredOption": ["@codex_thread_id", self.row["id"]],
            "mode": "open",
            "viewers": [],
        }
        record = {
            "action": "resume",
            "previewId": "a" * 32,
            "scope": "All",
            "targets": [target],
            "exclusions": [],
        }
        original = json.dumps(record, sort_keys=True)
        state = rofi.BatchUIState("preview", None, "resume", "a" * 32)

        def frame():
            snapshot = rofi._presentation_snapshot(self.store, self.config, self.context)
            return rofi._render_batch_inline(
                snapshot,
                rofi.ContinuationState(),
                rofi.NavigationState(),
                "resume",
                None,
                state,
                record=record,
            )

        before = frame()
        self.publish()
        after = frame()
        for value in (before, after):
            self.assertIn("Confirm Resume (1)", value)
            self.assertIn('<span foreground="#42a5f5">Viewer state</span>', value)
        self.assertEqual(self.header(before, "message"), self.header(after, "message"))
        self.assertEqual(original, json.dumps(record, sort_keys=True))
        self.assertEqual(self.provider_bytes, self.store.snapshot_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
