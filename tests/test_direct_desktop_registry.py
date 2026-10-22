from __future__ import annotations

import os
import socket
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from rofi_agent_plus.desktop import (
    Desktop,
    DesktopError,
    ProcessIdentity,
    Window,
    kitty_command,
    observe_process,
    validate_kitty_layout,
)
from rofi_agent_plus.registry import (
    MAX_RECORDS,
    Registry,
    RegistryError,
    atomic_json,
    target_storage_key,
)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = Registry("a" * 64, Path(self.temporary.name))
        self.owner = observe_process(os.getpid())
        self.target_id = "b" * 64

    def reserve(self, request="1" * 32, additional=False, target_id=None):
        return self.registry.reserve(
            target_id=target_id or self.target_id,
            target={"label": "Test"},
            reference={"targetKey": {}},
            request_id=request,
            additional=additional,
            owner=self.owner,
            transport={"kind": "local"},
        )

    def test_first_open_race_is_coalesced(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda n: self.reserve(f"{n:032x}"), range(1, 5)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({record.instance_id for record, _ in results}), 1)

    def test_additional_requests_and_replay(self):
        first, created = self.reserve(additional=True)
        second, _ = self.reserve("2" * 32, additional=True)
        replay, created_again = self.reserve(additional=True)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.instance_id, replay.instance_id)
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual([first.value["ordinal"], second.value["ordinal"]], [1, 2])
        self.registry.update(self.target_id, first.instance_id, state="exited")
        replay, created_again = self.reserve(additional=True)
        self.assertEqual(replay.state, "exited")
        self.assertFalse(created_again)

    def test_multiple_new_instances_require_row_refresh(self):
        self.reserve(additional=True)
        self.reserve("2" * 32, additional=True)
        with self.assertRaisesRegex(RegistryError, "multiple_instances_refresh"):
            self.reserve("3" * 32)

    def test_stable_ordinals_reuse_closed_number(self):
        first, _ = self.reserve(additional=True)
        second, _ = self.reserve("2" * 32, additional=True)
        self.registry.update(self.target_id, first.instance_id, state="exited")
        third, _ = self.reserve("3" * 32, additional=True)
        self.assertEqual(second.value["ordinal"], 2)
        self.assertEqual(third.value["ordinal"], 1)

    def test_corrupt_record_only_blocks_its_target(self):
        record, _ = self.reserve()
        path = self.registry.target_directory(self.target_id) / (record.instance_id + ".json")
        path.write_text('{"broken":true}\n')
        with self.assertRaisesRegex(RegistryError, "target_registry_corrupt"):
            self.registry.records(self.target_id)
        self.assertTrue(path.with_suffix(".quarantined").exists())
        healthy, created = self.reserve(target_id="c" * 64)
        self.assertTrue(created)
        self.assertEqual(healthy.target_id, "c" * 64)

    def test_symlink_is_not_followed(self):
        record, _ = self.reserve()
        directory = self.registry.target_directory(self.target_id)
        path = directory / (record.instance_id + ".json")
        saved = directory / "original"
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(RegistryError):
            self.registry.records(self.target_id)
        self.assertTrue(saved.exists())

    def test_missing_record_never_becomes_replacement(self):
        with self.assertRaisesRegex(RegistryError, "instance_missing"):
            self.registry.update(self.target_id, "f" * 32, state="live")

    def test_process_generation_change_is_not_live(self):
        original = self.owner
        replacement = ProcessIdentity(
            original.pid, str(int(original.start_ticks) + 1), original.boot_id
        )
        with patch("rofi_agent_plus.desktop.observe_process", return_value=replacement):
            self.assertFalse(original.alive())

    def test_dispatch_gap_stays_uncertain_and_first_open_reuses_it(self):
        record, _ = self.reserve()
        self.registry.update(self.target_id, record.instance_id, state="dispatching")
        desktop = type("FakeDesktop", (), {"generation": self.registry.generation})()
        with patch.object(ProcessIdentity, "alive", return_value=False):
            active = self.registry.reconcile(self.target_id, desktop, ())
        self.assertEqual(active[0].state, "uncertain")
        reused, created = self.reserve("2" * 32)
        self.assertFalse(created)
        self.assertEqual(reused.instance_id, record.instance_id)

    def test_abandoned_pre_dispatch_reservation_can_retire(self):
        self.reserve()
        desktop = type("FakeDesktop", (), {"generation": self.registry.generation})()
        with patch.object(ProcessIdentity, "alive", return_value=False):
            self.assertEqual(self.registry.reconcile(self.target_id, desktop, ()), [])
        _, created = self.reserve("2" * 32)
        self.assertTrue(created)

    def test_acknowledged_viewer_survives_picker_exit_before_terminal_registration(self):
        record, _ = self.reserve()
        wrapper = ProcessIdentity(999998, "88", self.owner.boot_id)
        self.registry.update(
            self.target_id,
            record.instance_id,
            state="dispatching",
            wrapper=wrapper.wire(),
        )
        desktop = type("FakeDesktop", (), {"generation": self.registry.generation})()

        def alive(identity):
            return identity.pid == wrapper.pid

        with patch.object(ProcessIdentity, "alive", alive):
            active = self.registry.reconcile(self.target_id, desktop, ())
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].state, "opening")
        self.assertIsNone(active[0].process("terminal"))

    def test_client_exit_retires_even_with_live_terminal_and_wrapper(self):
        record, _ = self.reserve()
        child = ProcessIdentity(999999, "1", self.owner.boot_id)
        self.registry.update(
            self.target_id,
            record.instance_id,
            state="live",
            terminal=self.owner.wire(),
            wrapper=self.owner.wire(),
            client=child.wire(),
        )
        desktop = type("FakeDesktop", (), {"generation": self.registry.generation})()
        self.assertEqual(self.registry.reconcile(self.target_id, desktop, ()), [])
        retired = self.registry.find_request(self.target_id, "1" * 32)
        self.assertIsNotNone(retired)
        self.assertEqual(retired.state, "exited")
        self.assertEqual(self.registry.records(self.target_id), [])

    def test_stale_reconciler_cannot_resurrect_retired_request(self):
        record, _ = self.reserve()
        self.registry.update(self.target_id, record.instance_id, state="failed")
        late = self.registry.update(
            self.target_id, record.instance_id, state="opening", terminal=self.owner.wire()
        )
        self.assertEqual(late.state, "failed")
        self.assertEqual(late.process("terminal"), self.owner)

    def test_terminal_archive_wins_over_crash_duplicate(self):
        record, _ = self.reserve()
        retired = self.registry.update(self.target_id, record.instance_id, state="failed")
        directory = self.registry.target_directory(self.target_id)
        active_path = directory / (record.instance_id + ".json")
        atomic_json(active_path, record.value)

        self.assertEqual(self.registry.records(self.target_id), [])
        self.assertFalse(active_path.exists())
        self.assertEqual(self.registry.find_request(self.target_id, "1" * 32), retired)

    def test_retired_requests_do_not_consume_active_capacity(self):
        records = [
            self.reserve(f"{number:032x}", additional=True)[0]
            for number in range(1, MAX_RECORDS + 1)
        ]
        with self.assertRaisesRegex(RegistryError, "target_record_limit"):
            self.reserve(f"{MAX_RECORDS + 1:032x}", additional=True)

        self.registry.update(self.target_id, records[0].instance_id, state="exited")

        replacement, created = self.reserve(f"{MAX_RECORDS + 1:032x}", additional=True)
        self.assertTrue(created)
        self.assertEqual(replacement.value["ordinal"], 1)
        retained = self.registry.find_request(self.target_id, f"{1:032x}")
        self.assertEqual(retained.state, "exited")

    def test_corrupt_archive_blocks_only_the_matching_request(self):
        first, _ = self.reserve()
        directory = self.registry.target_directory(self.target_id)
        retired = directory / "retired"
        retired.mkdir(mode=0o700)
        corrupt_request = retired / ("3" * 32 + ".json")
        descriptor = os.open(corrupt_request, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write('{"broken":true}\n')

        with self.assertRaisesRegex(RegistryError, "request_archive_corrupt"):
            self.registry.find_request(self.target_id, "3" * 32)
        unrelated, created = self.reserve("4" * 32, additional=True)
        self.assertTrue(created)
        self.assertEqual(unrelated.state, "reserved")
        self.assertEqual(self.registry.find_request(self.target_id, "1" * 32), first)

    def test_corrupt_matching_archive_isolated_from_other_requests(self):
        first, _ = self.reserve()
        retired = self.registry.target_directory(self.target_id) / "retired"
        retired.mkdir(mode=0o700)
        corrupt_request = retired / ("1" * 32 + ".json")
        descriptor = os.open(corrupt_request, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write('{"broken":true}\n')

        with self.assertRaisesRegex(RegistryError, "request_archive_corrupt"):
            self.reserve()
        unrelated, created = self.reserve("2" * 32, additional=True)
        self.assertTrue(created)
        self.assertEqual(unrelated.state, "reserved")
        active = {record.instance_id: record for record in self.registry.records(self.target_id)}
        self.assertEqual(len(active), 2)
        self.assertEqual(active[first.instance_id].state, "uncertain")
        desktop = type("FakeDesktop", (), {"generation": self.registry.generation})()
        reconciled = {
            record.instance_id: record
            for record in self.registry.reconcile(self.target_id, desktop, ())
        }
        self.assertEqual(reconciled[first.instance_id].state, "uncertain")

    def test_partial_process_updates_are_merged_under_lock(self):
        record, _ = self.reserve()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                executor.submit(
                    self.registry.update,
                    self.target_id,
                    record.instance_id,
                    **{key: self.owner.wire()},
                )
                for key in ("terminal", "wrapper")
            ]
            for result in results:
                result.result()
        current = self.registry.records(self.target_id)[0]
        self.assertEqual(current.process("terminal"), self.owner)
        self.assertEqual(current.process("wrapper"), self.owner)
        mode = (
            (self.registry.target_directory(self.target_id) / (record.instance_id + ".json"))
            .stat()
            .st_mode
        )
        self.assertEqual(mode & 0o777, 0o600)

    def test_target_digest_and_path_bounds(self):
        self.assertEqual(target_storage_key("sha256:" + self.target_id), self.target_id)
        with self.assertRaises(RegistryError):
            target_storage_key("../escape")
        with self.assertRaises(RegistryError):
            self.registry.target_directory("../escape")


class DesktopTests(unittest.TestCase):
    def test_exact_window_and_ambiguous_reuse(self):
        process = observe_process(os.getpid())
        window = Window(1, process.pid, "marker", "any title", False)
        self.assertEqual(Desktop.match((window,), "marker", process), window)
        with self.assertRaisesRegex(DesktopError, "ambiguous_terminal_layout"):
            Desktop.match(
                (window, Window(2, process.pid, "marker", "other", False)), "marker", process
            )
        with self.assertRaises(DesktopError):
            Desktop.match((Window(1, process.pid, "different", "title", True),), "marker", process)
        self.assertIsNone(Desktop.match((), "marker", process))

    def test_no_desktop_is_an_error(self):
        with self.assertRaisesRegex(DesktopError, "niri_socket_unavailable"):
            Desktop({})

    def test_terminal_options_cannot_override_identity(self):
        with patch("rofi_agent_plus.desktop.shutil.which", return_value="/usr/bin/kitty"):
            for option in ("--single-instance", "--class", "--session", "--title", "sh", "--hold"):
                with self.assertRaises(DesktopError):
                    kitty_command(
                        ["kitty", option], "marker", Path("/socket"), ["helper"], Path("/home")
                    )
            command = kitty_command(
                ["kitty", "-o", "font_size=12"],
                "marker",
                Path("/socket"),
                ["helper", "enter"],
                Path("/home"),
            )
        self.assertEqual(command[-2:], ["helper", "enter"])
        self.assertIn("--single-instance=no", command)
        self.assertIn("allow_remote_control=socket-only", command)

    def test_private_layout_requires_expected_foreground_and_single_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(control))
                process = observe_process(os.getpid())
                pane = {"pid": process.pid, "foreground_processes": [{"pid": process.pid}]}
                layout = [{"tabs": [{"windows": [pane]}]}]
                with patch("rofi_agent_plus.desktop.run_json", return_value=layout):
                    validate_kitty_layout(control, process, process)
                for invalid in (
                    [{"tabs": [{"windows": [pane, pane]}]}],
                    [{"tabs": [{"windows": [pane]}, {"windows": [pane]}]}],
                    [{"tabs": [{"windows": [{"pid": process.pid, "foreground_processes": []}]}]}],
                ):
                    with (
                        patch("rofi_agent_plus.desktop.run_json", return_value=invalid),
                        self.assertRaises(DesktopError),
                    ):
                        validate_kitty_layout(control, process, process)


if __name__ == "__main__":
    unittest.main()
