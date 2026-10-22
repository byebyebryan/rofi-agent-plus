from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from rofi_agent_plus.cache import CacheError, CacheStore
from rofi_agent_plus.config import DesktopConfig, DirectConfig
from rofi_agent_plus.desktop import ProcessIdentity, Window, observe_process
from rofi_agent_plus.model import (
    EntryReference,
    Inventory,
    Target,
    parse_entry_reference,
    parse_inventory,
)
from rofi_agent_plus.registry import Registry, target_storage_key
from rofi_agent_plus.rofi import (
    PickerContext,
    PickerState,
    _state_json,
    build_rows,
    run_rofi,
)
from rofi_agent_plus.transport import FleetSnapshot, HostResult, TransportDiagnostic
from rofi_agent_plus.view_preferences import ViewPreference, ViewPreferenceStore

FIXTURES = Path(__file__).parent / "fixtures" / "direct-host"


def fixture_inventory(
    *, codex_status: str = "complete", claude_status: str = "complete", empty=()
) -> Inventory:
    value = json.loads((FIXTURES / "inventory.json").read_text(encoding="utf-8"))
    observed_at = time.time_ns() // 1_000_000
    value["observedAt"] = observed_at
    value["providerCoverage"][0]["status"] = codex_status
    value["providerCoverage"][0]["observedAt"] = observed_at
    value["providerCoverage"][0]["reason"] = None if codex_status == "complete" else "probe_failed"
    value["providerCoverage"][1]["status"] = claude_status
    value["providerCoverage"][1]["observedAt"] = observed_at
    value["providerCoverage"][1]["reason"] = None if claude_status == "complete" else "probe_failed"
    value["targets"] = [
        target for target in value["targets"] if target["key"]["provider"] not in set(empty)
    ]
    for target in value["targets"]:
        target["daemon"]["observedAt"] = observed_at
    return parse_inventory(value)


def host_result(inventory: Inventory, *, status: str = "complete") -> HostResult:
    return HostResult(
        inventory.host_id, "Starship", True, status, inventory.observed_at, None, inventory
    )


class FakeTransport:
    def __init__(self, results: tuple[HostResult, ...] = ()):
        self.results = results
        self.calls = 0
        self.current_mesh_revision = "sha256:" + "a" * 64

    def read_fleet(self, *, on_result=None):
        self.calls += 1
        for result in self.results:
            if on_result is not None:
                on_result(result)
        return FleetSnapshot(
            self.current_mesh_revision,
            max((result.observed_at for result in self.results), default=1),
            self.results,
        )


class FakeDesktop:
    def __init__(self, generation: str, windows=()):
        self.generation = generation
        self.env = {"PATH": "/usr/bin:/bin"}
        self.visible = tuple(windows)
        self.window_calls = 0

    def windows(self):
        self.window_calls += 1
        return self.visible

    @staticmethod
    def match(windows, marker, terminal):
        matching = [window for window in windows if window.app_id == marker]
        return matching[0] if len(matching) == 1 else None


class CacheStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = CacheStore(Path(self.temporary.name) / "cache")

    def test_partial_and_transport_failure_preserve_targets_and_last_complete(self):
        complete = fixture_inventory()
        self.store.publish_host(host_result(complete), mesh_revision="sha256:" + "a" * 64)
        original = self.store.load()
        assert original is not None
        codex = next(item for item in original.hosts[0].providers if item.provider == "codex")
        self.assertEqual(codex.last_complete_at, complete.observed_at)

        partial = fixture_inventory(codex_status="partial", empty={"codex"})
        self.store.publish_host(
            host_result(partial, status="partial"), mesh_revision="sha256:" + "b" * 64
        )
        after_partial = self.store.load()
        assert after_partial is not None
        codex_after = next(
            item for item in after_partial.hosts[0].providers if item.provider == "codex"
        )
        self.assertEqual(
            [target.key.identity for target in codex_after.targets],
            [complete.targets[0].key.identity],
        )
        self.assertEqual(codex_after.last_complete_at, codex.last_complete_at)
        self.assertEqual(codex_after.status, "partial")

        failed = HostResult(
            "snap",
            "Starship",
            True,
            "error",
            complete.observed_at + 1000,
            None,
            None,
            TransportDiagnostic("inventory_timeout", "bounded test failure"),
        )
        self.store.publish_host(failed, mesh_revision="sha256:" + "c" * 64)
        after_error = self.store.load()
        assert after_error is not None
        preserved = next(
            item for item in after_error.hosts[0].providers if item.provider == "codex"
        )
        self.assertEqual(
            [target.key.identity for target in preserved.targets],
            [complete.targets[0].key.identity],
        )
        self.assertEqual(preserved.last_complete_at, codex.last_complete_at)

    def test_complete_empty_provider_removes_catalog_targets(self):
        initial = fixture_inventory()
        self.store.publish_host(host_result(initial), mesh_revision="sha256:" + "a" * 64)
        empty = fixture_inventory(empty={"codex"})
        self.store.publish_host(host_result(empty), mesh_revision="sha256:" + "b" * 64)
        snapshot = self.store.load()
        assert snapshot is not None
        codex = next(item for item in snapshot.hosts[0].providers if item.provider == "codex")
        self.assertEqual(codex.status, "complete")
        self.assertEqual(codex.targets, ())

    def test_per_host_callback_publishes_before_the_fleet_finishes(self):
        inventory = fixture_inventory()
        result = host_result(inventory)
        transport = FakeTransport((result,))
        seen = []
        self.store.refresh(transport, on_host=seen.append)
        self.assertEqual(seen, [result])
        snapshot = self.store.load()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.mesh_revision, transport.current_mesh_revision)

    def test_finish_fleet_marks_removed_and_preserves_current_mesh_order(self):
        local = fixture_inventory()
        self.store.publish_host(host_result(local), mesh_revision="sha256:" + "a" * 64)
        remote_value = local.to_json()
        remote_value["self"]["hostId"] = "peer"
        remote_value["targets"] = []
        for coverage in remote_value["providerCoverage"]:
            coverage["status"] = "complete"
        remote = parse_inventory(remote_value)
        remote_result = HostResult(
            "peer", "Peer", False, "complete", remote.observed_at, "peer-route", remote
        )
        store_transport = FakeTransport((host_result(local), remote_result))
        self.store.refresh(store_transport)
        final = self.store.finish_fleet(
            FleetSnapshot("sha256:" + "d" * 64, 2000, (host_result(local),))
        )
        self.assertEqual([host.host_id for host in final.hosts], ["snap", "peer"])
        self.assertEqual(final.hosts[1].status, "removed")

    def test_unknown_schema_and_unsafe_files_are_rejected(self):
        self.store.publish_host(
            host_result(fixture_inventory()), mesh_revision="sha256:" + "a" * 64
        )
        value = self.store.load().to_json()
        value["schemaVersion"] = 2
        self.store.path.write_text(json.dumps(value), encoding="utf-8")
        self.store.path.chmod(0o600)
        with self.assertRaisesRegex(CacheError, "version is unsupported"):
            self.store.load()

    def test_cache_path_is_versioned_and_private(self):
        root = Path(self.temporary.name) / "xdg-cache"
        store = CacheStore(env={"XDG_CACHE_HOME": str(root)})
        self.assertEqual(store.path, root / "rofi-agent-plus" / "direct-v1" / "catalog.json")
        store.publish_host(host_result(fixture_inventory()), mesh_revision="sha256:" + "a" * 64)
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(store.root.stat().st_mode & 0o777, 0o700)


class ViewPreferenceTests(unittest.TestCase):
    def test_strict_target_instance_identity_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ViewPreferenceStore(temporary)
            value = ViewPreference("a" * 64, "b" * 32)
            self.assertTrue(store.save(value))
            self.assertEqual(store.load(), value)
            store.path.write_text('{"schemaVersion":2}', encoding="utf-8")
            store.path.chmod(0o600)
            self.assertEqual(store.load(), ViewPreference())


class PickerRowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inventory = fixture_inventory()
        self.target: Target = self.inventory.targets[0]
        self.reference: EntryReference = parse_entry_reference(
            json.loads((FIXTURES / "entry-reference.json").read_text(encoding="utf-8"))
        )
        self.target_id = target_storage_key(self.target.key.identity)
        self.snapshot_store = CacheStore(self.root / "cache")
        self.snapshot_store.publish_host(
            host_result(self.inventory, status="partial"), mesh_revision="sha256:" + "a" * 64
        )
        self.snapshot = self.snapshot_store.load()
        self.generation = "e" * 64
        self.registry = Registry(self.generation, self.root / "registry")
        self.owner = observe_process(os.getpid())
        assert self.owner is not None

    def live_record(self, title="terminal <span>title</span>", request="1" * 32):
        record, _created = self.registry.reserve(
            target_id=self.target_id,
            target=self.target.wire,
            reference=self.reference.wire,
            request_id=request,
            additional=True,
            owner=self.owner,
            transport={"schemaVersion": 1, "kind": "local"},
        )
        record = self.registry.update(
            self.target_id,
            record.instance_id,
            state="live",
            terminal=self.owner.wire(),
            wrapper=self.owner.wire(),
            client=self.owner.wire(),
        )
        return record, Window(42, self.owner.pid, record.marker, title, True)

    def test_live_instance_suppresses_launcher_row_and_title_is_escaped(self):
        record, window = self.live_record("title </span><script>bad</script>")
        desktop = FakeDesktop(self.generation, (window,))
        rows = build_rows(self.snapshot, registry=self.registry, desktop=desktop, windows=(window,))
        instances = [row for row in rows if row.kind == "instance"]
        targets = [row for row in rows if row.kind == "target" and row.target_id == self.target_id]
        self.assertEqual([row.instance_id for row in instances], [record.instance_id])
        self.assertEqual(targets, [])
        self.assertIn("title </span><script>bad</script>", instances[0].meta)
        frame = __import__("rofi_agent_plus.rofi", fromlist=["_render"])._render(
            rows,
            PickerState(selected_key=instances[0].key, request_id="f" * 32),
            preserve=True,
            select=True,
        )
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", frame)
        self.assertNotIn('"title </span>', frame)
        self.assertNotIn("bad</script>", json.dumps(instances[0].info))

    def test_registered_window_remains_visible_without_catalog(self):
        record, window = self.live_record()
        rows = build_rows(
            None,
            registry=self.registry,
            desktop=FakeDesktop(self.generation, (window,)),
            windows=(window,),
        )
        self.assertTrue(any(row.instance_id == record.instance_id for row in rows))
        self.assertTrue(
            any(row.kind == "status" and "No host catalog" in row.display for row in rows)
        )

    def test_multiple_windows_are_adjacent_and_use_registry_ordinals(self):
        first, window_one = self.live_record("first", "1" * 32)
        second, window_two = self.live_record("second", "2" * 32)
        windows = (window_one, window_two)
        rows = build_rows(
            self.snapshot,
            registry=self.registry,
            desktop=FakeDesktop(self.generation, windows),
            windows=windows,
        )
        instances = [
            row for row in rows if row.kind == "instance" and row.target_id == self.target_id
        ]
        self.assertEqual(
            [row.instance_id for row in instances], [first.instance_id, second.instance_id]
        )
        self.assertEqual([row.record.value["ordinal"] for row in instances], [1, 2])
        self.assertNotIn("target", [row.kind for row in rows if row.target_id == self.target_id])

    def test_cached_target_and_registered_window_survive_host_failure_and_removal(self):
        record, _window = self.live_record()
        failed = HostResult(
            "snap",
            "Starship",
            True,
            "error",
            self.inventory.observed_at + 10,
            None,
            None,
            TransportDiagnostic("inventory_timeout", "offline fixture"),
        )
        self.snapshot_store.publish_host(failed, mesh_revision="sha256:" + "b" * 64)
        offline = self.snapshot_store.load()
        rows = build_rows(
            offline,
            registry=self.registry,
            desktop=FakeDesktop(self.generation),
            windows=(),
        )
        self.assertTrue(any(row.instance_id == record.instance_id for row in rows))
        self.assertFalse(
            any(row.kind == "target" and row.target_id == self.target_id for row in rows)
        )
        self.assertTrue(
            any("last-known targets" in row.display for row in rows if row.kind == "status")
        )

        removed = self.snapshot_store.finish_fleet(FleetSnapshot("sha256:" + "c" * 64, 3000, ()))
        rows = build_rows(
            removed,
            registry=self.registry,
            desktop=FakeDesktop(self.generation),
            windows=(),
        )
        self.assertTrue(any(row.instance_id == record.instance_id for row in rows))
        self.assertTrue(
            any("not in current Host Mesh" in row.display for row in rows if row.kind == "status")
        )

    def test_non_live_instances_and_provider_status_are_nonselectable(self):
        record, _ = self.registry.reserve(
            target_id=self.target_id,
            target=self.target.wire,
            reference=self.reference.wire,
            request_id="2" * 32,
            additional=True,
            owner=self.owner,
            transport={"schemaVersion": 1, "kind": "local"},
        )
        rows = build_rows(
            self.snapshot, registry=self.registry, desktop=FakeDesktop(self.generation), windows=()
        )
        row = next(item for item in rows if item.instance_id == record.instance_id)
        self.assertFalse(row.selectable)
        frame = __import__("rofi_agent_plus.rofi", fromlist=["_render"])._render(
            rows, PickerState(request_id="f" * 32), preserve=True, select=False
        )
        self.assertIn("nonselectable\x1ftrue", frame)


