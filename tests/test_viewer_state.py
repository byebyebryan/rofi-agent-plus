from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import viewer_state as viewers

BACKEND = {
    "kind": "contract",
    "capability": "host-mesh-v1+tmux-session-v1",
    "meshRevision": "sha256:" + "a" * 64,
}
REFERENCE = {
    "hostId": "beta",
    "serverGeneration": "tmux-v1:one",
    "sessionId": "$7",
    "createdAt": 30,
}


def inventory(*, observed_at: int = 1_000) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "meshRevision": BACKEND["meshRevision"],
        "viewerEndpoint": {"hostId": "alpha", "observedAt": observed_at},
        "hosts": [
            {
                "hostId": "beta",
                "status": "ok",
                "sessions": [
                    {
                        **REFERENCE,
                        "localViewer": {"state": "open", "confidence": "matched"},
                    }
                ],
            }
        ],
    }


def snapshot() -> dict[str, object]:
    row = {
        "hostId": "beta",
        "kind": "codex",
        "id": "conversation",
        "active": True,
        "sourceObservation": "current",
        "tmux": {**REFERENCE, "meshRevision": BACKEND["meshRevision"]},
    }
    return {
        "backend": dict(BACKEND),
        "generatedAt": 50,
        "lastRefresh": {"completedAt": 50},
        "hosts": {"beta": {"generatedAt": 50, "sessions": [row]}},
        "sessions": [row],
        "fixedPreview": {"targets": [dict(REFERENCE)]},
    }


class ViewerStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = viewers.ViewerStateStore(Path(self.temporary.name) / "viewers")
        self.scope = viewers.make_scope("fingerprint", BACKEND, "alpha", environ={})
        patcher = mock.patch("rofi_agent_plus.viewer_state._millis", return_value=1_000)
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_observation_overlay_preserves_provider_and_frozen_batch_state(self) -> None:
        self.assertTrue(self.store.ingest(inventory(), self.scope))
        source = snapshot()
        before = copy.deepcopy(source)
        decorated = self.store.decorate(source, self.scope)
        self.assertEqual(before, source)
        self.assertEqual(source["generatedAt"], decorated["generatedAt"])
        self.assertEqual(source["lastRefresh"], decorated["lastRefresh"])
        self.assertIs(source["fixedPreview"], decorated["fixedPreview"])
        self.assertEqual(
            {"state": "open", "confidence": "matched"}, decorated["sessions"][0]["localViewer"]
        )
        self.assertEqual(
            decorated["sessions"][0]["localViewer"],
            decorated["hosts"]["beta"]["sessions"][0]["localViewer"],
        )
        self.assertTrue(decorated["_viewerWatch"])

    def test_full_reference_context_and_freshness_gate_positive_state(self) -> None:
        self.store.ingest(inventory(), self.scope)
        for field, replacement in (
            ("createdAt", 31),
            ("sessionId", "$8"),
            ("serverGeneration", "tmux-v1:two"),
            ("hostId", "gamma"),
            ("meshRevision", "sha256:" + "b" * 64),
        ):
            with self.subTest(field=field):
                source = snapshot()
                source["sessions"][0]["tmux"][field] = replacement
                self.assertEqual(
                    "unknown",
                    self.store.decorate(source, self.scope)["sessions"][0]["localViewer"]["state"],
                )
        for flag, value in (
            ("tmuxStale", True),
            ("tmuxAmbiguous", True),
            ("sourceObservation", "retained"),
        ):
            with self.subTest(flag=flag):
                source = snapshot()
                source["sessions"][0][flag] = value
                self.assertEqual(
                    "unknown",
                    self.store.decorate(source, self.scope)["sessions"][0]["localViewer"]["state"],
                )
        for now in (999, 11_000):
            self.assertEqual(
                "unknown",
                self.store.decorate(snapshot(), self.scope, now=now)["sessions"][0]["localViewer"][
                    "state"
                ],
            )
        changed = {**self.scope, "desktop": "b" * 64}
        self.assertIsNone(self.store.current(changed))

    def test_desktop_socket_epoch_changes_scope_without_storing_environment(self) -> None:
        socket = Path(self.temporary.name) / "niri.sock"
        socket.write_text("one")
        environment = {"NIRI_SOCKET": str(socket), "DISPLAY": "private-display"}
        first = viewers.make_scope("fingerprint", BACKEND, "alpha", environ=environment)
        socket.unlink()
        socket.write_text("two")
        second = viewers.make_scope("fingerprint", BACKEND, "alpha", environ=environment)
        self.assertNotEqual(first["desktop"], second["desktop"])
        self.assertNotIn("private-display", json.dumps(first))
        self.assertNotIn(str(socket), json.dumps(first))

    def test_request_is_deduplicated_and_late_publication_is_rejected(self) -> None:
        def command(identifier: str) -> list[str]:
            return ["fixture", "_viewer-refresh", identifier]
        with mock.patch("rofi_agent_plus.viewer_state.subprocess.Popen") as spawn:
            with mock.patch.dict(os.environ, {"ROFI_RETV": "1", "ROFI_DATA": "private"}):
                self.assertTrue(self.store.request(self.scope, command))
                first = self.store.pending(self.scope)
                self.assertTrue(self.store.request(self.scope, command))
            spawn.assert_called_once()
            self.assertFalse(any(key.startswith("ROFI_") for key in spawn.call_args.kwargs["env"]))
            self.assertEqual(0o700, stat.S_IMODE(self.store.root.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(self.store.request_path.stat().st_mode))
            self.clock.return_value = 1_000 + viewers.REQUEST_SECONDS * 1000
            self.store.request(self.scope, command, spawn=False)
            second = self.store.pending(self.scope)
            self.assertNotEqual(first["requestId"], second["requestId"])
            self.assertFalse(self.store.publish(first["requestId"], self.scope, inventory()))
            self.assertIsNone(self.store.current(self.scope))
            self.assertTrue(
                self.store.publish(
                    second["requestId"], self.scope, inventory(observed_at=self.clock.return_value)
                )
            )
            self.assertIsNotNone(self.store.current(self.scope))
            self.assertIsNone(self.store.pending(self.scope))

    def test_new_scope_supersedes_helper_and_failure_does_not_retain_open(self) -> None:
        self.store.request(self.scope, lambda _: ["fixture"], spawn=False)
        first = self.store.pending(self.scope)
        changed = {**self.scope, "desktop": "b" * 64}
        self.store.request(changed, lambda _: ["fixture"], spawn=False)
        second = self.store.pending(changed)
        self.assertFalse(self.store.publish(first["requestId"], self.scope, inventory()))
        self.assertTrue(self.store.publish(second["requestId"], changed, inventory()))
        self.clock.return_value = 11_000
        self.store.request(changed, lambda _: ["fixture"], spawn=False)
        third = self.store.pending(changed)
        self.assertTrue(self.store.publish(third["requestId"], changed, error="refresh_failed"))
        self.assertEqual([], self.store.current(changed)["rows"])
        self.assertEqual(
            "unknown",
            self.store.decorate(snapshot(), changed)["sessions"][0]["localViewer"]["state"],
        )

    def test_bulk_ingest_cannot_overwrite_newer_pending_or_completed_observation(self) -> None:
        self.clock.return_value = 2_000
        self.store.request(self.scope, lambda _: ["fixture"], spawn=False)
        request = self.store.pending(self.scope)
        self.assertFalse(self.store.ingest(inventory(observed_at=1_999), self.scope))
        self.assertIsNotNone(self.store.pending(self.scope))
        self.assertTrue(
            self.store.publish(request["requestId"], self.scope, inventory(observed_at=2_000))
        )
        self.assertFalse(self.store.ingest(inventory(observed_at=1_000), self.scope))
        self.assertEqual(2_000, self.store.current(self.scope)["observedAt"])

    def test_invalid_endpoint_observations_and_private_records_fail_closed(self) -> None:
        wrong_endpoint = inventory()
        wrong_endpoint["viewerEndpoint"]["hostId"] = "beta"
        with self.assertRaises(viewers.ViewerStateError):
            self.store.ingest(wrong_endpoint, self.scope)
        for value in (
            {"state": []},
            {"state": "open", "confidence": []},
            {"state": "none", "confidence": "matched"},
            {"state": "open"},
            {"state": "unknown", "reason": "bad\nreason"},
        ):
            with self.subTest(value=value), self.assertRaises(viewers.ViewerStateError):
                viewers.observation(value)
        self.store._ensure_root()
        os.mkfifo(self.store.snapshot_path, 0o600)
        self.assertIsNone(self.store.current(self.scope))
        self.store.snapshot_path.unlink()
        other = Path(self.temporary.name) / "unrelated"
        other.write_text("{}\n")
        self.store.snapshot_path.symlink_to(other)
        self.assertIsNone(self.store.current(self.scope))
        self.store.snapshot_path.unlink()
        self.store.snapshot_path.write_text('{"version":1,"version":1}\n')
        self.store.snapshot_path.chmod(0o600)
        self.assertIsNone(self.store.current(self.scope))


if __name__ == "__main__":
    unittest.main()
