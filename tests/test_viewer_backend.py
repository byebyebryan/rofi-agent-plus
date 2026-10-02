"""Public bulk viewer refreshes stay separate from provider discovery."""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import app, viewer_state
from rofi_agent_plus.cache import CacheStore
from rofi_agent_plus.config import PickerConfig
from rofi_agent_plus.contract_backend import (
    CommandOutput,
    ContractBackend,
    parse_mesh,
)

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


class ViewerBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = PickerConfig()
        self.store = CacheStore(Path(self.temporary.name) / "cache")
        self.store.ensure_root()
        self.store.snapshot_path.write_bytes(b"provider snapshot must not change")
        self.mesh = parse_mesh(json.loads((FIXTURES / "mesh-v1.json").read_text()))
        self.payload = json.loads((FIXTURES / "tmux-inventory-v1.json").read_text())
        self.now = time.time_ns() // 1_000_000
        self.payload["viewerEndpoint"] = {"hostId": "alpha", "observedAt": self.now}
        self.payload["hosts"][0]["sessions"][0]["localViewer"] = {
            "state": "open",
            "confidence": "matched",
        }
        self.calls: list[list[str]] = []
        self.backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=self.runner)
        self.backend.mesh = self.mesh
        remote = mock.patch.object(
            self.backend,
            "_remote_provider_results",
            return_value=(None, [], {"sessions": []}, {"sessions": []}, {}),
        )
        remote.start()
        self.addCleanup(remote.stop)
        self.scope = viewer_state.make_scope(
            self.config.fingerprint, self.backend.identity, "alpha"
        )
        self.viewers = self.store.viewer_store()

    def runner(self, argv, **_kwargs):
        self.assertEqual("inventory", argv[1])
        self.calls.append(list(argv))
        encoded = (json.dumps(self.payload) + "\n").encode()
        return CommandOutput(tuple(argv), 0, encoded.decode(), "", stdout_bytes=encoded)

    def request(self):
        self.viewers.request(self.scope, lambda identifier: ["fixture", identifier], spawn=False)
        return self.viewers.pending(self.scope)["requestId"]

    def test_finite_helper_runs_one_public_bulk_call_and_preserves_provider_cache(self) -> None:
        request = self.request()
        with mock.patch.object(
            self.store, "_select_backend", return_value=(self.backend, self.backend.identity)
        ) as select:
            self.assertTrue(self.store.refresh_viewers(self.config, request))
        self.assertEqual(2, select.call_count)
        self.assertEqual(1, len(self.calls))
        self.assertIn("--with-viewers", self.calls[0])
        self.assertIn("--panes", self.calls[0])
        self.assertEqual(
            self.mesh.revision, self.calls[0][self.calls[0].index("--mesh-revision") + 1]
        )
        self.assertEqual(
            b"provider snapshot must not change", self.store.snapshot_path.read_bytes()
        )
        self.assertEqual(
            "matched", self.viewers.current(self.scope)["rows"][0]["viewer"]["confidence"]
        )
        with mock.patch.object(self.store, "_select_backend") as select:
            self.assertFalse(self.store.refresh_viewers(self.config, request))
            select.assert_not_called()

    def test_invalid_enrichment_invalid_owner_facts_and_wrong_endpoint_fail_to_unknown(
        self,
    ) -> None:
        base = copy.deepcopy(self.payload)
        for mutation in ("endpoint", "confidence", "panes"):
            self.payload = copy.deepcopy(base)
            if mutation == "endpoint":
                self.payload["viewerEndpoint"]["hostId"] = "beta"
            elif mutation == "confidence":
                self.payload["hosts"][0]["sessions"][0]["localViewer"]["confidence"] = []
            else:
                self.payload["hosts"][0]["sessions"][0]["panes"][0]["pid"] = True
            with self.subTest(mutation=mutation):
                self.viewers.snapshot_path.unlink(missing_ok=True)
                request = self.request()
                with mock.patch.object(
                    self.store,
                    "_select_backend",
                    return_value=(self.backend, self.backend.identity),
                ):
                    self.assertTrue(self.store.refresh_viewers(self.config, request))
                self.assertEqual([], self.viewers.current(self.scope)["rows"])
                self.assertEqual(
                    b"provider snapshot must not change", self.store.snapshot_path.read_bytes()
                )

    def test_mesh_or_desktop_change_during_observation_rejects_publication(self) -> None:
        request = self.request()
        changed = {**self.backend.identity, "meshRevision": "sha256:" + "a" * 64}
        with mock.patch.object(
            self.store,
            "_select_backend",
            side_effect=[(self.backend, self.backend.identity), (self.backend, changed)],
        ):
            self.assertFalse(self.store.refresh_viewers(self.config, request))
        self.assertIsNone(self.viewers.current(self.scope))
        with mock.patch.object(
            self.store, "_select_backend", return_value=(self.backend, self.backend.identity)
        ):
            with mock.patch(
                "rofi_agent_plus.viewer_state.desktop_context",
                side_effect=[self.scope["desktop"], self.scope["desktop"], "b" * 64],
            ):
                self.assertFalse(self.store.refresh_viewers(self.config, request))
        self.assertIsNone(self.viewers.current(self.scope))

    def test_normal_discovery_reuses_inventory_and_enrichment_failure_keeps_owner_facts(
        self,
    ) -> None:
        with mock.patch.object(self.backend, "_active", return_value=(None, {})):
            with mock.patch.object(
                self.backend,
                "_provider_results",
                return_value=([], {"sessions": []}, {"sessions": []}),
            ):
                events = self.backend._once(self.config)
        self.assertEqual(1, len(self.calls))
        self.assertTrue(events)
        self.assertIsNotNone(self.backend.viewer_inventory_result)
        self.store._ingest_viewers(self.config, self.backend)
        self.assertEqual("open", self.viewers.current(self.scope)["rows"][0]["viewer"]["state"])
        self.payload["viewerEndpoint"]["hostId"] = "beta"
        with mock.patch.object(self.backend, "_active", return_value=(None, {})):
            with mock.patch.object(
                self.backend,
                "_provider_results",
                return_value=([], {"sessions": []}, {"sessions": []}),
            ):
                events = self.backend._once(self.config)
        self.assertIsNone(self.backend.viewer_inventory_result)
        self.assertFalse(
            any(
                error.get("stage") == "tmux"
                for event in events
                for error in event.get("errors", [])
            )
        )
        self.store._ingest_viewers(self.config, self.backend)
        self.assertEqual([], self.viewers.current(self.scope)["rows"])
        self.assertEqual(
            b"provider snapshot must not change", self.store.snapshot_path.read_bytes()
        )

    def test_explicit_helper_dispatch_precedes_rofi_environment(self) -> None:
        with mock.patch.dict(os.environ, {"ROFI_RETV": "1", "ROFI_DATA": "native callback"}):
            with mock.patch.object(app, "CacheStore", return_value=self.store):
                with mock.patch.object(app, "load_config", return_value=self.config):
                    with mock.patch.object(
                        self.store, "refresh_viewers", return_value=True
                    ) as refresh:
                        with mock.patch.object(app, "run_rofi") as rofi:
                            self.assertEqual(0, app.main(["_viewer-refresh", "a" * 32]))
        refresh.assert_called_once_with(self.config, "a" * 32)
        rofi.assert_not_called()
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(2, app.main(["_viewer-refresh"]))


if __name__ == "__main__":
    unittest.main()