class FakeActions:
    def __init__(
        self,
        registry: Registry,
        target_id: str,
        target: Target,
        reference: EntryReference,
        owner: ProcessIdentity,
    ):
        self.registry = registry
        self.target_id = target_id
        self.target = target
        self.reference = reference
        self.owner = owner
        self.requests: dict[str, object] = {}
        self.dispatches = 0
        self.focuses: list[tuple[str, str]] = []
        self.additional: list[tuple[bool, str]] = []

    def open_target(self, target, reference, *, additional=False, request_id=None):
        self.additional.append((additional, request_id))
        request_id = request_id or "0" * 32
        if request_id in self.requests:
            return self.requests[request_id]
        record, created = self.registry.reserve(
            target_id=self.target_id,
            target=target.wire,
            reference=reference.wire,
            request_id=request_id,
            additional=additional,
            owner=self.owner,
            transport={"schemaVersion": 1, "kind": "local"},
        )
        if created:
            self.dispatches += 1
            record = self.registry.update(self.target_id, record.instance_id, state="dispatching")
        self.requests[request_id] = record
        return record

    def focus_instance(self, target_id, instance_id):
        self.focuses.append((target_id, instance_id))
        return next(
            item for item in self.registry.records(target_id) if item.instance_id == instance_id
        )


class PickerCallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inventory = fixture_inventory()
        self.target: Target = self.inventory.targets[0]
        self.reference: EntryReference = parse_entry_reference(
            json.loads((FIXTURES / "entry-reference.json").read_text(encoding="utf-8"))
        )
        self.target_id = target_storage_key(self.target.key.identity)
        self.snapshot_store = CacheStore(self.root / "cache")
        self.snapshot_store.publish_host(
            host_result(self.inventory), mesh_revision="sha256:" + "a" * 64
        )
        self.snapshot = self.snapshot_store.load()
        self.generation = "e" * 64
        self.registry = Registry(self.generation, self.root / "registry")
        self.owner = observe_process(os.getpid())
        assert self.owner is not None

    def _context(self):
        env = {"PATH": "/usr/bin:/bin"}
        desktop = FakeDesktop(self.generation)
        transport = FakeTransport()
        preferences = ViewPreferenceStore(self.root / "state")
        actions = FakeActions(
            self.registry, self.target_id, self.target, self.reference, self.owner
        )
        context = PickerContext(
            env,
            DirectConfig(DesktopConfig(), ()),
            None,
            transport,
            self.snapshot_store,
            preferences,
            desktop,
            None,
            self.registry,
            actions,
        )
        return context, transport, actions

    def test_timeout_is_disabled_and_legacy_callback_is_local_only(self):
        context, transport, _actions = self._context()
        data = _state_json(PickerState(selected_key=f"t:{self.target_id}", request_id="a" * 32))
        output = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(output):
            self.assertEqual(
                0,
                run_rofi(
                    {"ROFI_RETV": "28", "ROFI_DATA": data},
                    context=context,
                ),
            )
        self.assertEqual(transport.calls, 0)
        self.assertEqual(context.desktop.window_calls, 1)
        self.assertIn("keep-filter\x1ftrue", output.getvalue())
        self.assertIn("keep-selection\x1ftrue", output.getvalue())
        self.assertIn("timeout { delay: 0.0;", output.getvalue())
        self.assertNotIn("timerPhase", output.getvalue())

    def test_alt_r_refreshes_synchronously_and_renders_the_new_snapshot(self):
        context, transport, _actions = self._context()
        changed = self.inventory.to_json()
        changed["targets"][0]["label"] = "Fresh catalog label"
        fresh = parse_inventory(changed)
        transport.results = (host_result(fresh),)
        row = next(row for row in build_rows(self.snapshot) if row.target_id == self.target_id)
        state = _state_json(PickerState(selected_key=row.key, request_id="a" * 32))
        output = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(output):
            self.assertEqual(
                0,
                run_rofi(
                    {
                        "ROFI_RETV": "10",
                        "ROFI_DATA": state,
                        "ROFI_INFO": json.dumps(row.info),
                    },
                    context=context,
                ),
            )

        self.assertEqual(transport.calls, 1)
        self.assertIn("Fresh catalog label", output.getvalue())
        self.assertIn("Host inventory refreshed", output.getvalue())
        self.assertIn("new-selection\x1f0", output.getvalue())

    def test_missing_catalog_is_refreshed_before_initial_frame(self):
        context, transport, _actions = self._context()
        context.cache = CacheStore(self.root / "cold-cache")
        transport.results = (host_result(self.inventory),)
        output = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(output):
            self.assertEqual(0, run_rofi({"ROFI_RETV": "0"}, context=context))

        self.assertEqual(transport.calls, 1)
        self.assertIn(self.target.label, output.getvalue())
        self.assertIsNotNone(context.cache.load())

    def test_enter_replay_reuses_request_id_and_registry_dispatch(self):
        context, _transport, actions = self._context()
        row = next(
            row
            for row in build_rows(self.snapshot)
            if row.kind == "target" and row.target_id == self.target_id
        )
        data = _state_json(PickerState(selected_key=row.key, request_id="b" * 32))
        env = {"ROFI_RETV": "1", "ROFI_DATA": data, "ROFI_INFO": json.dumps(row.info)}
        output = io.StringIO()
        import contextlib

        for _ in range(2):
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, run_rofi(env, context=context))
        self.assertEqual(actions.dispatches, 1)
        self.assertEqual([item[1] for item in actions.additional], ["b" * 32])

    def test_tab_cycles_additional_and_refreshes_action_token(self):
        context, transport, _actions = self._context()
        row = next(
            row
            for row in build_rows(self.snapshot)
            if row.kind == "target" and row.target_id == self.target_id
        )
        data = _state_json(PickerState(selected_key=row.key, request_id="c" * 32))
        output = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(output):
            run_rofi(
                {"ROFI_RETV": "16", "ROFI_DATA": data, "ROFI_INFO": json.dumps(row.info)},
                context=context,
            )
        frame = output.getvalue()
        self.assertIn("prompt\x1fOpen another window", frame)
        self.assertIn("keep-filter\x1ftrue", frame)
        self.assertIn("new-selection\x1f0", frame)
        self.assertEqual(transport.calls, 0)

    def test_initial_frame_reuses_last_target_selection(self):
        context, transport, _actions = self._context()
        context.preferences.save(ViewPreference(self.target_id, None))
        output = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(output):
            run_rofi({"ROFI_RETV": "0"}, context=context)
        self.assertIn("new-selection\x1f0", output.getvalue())
        self.assertEqual(transport.calls, 0)

    def test_stale_window_info_never_falls_back_to_target_open(self):
        context, transport, actions = self._context()
        info = {
            "schemaVersion": 1,
            "kind": "instance",
            "targetId": self.target_id,
            "instanceId": "d" * 32,
        }
        data = _state_json(
            PickerState(selected_key=f"i:{self.target_id}:{'d' * 32}", request_id="e" * 32)
        )
        output = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(output):
            run_rofi(
                {"ROFI_RETV": "1", "ROFI_DATA": data, "ROFI_INFO": json.dumps(info)},
                context=context,
            )
        self.assertEqual(actions.dispatches, 0)
        self.assertEqual(actions.focuses, [])
        self.assertEqual(transport.calls, 1)
        self.assertIn("Selection is stale", output.getvalue())

    def test_typed_custom_input_is_inert_and_does_not_refresh(self):
        context, transport, actions = self._context()
        data = _state_json(PickerState(request_id="f" * 32))
        output = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(output):
            self.assertEqual(
                0,
                run_rofi(
                    {"ROFI_RETV": "1", "ROFI_DATA": data, "ROFI_INFO": "typed input"},
                    context=context,
                ),
            )

        self.assertEqual(transport.calls, 0)
        self.assertEqual(actions.dispatches, 0)
        self.assertEqual(actions.focuses, [])
        self.assertIn("Custom input is ignored", output.getvalue())


if __name__ == "__main__":
    unittest.main()
