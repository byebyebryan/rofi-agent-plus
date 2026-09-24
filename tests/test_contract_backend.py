"""Hermetic Host Mesh/Tmux Session v1 consumer tests."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import engine
from rofi_agent_plus.cache import CACHE_VERSION, CacheStore, _merge_host_snapshot, build_snapshot
from rofi_agent_plus.codex import AppServerClient
from rofi_agent_plus.config import PickerConfig
from rofi_agent_plus.contract_backend import (
    _ACTIVE_PROBE,
    CommandOutput,
    ContractBackend,
    ContractError,
    StaleMeshError,
    _composite_probe_input,
    _composite_results,
    _error_envelope_code,
    _inventory,
    _inventory_args,
    _raise_command_failure,
    _run_bounded,
    _tmux_association,
    _validate_active,
    parse_mesh,
    select_backend,
)
from rofi_agent_plus.engine import PickerError
from rofi_agent_plus.rofi import _open_selection, _parse_selection, render_snapshot, run_rofi

ROOT = Path(__file__).parent / "fixtures" / "contract"
THREAD = "11111111-1111-1111-1111-111111111111"
CONTRACT_CAPABILITY = "host-mesh-v1+tmux-session-v1"


def fixture(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text())


def output(argv: list[str], stdout: object, *, code: int = 0, stderr: str = "") -> CommandOutput:
    encoded = json.dumps(stdout, separators=(",", ":")) + "\n"
    return CommandOutput(tuple(argv), code, encoded, stderr, stdout_bytes=encoded.encode())


class MeshParseTest(unittest.TestCase):
    def test_alias_tokens_policy_bounds_and_emitted_route_order(self) -> None:
        payload = fixture("mesh-v1.json")
        remote = payload["hosts"][1]
        assert isinstance(remote, dict)
        remote["aliases"] = ["user@beta", "beta_native"]
        mesh = parse_mesh(payload)
        self.assertEqual(
            ("beta-first.test", "beta-fallback.test"),
            tuple(route.destination for route in mesh.hosts[1].routes),
        )

        for key, value in (("connectTimeoutSeconds", 61), ("connectionAttempts", 11)):
            invalid = copy.deepcopy(payload)
            invalid["sshPolicy"][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                parse_mesh(invalid)

    def test_same_host_alias_overlap_is_allowed_cross_host_is_not(self) -> None:
        payload = fixture("mesh-v1.json")
        payload["hosts"][0]["aliases"] = ["alpha"]
        self.assertEqual("alpha", parse_mesh(payload).local.host_id)
        invalid = copy.deepcopy(payload)
        invalid["hosts"][1]["aliases"] = ["alpha"]
        with self.assertRaisesRegex(ContractError, "ambiguous"):
            parse_mesh(invalid)

    def test_generated_revision_health_ttl_and_route_timestamps_are_strict(self) -> None:
        payload = fixture("mesh-v1.json")
        self.assertEqual(1722742999000, parse_mesh(payload).hosts[1].routes[0].last_reachable_at)
        mutations = (
            ("generatedAt", True),
            ("generatedAt", 2**63),
            ("meshRevision", "has space"),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                malformed = copy.deepcopy(payload)
                malformed[key] = value
                with self.assertRaises(ContractError):
                    parse_mesh(malformed)
        for value in (0, 86401):
            malformed = copy.deepcopy(payload)
            malformed["sshPolicy"]["routeHealthTtlSeconds"] = value
            with self.assertRaises(ContractError):
                parse_mesh(malformed)
        malformed = copy.deepcopy(payload)
        malformed["sshPolicy"]["executable"] = "ssh client"
        with self.assertRaises(ContractError):
            parse_mesh(malformed)
        for key, value in (("lastReachableAt", -1), ("lastUnreachableAt", True)):
            malformed = copy.deepcopy(payload)
            malformed["hosts"][1]["routes"][0][key] = value
            with self.assertRaises(ContractError):
                parse_mesh(malformed)

    def test_prepare_rejects_malformed_nonzero_and_unsupported_provider_output(self) -> None:
        for payload, code in (("not-json", 0), ({"schemaVersion": 2}, 0), ({}, 2)):
            with self.subTest(payload=payload, code=code):

                def runner(
                    argv: list[str],
                    _payload: object = payload,
                    _code: int = code,
                    **_kwargs: object,
                ) -> CommandOutput:
                    return (
                        CommandOutput(tuple(argv), _code, _payload, "failure")
                        if isinstance(_payload, str)
                        else output(argv, _payload, code=_code, stderr="failure")
                    )

                backend = ContractBackend(
                    "rofi-ssh-plus",
                    "rofi-tmux-plus",
                    runner=runner,
                )
                with self.assertRaises(ContractError):
                    backend.prepare()


class InventoryContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mesh = parse_mesh(fixture("mesh-v1.json"))
        self.payload = fixture("tmux-inventory-v1.json")

    def test_argv_pins_mesh_hosts_and_only_requested_options(self) -> None:
        argv = _inventory_args("rofi-tmux-plus", self.mesh)
        self.assertEqual(
            [
                "rofi-tmux-plus",
                "inventory",
                "--json",
                "--panes",
                "--mesh-revision",
                "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
                "--host",
                "alpha",
                "--host",
                "beta",
                "--session-option",
                "@codex_thread_id",
                "--session-option",
                "@codex_name",
                "--session-option",
                "@claude_session_id",
                "--session-option",
                "@claude_name",
                "--session-option",
                "@opencode_session_id",
                "--session-option",
                "@opencode_name",
                "--session-option",
                "@agent_picker_waiting",
            ],
            argv,
        )

    def test_eagerly_rejects_unreferenced_malformed_session_and_missing_option(self) -> None:
        malformed = copy.deepcopy(self.payload)
        beta = malformed["hosts"][1]
        beta.update(
            {
                "serverGeneration": "tmux-v1:beta",
                "sessions": [copy.deepcopy(malformed["hosts"][0]["sessions"][0])],
            }
        )
        beta["sessions"][0].update(
            {
                "hostId": "beta",
                "serverGeneration": "tmux-v1:beta",
                "sessionId": "$5",
                "createdAt": 9,
            }
        )
        beta["sessions"][0]["options"].pop("@opencode_name")
        with self.assertRaisesRegex(ContractError, "requested option"):
            _inventory(malformed, self.mesh)

        malformed = copy.deepcopy(self.payload)
        malformed["hosts"][1]["nativeHostname"] = "bad\nname"
        with self.assertRaises(ContractError):
            _inventory(malformed, self.mesh)

    def test_remote_domain_error_may_have_no_reached_route(self) -> None:
        payload = copy.deepcopy(self.payload)
        beta = payload["hosts"][1]
        beta.update(
            {
                "status": "error",
                "route": None,
                "error": {"code": "operation_failed", "message": "remote command failed"},
            }
        )
        parsed = _inventory(payload, self.mesh)
        self.assertEqual("error", parsed["beta"]["status"])

    def test_duplicate_option_and_process_conflict_are_ambiguous(self) -> None:
        host = self.payload["hosts"][0]
        duplicated = copy.deepcopy(host)
        second = copy.deepcopy(duplicated["sessions"][0])
        second.update({"sessionId": "$9", "createdAt": 99, "name": "other"})
        duplicated["sessions"].append(second)
        association, error = _tmux_association(
            duplicated,
            "codex",
            THREAD,
            {},
            "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        )
        self.assertIsNone(association)
        self.assertEqual("ambiguous provider correlation", error)

        conflict = copy.deepcopy(host)
        second = copy.deepcopy(conflict["sessions"][0])
        second.update({"sessionId": "$9", "createdAt": 99, "name": "other"})
        second["options"]["@codex_thread_id"] = None
        second["panes"][0]["pid"] = 88
        conflict["sessions"].append(second)
        association, error = _tmux_association(
            conflict,
            "codex",
            THREAD,
            {"pid": 88, "ancestors": [88]},
            "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        )
        self.assertIsNone(association)
        self.assertEqual("provider option conflicts with process correlation", error)

    def test_nullable_producer_descriptor_keeps_stable_reference(self) -> None:
        parsed = _inventory(fixture("tmux-inventory-nullable-v1.json"), self.mesh)
        association, error = _tmux_association(
            parsed["alpha"],
            "codex",
            THREAD,
            {},
            "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        )
        self.assertIsNone(error)
        assert association is not None
        self.assertEqual("$4", association["sessionId"])
        self.assertIsNone(association["observedName"])

    def test_option_claims_mark_associations_but_process_only_claims_do_not(self) -> None:
        host = copy.deepcopy(self.payload["hosts"][0])
        association, error = _tmux_association(
            host,
            "codex",
            THREAD,
            {"pid": 12345, "ancestors": [12345]},
            "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        )
        self.assertIsNone(error)
        assert association is not None
        self.assertIs(association["providerOptionVerified"], True)

        process_only = copy.deepcopy(host)
        process_only["sessions"][0]["options"]["@codex_thread_id"] = None
        association, error = _tmux_association(
            process_only,
            "codex",
            THREAD,
            {"pid": 12345, "ancestors": [12345]},
            "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        )
        self.assertIsNone(error)
        assert association is not None
        self.assertNotIn("providerOptionVerified", association)


class BackendSelectionAndTransportTest(unittest.TestCase):
    def test_host_list_exit_and_body_envelopes_must_match(self) -> None:
        mesh_payload = fixture("mesh-v1.json")
        error_payload = {
            "schemaVersion": 1,
            "ok": False,
            "error": {"code": "operation_failed", "message": "failed"},
        }
        for returncode, payload in ((2, mesh_payload), (0, error_payload)):
            with self.subTest(returncode=returncode):
                backend = ContractBackend(
                    "rofi-ssh-plus",
                    "rofi-tmux-plus",
                    runner=lambda argv, payload=payload, returncode=returncode, **_kwargs: output(
                        argv, payload, code=returncode
                    ),
                )
                with self.assertRaises(ContractError):
                    backend.prepare()

    def test_error_envelope_uses_the_inventory_byte_cap(self) -> None:
        payload = {
            "schemaVersion": 1,
            "ok": False,
            "error": {"code": "operation_failed", "message": "failed"},
        }
        payload["extensions"] = {f"field{index}": "x" * (16 * 1024) for index in range(40)}
        encoded = json.dumps(payload, separators=(",", ":")) + "\n"
        command = CommandOutput(
            (),
            2,
            encoded,
            "",
            stdout_bytes=encoded.encode(),
        )
        self.assertIsNone(_error_envelope_code(command))
        self.assertEqual(
            "operation_failed",
            _error_envelope_code(command, limit=1 << 20),
        )

    def test_typed_tmux_errors_validate_host_id_before_stale_recovery(self) -> None:
        payload = {
            "schemaVersion": 1,
            "ok": False,
            "error": {
                "code": "stale_mesh",
                "message": "stale",
                "hostId": "not a host id",
            },
        }
        command = output([], payload, code=2)
        # Host Mesh has no known hostId field, so its extension remains
        # forward-compatible and does not suppress the stable error code.
        self.assertEqual("stale_mesh", _error_envelope_code(command))
        self.assertIsNone(_error_envelope_code(command, limit=1 << 20, validate_host_id=True))
        with self.assertRaises(ContractError) as caught:
            _raise_command_failure(
                command,
                "Tmux Session inventory",
                limit=1 << 20,
                validate_host_id=True,
            )
        self.assertNotIsInstance(caught.exception, StaleMeshError)

    def test_tmux_is_required_but_missing_ssh_selects_local_contract(self) -> None:
        with self.assertRaisesRegex(ContractError, "rofi-tmux-plus is required"):
            select_backend(which=lambda _name: None)
        backend = select_backend(
            which=lambda name: "/tools/tmux" if name == "rofi-tmux-plus" else None
        )
        self.assertEqual("contract", backend.kind)
        self.assertIsNone(backend.ssh_command)
        with mock.patch(
            "rofi_agent_plus.contract_backend.socket.gethostname", return_value="LOCAL.Example"
        ):
            with mock.patch(
                "rofi_agent_plus.contract_backend.socket.getfqdn", return_value="LOCAL.Example"
            ):
                backend.prepare()
        self.assertEqual(
            {
                "kind": "contract",
                "capability": CONTRACT_CAPABILITY,
                "meshRevision": None,
            },
            backend.identity,
        )
        assert backend.mesh is not None
        self.assertEqual("local", backend.mesh.local.host_id)
        self.assertEqual("LOCAL", backend.mesh.local.display)
        self.assertEqual(("LOCAL.Example", "LOCAL"), backend.mesh.local.aliases)

    def test_local_only_identity_matches_tmux_plus_odd_hostname_rules(self) -> None:
        backend = select_backend(
            which=lambda name: "/tools/tmux" if name == "rofi-tmux-plus" else None
        )
        with mock.patch(
            "rofi_agent_plus.contract_backend.socket.gethostname", return_value="-bad name"
        ):
            with mock.patch(
                "rofi_agent_plus.contract_backend.socket.getfqdn", return_value="-bad name"
            ):
                backend.prepare()
        assert backend.mesh is not None
        self.assertEqual("localhost", backend.mesh.local.host_id)
        self.assertEqual("-bad name", backend.mesh.local.display)
        self.assertEqual(("localhost",), backend.mesh.local.aliases)

    def test_present_malformed_ssh_contract_never_becomes_local_only(self) -> None:
        backend = select_backend(
            which=lambda name: f"/tools/{name}",
            runner=lambda argv, **_kwargs: CommandOutput(tuple(argv), 0, "not-json", ""),
        )
        with self.assertRaisesRegex(ContractError, "invalid JSON"):
            backend.prepare()
        self.assertIsNone(backend.mesh)

    def test_ssh_absent_discovers_local_rows_through_tmux_contract(self) -> None:
        inventory_calls: list[list[str]] = []

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            inventory_calls.append(argv)
            return output(
                argv,
                {
                    "schemaVersion": 1,
                    "generatedAt": 1,
                    "meshRevision": None,
                    "hosts": [
                        {
                            "hostId": "local",
                            "display": "LOCAL",
                            "local": True,
                            "status": "ok",
                            "observedAt": 1,
                            "nativeHostname": "LOCAL",
                            "serverGeneration": None,
                            "route": None,
                            "sessions": [],
                        }
                    ],
                },
            )

        backend = select_backend(
            which=lambda name: "/tools/tmux" if name == "rofi-tmux-plus" else None,
            runner=runner,
        )
        with (
            mock.patch(
                "rofi_agent_plus.contract_backend.socket.gethostname", return_value="LOCAL.Example"
            ),
            mock.patch(
                "rofi_agent_plus.contract_backend.socket.getfqdn", return_value="LOCAL.Example"
            ),
        ):
            backend.prepare()
        active = {
            "nativeHostname": "LOCAL",
            "active": {},
            "claudeActive": {},
            "opencodeActive": {},
        }
        backend._active = lambda _host, _deadline: (None, active)  # type: ignore[method-assign]
        backend._provider_results = lambda *_args: (  # type: ignore[method-assign]
            [{"id": THREAD, "name": "local session", "cwd": "/work"}],
            {"installed": False, "sessions": []},
            {"installed": False, "sessions": []},
        )

        events = backend._once(PickerConfig())

        self.assertEqual(1, len(inventory_calls))
        self.assertIn("--host", inventory_calls[0])
        self.assertIn("local", inventory_calls[0])
        self.assertNotIn("--mesh-revision", inventory_calls[0])
        row = events[1]["sessions"][0]
        self.assertTrue(row["contractMode"])
        self.assertEqual("local", row["hostId"])
        self.assertIsNone(row["backend"]["meshRevision"])

    def test_reached_marker_uses_emitted_route_order_and_reports_only_evidence(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        calls: list[list[str]] = []

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            calls.append(argv)
            if argv[1:3] == ["mesh", "report-route"]:
                return output(argv, {"schemaVersion": 1, "ok": True})
            if argv[0] == "ssh" and argv[-2] == "beta-first.test":
                return CommandOutput(tuple(argv), 255, "", "Permission denied")
            remote = argv[-1]
            nonce = remote.split("rofi-plus-reached ")[1].split(" ", 1)[0].strip("'")
            marker = f"\x1eROFI_PLUS_REACHED_V1:{nonce}\x1f\n"
            payload = {
                "nativeHostname": "beta-native",
                "active": {},
                "claudeActive": {},
                "opencodeActive": {},
            }
            return output(argv, payload, stderr=marker)

        backend = ContractBackend(
            "rofi-ssh-plus", "rofi-tmux-plus", runner=runner, now_millis=lambda: 7
        )
        backend.mesh = mesh
        route, active = backend._remote_active(mesh.hosts[1], time.monotonic() + 5)
        self.assertEqual("beta-fallback.test", route)
        self.assertIsInstance(active, dict)
        ssh_routes = [call[-2] for call in calls if call[0] == "ssh"]
        self.assertEqual(["beta-first.test", "beta-fallback.test"], ssh_routes)
        reports = [call for call in calls if call[1:3] == ["mesh", "report-route"]]
        self.assertEqual(1, len(reports))
        self.assertIn("beta-fallback.test", reports[0])
        self.assertIn("reachable", reports[0])

    def test_marked_domain_attempt_retries_transport_and_keeps_report_failure_diagnostic(
        self,
    ) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        calls: list[list[str]] = []

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            calls.append(argv)
            if argv[1:3] == ["mesh", "report-route"]:
                return CommandOutput(tuple(argv), 2, "{}", "report unavailable")
            if argv[-2] == "beta-first.test":
                return CommandOutput(tuple(argv), 255, "", "operation timed out")
            nonce = argv[-1].split("rofi-plus-reached ")[1].split(" ", 1)[0].strip("'")
            return output(
                argv,
                {"installed": False, "sessions": []},
                stderr=f"\x1eROFI_PLUS_REACHED_V1:{nonce}\x1f\n",
            )

        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=runner)
        backend.mesh = mesh
        route, completed = backend._remote_command(
            mesh.hosts[1],
            time.monotonic() + 4,
            remote_argv=("python3", "-", "40", ""),
            input_data=b"probe",
            label="provider query",
        )
        self.assertEqual("beta-fallback.test", route)
        self.assertIsInstance(completed, CommandOutput)
        self.assertEqual(
            ["beta-first.test", "beta-fallback.test"], [row[-2] for row in calls if row[0] == "ssh"]
        )
        self.assertTrue(backend._report_errors)
        remote = next(
            row[-1] for row in calls if row[0] == "ssh" and row[-2] == "beta-fallback.test"
        )
        self.assertIn("python3", remote)
        self.assertNotIn("ssh true", remote)

    def test_local_activity_uses_bounded_probe_instead_of_engine_ps(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = mesh
        backend._run = mock.Mock(side_effect=ContractError("contract command timed out"))  # type: ignore[method-assign]
        with mock.patch("rofi_agent_plus.contract_backend.engine._process_table") as table:
            route, active = backend._active(mesh.local, time.monotonic() + 1)
        self.assertIsNone(route)
        self.assertIsInstance(active, ContractError)
        table.assert_not_called()

    def test_remote_provider_results_join_one_composite_with_codex(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = mesh
        active = {
            "nativeHostname": "beta-native",
            "active": {},
            "claudeActive": {},
            "opencodeActive": {},
        }
        backend._remote_composite = mock.Mock(  # type: ignore[method-assign]
            return_value=(
                "beta-first.test",
                active,
                {"installed": False, "sessions": []},
                {"installed": False, "sessions": []},
            )
        )
        backend._remote_codex_threads = mock.Mock(return_value=[])  # type: ignore[method-assign]
        route, codex, claude, opencode, observed_active = backend._remote_provider_results(
            mesh.hosts[1], PickerConfig(), time.monotonic() + 2
        )
        self.assertEqual("beta-first.test", route)
        self.assertEqual([], codex)
        self.assertFalse(claude["installed"])
        self.assertFalse(opencode["installed"])
        self.assertEqual(active, observed_active)
        backend._remote_composite.assert_called_once()
        backend._remote_codex_threads.assert_called_once()

    def test_composite_probe_reaps_timed_out_child_and_preserves_siblings(self) -> None:
        slow_active = "import time; time.sleep(10)"
        healthy = 'print("{\\"installed\\":false,\\"sessions\\":[]}")'
        with (
            mock.patch("rofi_agent_plus.contract_backend._ACTIVE_PROBE", slow_active),
            mock.patch("rofi_agent_plus.contract_backend.CLAUDE_SESSION_PROBE", healthy),
            mock.patch("rofi_agent_plus.contract_backend.OPENCODE_SESSION_PROBE", healthy),
        ):
            started = time.monotonic()
            command = _run_bounded(
                [sys.executable, "-", "1", "1"],
                input_data=_composite_probe_input(),
                timeout=4,
                stdout_limit=512 * 1024,
            )
        self.assertLess(time.monotonic() - started, 3.0)
        active, claude, opencode = _composite_results(command)
        self.assertIsInstance(active, ContractError)
        self.assertIn("timed_out", str(active))
        self.assertIsInstance(claude, dict)
        self.assertIsInstance(opencode, dict)

    def test_composite_probe_isolates_invalid_child_json(self) -> None:
        with mock.patch("rofi_agent_plus.contract_backend._ACTIVE_PROBE", "print('not JSON')"):
            command = _run_bounded(
                [sys.executable, "-", "1", "1"],
                input_data=_composite_probe_input(),
                timeout=2,
                stdout_limit=512 * 1024,
            )
        active, claude, opencode = _composite_results(command)
        self.assertIsInstance(active, ContractError)
        self.assertIn("invalid_json", str(active))
        self.assertIsInstance(claude, dict)
        self.assertIsInstance(opencode, dict)

    def test_composite_stage_failures_are_isolated_and_bounded(self) -> None:
        valid_active = {
            "nativeHostname": "beta-native",
            "active": {},
            "claudeActive": {},
            "opencodeActive": {},
        }
        payload = {
            "schemaVersion": 1,
            "stages": {
                "active": {"ok": True, "payload": valid_active},
                "claude": {
                    "ok": False,
                    "error": {"code": "invalid_json", "message": "invalid JSON"},
                },
                "opencode": {
                    "ok": False,
                    "error": {"code": "output_limit", "message": "stdout limit"},
                },
            },
        }
        active, claude, opencode = _composite_results(output([], payload))
        self.assertEqual(valid_active, active)
        self.assertIsInstance(claude, ContractError)
        self.assertIn("invalid_json", str(claude))
        self.assertIsInstance(opencode, ContractError)
        self.assertIn("output_limit", str(opencode))

    def test_bounded_process_rejects_output_and_reaps_timeout(self) -> None:
        noisy = [sys.executable, "-c", "import sys;sys.stdout.write('x'*100000)"]
        with self.assertRaisesRegex(ContractError, "stdout"):
            _run_bounded(noisy, timeout=2, stdout_limit=128)
        noisy_stderr = [sys.executable, "-c", "import sys;sys.stderr.write('x'*100000)"]
        with self.assertRaisesRegex(ContractError, "stderr"):
            _run_bounded(noisy_stderr, timeout=2, stderr_limit=128)
        sleepy = [sys.executable, "-c", "import time;time.sleep(5)"]
        started = time.monotonic()
        with self.assertRaisesRegex(ContractError, "timed out"):
            _run_bounded(sleepy, timeout=0.05)
        self.assertLess(time.monotonic() - started, 1.0)

        blocked_stdin = [sys.executable, "-c", "import time;time.sleep(5)"]
        started = time.monotonic()
        with self.assertRaisesRegex(ContractError, "timed out"):
            _run_bounded(blocked_stdin, input_data=b"x" * (2 << 20), timeout=0.05)
        self.assertLess(time.monotonic() - started, 1.0)
        invalid_utf8 = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'\\xff')"]
        with self.assertRaisesRegex(ContractError, "UTF-8"):
            _run_bounded(invalid_utf8, timeout=2)
        closed_stdin = [
            sys.executable,
            "-c",
            "import os,sys,time;os.close(0);sys.stderr.write('closed');sys.stderr.flush();time.sleep(.03)",
        ]
        completed = _run_bounded(closed_stdin, input_data=b"x" * (2 << 20), timeout=2)
        self.assertEqual(0, completed.returncode)
        self.assertIn("closed", completed.stderr)

    def test_codex_waits_for_marker_while_buffering_early_stdout(self) -> None:
        marker = b"\x1eROFI_PLUS_REACHED_V1:0123456789abcdef\x1f\n"

        def program(stderr: bytes) -> list[str]:
            source = (
                "import sys,time;"
                "sys.stdout.buffer.write(b'early-json-rpc\\n');sys.stdout.flush();"
                "time.sleep(.03);"
                f"sys.stderr.buffer.write({stderr!r});sys.stderr.flush();"
                "time.sleep(.05)"
            )
            return [sys.executable, "-c", source]

        with AppServerClient(
            program(marker),
            1,
            "test",
            ContractError,
            stdout_limit=1024,
            stderr_limit=1024,
            reached_marker=marker,
        ) as client:
            self.assertEqual((True, ""), client.wait_for_marker(0.5))
            self.assertIn(b"early-json-rpc", client._buffer)
        for candidate in (b"", marker.replace(b"0123456789abcdef", b"wrong"), marker + marker):
            with self.subTest(candidate=candidate):
                with AppServerClient(
                    program(candidate),
                    1,
                    "test",
                    ContractError,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    reached_marker=marker,
                ) as client:
                    self.assertFalse(client.wait_for_marker(0.1)[0])

    def test_codex_stdout_limit_counts_discarded_jsonl_records(self) -> None:
        source = (
            "import sys,time;"
            "sys.stdin.readline();"
            "line='{\"id\":0,\"noise\":\"' + 'x'*48 + '\"}\\n';"
            "sys.stdout.write(line);sys.stdout.flush();time.sleep(.04);"
            "sys.stdout.write(line);sys.stdout.flush();time.sleep(.04);"
            'sys.stdout.write(\'{"id":1,"result":{}}\\n\');sys.stdout.flush()'
        )
        with AppServerClient(
            [sys.executable, "-c", source],
            1,
            "test",
            ContractError,
            stdout_limit=100,
            stderr_limit=1024,
        ) as client:
            with self.assertRaisesRegex(ContractError, "stdout limit"):
                client.call("test", {})

    def test_codex_app_server_uses_stdio_and_wrong_marker_falls_through_to_next_route(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        local_commands: list[list[str]] = []
        remote_commands: list[list[str]] = []
        marker_timeouts: list[float] = []

        class LocalClient:
            def __init__(self, command: list[str], *_args: object, **_kwargs: object) -> None:
                local_commands.append(command)
                self.timeout = 0.0

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def initialize(self) -> None:
                return None

            def call(self, method: str, _params: object) -> object:
                return {"data": []} if method == "thread/list" else {}

        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: output(argv, {"installed": False, "sessions": []}),
        )
        backend.mesh = mesh
        with mock.patch("rofi_agent_plus.contract_backend.AppServerClient", LocalClient):
            backend._provider_results(mesh.local, PickerConfig(), time.monotonic() + 2)
        self.assertEqual([["codex", "app-server", "--stdio"]], local_commands)

        class RemoteClient:
            def __init__(self, command: list[str], *_args: object, **_kwargs: object) -> None:
                remote_commands.append(command)
                self.timeout = 0.0
                self._first = command[-2] == "beta-first.test"

            def wait_for_marker(self, timeout: float) -> tuple[bool, str]:
                marker_timeouts.append(timeout)
                return (False, "wrong reached marker") if self._first else (True, "")

            def marker_result(self) -> tuple[bool, str]:
                return self.wait_for_marker(0)

            def initialize(self) -> None:
                return None

            def call(self, method: str, _params: object) -> object:
                return {"data": []} if method == "thread/list" else {}

            def close(self) -> None:
                return None

        backend._report_hint = mock.Mock()  # type: ignore[method-assign]
        with mock.patch("rofi_agent_plus.contract_backend.AppServerClient", RemoteClient):
            self.assertEqual(
                [],
                backend._remote_codex_threads(mesh.hosts[1], PickerConfig(), time.monotonic() + 10),
            )
        self.assertEqual(
            ["beta-first.test", "beta-fallback.test"],
            [command[-2] for command in remote_commands],
        )
        self.assertTrue(
            all("codex app-server --stdio" in command[-1] for command in remote_commands)
        )
        self.assertEqual(2, len(marker_timeouts))
        self.assertTrue(
            all(
                mesh.connect_timeout + 3.5 <= timeout <= mesh.connect_timeout + 4
                for timeout in marker_timeouts
            )
        )
        backend._report_hint.assert_called_once_with(
            mesh.hosts[1], mesh.hosts[1].routes[1], "reachable", mock.ANY
        )

    def test_report_envelopes_are_typed_and_nonstale_hints_do_not_abort(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = mesh
        host, route = mesh.hosts[1], mesh.hosts[1].routes[0]
        backend._run = lambda argv, **_kwargs: output(  # type: ignore[method-assign]
            argv, {"schemaVersion": 1, "ok": True, "accepted": False}
        )
        backend._report(host, route, "reachable", time.monotonic() + 2)
        backend._run = lambda argv, **_kwargs: output(  # type: ignore[method-assign]
            argv,
            {
                "schemaVersion": 1,
                "ok": False,
                "error": {"code": "stale_mesh", "message": "stale"},
            },
            code=2,
        )
        with self.assertRaises(StaleMeshError):
            backend._report(host, route, "reachable", time.monotonic() + 2)
        backend._run = lambda argv, **_kwargs: CommandOutput(  # type: ignore[method-assign]
            tuple(argv), 2, "not-json", "ordinary stale_mesh wording"
        )
        with self.assertRaises(ContractError) as caught:
            backend._report(host, route, "reachable", time.monotonic() + 2)
        self.assertNotIsInstance(caught.exception, StaleMeshError)

    def test_route_hint_dedupes_only_success_and_preserves_stale(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = mesh
        host, route = mesh.hosts[1], mesh.hosts[1].routes[0]
        started = threading.Event()
        release = threading.Event()
        calls: list[object] = []

        def successful(*_args: object) -> None:
            calls.append(object())
            started.set()
            self.assertTrue(release.wait(1))

        backend._report = successful  # type: ignore[method-assign]
        errors: list[Exception] = []

        def send_hint() -> None:
            try:
                backend._report_hint(host, route, "reachable", time.monotonic() + 2)
            except Exception as error:  # noqa: BLE001 - test records all result paths
                errors.append(error)

        first = threading.Thread(target=send_hint)
        second = threading.Thread(target=send_hint)
        first.start()
        self.assertTrue(started.wait(1))
        second.start()
        release.set()
        first.join(1)
        second.join(1)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(calls))

        backend._reset_report_transaction()
        backend._report = mock.Mock(side_effect=[ContractError("offline"), None])  # type: ignore[method-assign]
        backend._report_hint(host, route, "reachable", time.monotonic() + 2)
        backend._report_hint(host, route, "reachable", time.monotonic() + 2)
        self.assertEqual(2, backend._report.call_count)  # type: ignore[attr-defined]
        self.assertTrue(backend._report_errors)

        backend._reset_report_transaction()
        backend._report = mock.Mock(side_effect=StaleMeshError("changed"))  # type: ignore[method-assign]
        with self.assertRaises(StaleMeshError):
            backend._report_hint(host, route, "reachable", time.monotonic() + 2)
        backend._report.assert_called_once()  # type: ignore[attr-defined]

    def test_remote_probe_keeps_root_codex_and_claude_fd_fallback_without_tmux(self) -> None:
        root_id = THREAD
        child_id = "22222222-2222-2222-2222-222222222222"
        claude_id = "33333333-3333-3333-3333-333333333333"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            proc = base / "proc"
            scripts = base / "bin"
            scripts.mkdir()
            ps = scripts / "ps"
            ps.write_text(
                "#!/bin/sh\nprintf '%s\\n' '10 1 codex' '11 1 claude' '12 1 codex' '13 1 codex'\n"
            )
            ps.chmod(0o700)
            commands = {
                10: b"codex\0",
                11: b"claude\0",
                12: b"codex\0app-server\0--listen\0" + b"127.0.0.1\0",
                13: b"codex\0",
            }
            for pid, command in commands.items():
                fd = proc / str(pid) / "fd"
                fd.mkdir(parents=True)
                (proc / str(pid) / "cmdline").write_bytes(command)
            root_rollout = base / f"rollout-{root_id}.jsonl"
            root_rollout.write_text('{"type":"session_meta","payload":{"source":{}}}\n')
            child_rollout = base / f"rollout-{child_id}.jsonl"
            child_rollout.write_text(
                '{"type":"session_meta","payload":{"source":{"subagent":true}}}\n'
            )
            projects = base / "projects"
            projects.mkdir()
            transcript = projects / f"{claude_id}.jsonl"
            transcript.write_text("{}\n")
            (proc / "10" / "fd" / "3").symlink_to(root_rollout)
            (proc / "10" / "fd" / "4").symlink_to(child_rollout)
            (proc / "13" / "fd" / "3").symlink_to(root_rollout)
            (proc / "11" / "fd" / "3").symlink_to(transcript)
            environment = {
                **os.environ,
                "PATH": str(scripts),
                "ROFI_AGENT_PLUS_PROC_ROOT": str(proc),
            }
            result = subprocess.run(
                [sys.executable, "-c", _ACTIVE_PROBE],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
        active = json.loads(result.stdout)
        self.assertEqual({root_id}, set(active["active"]))
        root_candidates = active["active"][root_id]["candidates"]
        self.assertEqual(2, len(root_candidates))
        self.assertEqual({10, 13}, {candidate["pid"] for candidate in root_candidates})
        self.assertEqual({claude_id}, set(active["claudeActive"]))
        validated = _validate_active(active)
        self.assertEqual(active["nativeHostname"], validated["nativeHostname"])
        self.assertEqual(active["active"], validated["active"])
        self.assertEqual(active["claudeActive"], validated["claudeActive"])
        self.assertEqual(active["opencodeActive"], validated["opencodeActive"])
        self.assertNotIn("tmux", _ACTIVE_PROBE)


class ContractBackendAssemblyTest(unittest.TestCase):
    def test_remote_refresh_uses_two_domains_and_one_reachable_hint(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = copy.deepcopy(fixture("tmux-inventory-v1.json"))
        inventory["hosts"] = [inventory["hosts"][1]]
        composite_commands: list[list[str]] = []
        codex_commands: list[list[str]] = []
        reports: list[list[str]] = []
        active = {
            "nativeHostname": "beta-native",
            "active": {},
            "claudeActive": {},
            "opencodeActive": {},
        }
        composite = {
            "schemaVersion": 1,
            "stages": {
                "active": {"ok": True, "payload": active},
                "claude": {"ok": True, "payload": {"installed": False, "sessions": []}},
                "opencode": {"ok": True, "payload": {"installed": False, "sessions": []}},
            },
        }

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            if argv[0] == "rofi-tmux-plus":
                return output(argv, inventory)
            if argv[1:3] == ["mesh", "report-route"]:
                reports.append(argv)
                return output(argv, {"schemaVersion": 1, "ok": True, "accepted": True})
            if argv[0] == "ssh":
                composite_commands.append(argv)
                nonce = argv[-1].split("rofi-plus-reached ")[1].split(" ", 1)[0].strip("'")
                return output(argv, composite, stderr=f"\x1eROFI_PLUS_REACHED_V1:{nonce}\x1f\n")
            raise AssertionError(argv)

        class RemoteClient:
            def __init__(self, command: list[str], *_args: object, **_kwargs: object) -> None:
                codex_commands.append(command)
                self.timeout = 0.0

            def wait_for_marker(self, _timeout: float) -> tuple[bool, str]:
                return True, ""

            def marker_result(self) -> tuple[bool, str]:
                return True, ""

            def initialize(self) -> None:
                return None

            def call(self, method: str, _params: object) -> object:
                return (
                    {"data": [{"id": THREAD, "name": "remote", "cwd": "/work"}]}
                    if method == "thread/list"
                    else {}
                )

            def close(self) -> None:
                return None

        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=runner)
        backend.mesh = mesh
        with mock.patch("rofi_agent_plus.contract_backend.AppServerClient", RemoteClient):
            events = backend._once(PickerConfig(), host_ids=("beta",))

        self.assertEqual(1, len(composite_commands))
        self.assertEqual(1, len(codex_commands))
        self.assertIn("python3 -", composite_commands[0][-1])
        self.assertIn("codex app-server --stdio", codex_commands[0][-1])
        self.assertEqual(1, len(reports))
        self.assertIn("reachable", reports[0])
        self.assertEqual(THREAD, events[1]["sessions"][0]["id"])

    def test_remote_domains_keep_a_successful_sibling_and_active_only_rows(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = copy.deepcopy(fixture("tmux-inventory-v1.json"))
        inventory["hosts"] = [inventory["hosts"][1]]
        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: output(argv, inventory),
        )
        backend.mesh = mesh
        active = {
            "nativeHostname": "beta-native",
            "active": {THREAD: {"candidates": [{"pid": 9, "ancestors": [9]}]}},
            "claudeActive": {},
            "opencodeActive": {},
        }
        composite_failure = ContractError("provider composite transaction failed")
        backend._remote_provider_results = mock.Mock(  # type: ignore[method-assign]
            return_value=(
                "beta-first.test",
                [{"id": THREAD, "name": "remote", "cwd": "/work"}],
                composite_failure,
                composite_failure,
                active,
            )
        )
        events = backend._once(PickerConfig(), host_ids=("beta",))
        self.assertEqual(THREAD, events[1]["sessions"][0]["id"])
        self.assertTrue(any(error["stage"] == "claude" for error in events[1]["errors"]))

        backend._remote_provider_results = mock.Mock(  # type: ignore[method-assign]
            return_value=(
                "beta-first.test",
                ContractError("Codex unavailable"),
                {"installed": False, "sessions": []},
                {"installed": False, "sessions": []},
                active,
            )
        )
        events = backend._once(PickerConfig(), host_ids=("beta",))
        row = events[1]["sessions"][0]
        self.assertEqual(THREAD, row["id"])
        self.assertTrue(row["active"])
        self.assertTrue(any(error["stage"] == "threads" for error in events[1]["errors"]))

    def test_active_only_rows_carry_private_cache_provenance(self) -> None:
        rows: list[dict[str, object]] = []
        ContractBackend._append_active_only_rows(
            rows,
            {
                "active": {THREAD: {"candidates": [{"pid": 1, "ancestors": [1]}]}},
                "claudeActive": {},
                "opencodeActive": {},
            },
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("activity-only", rows[0]["sourceObservation"])

    def test_tmux_inventory_exit_and_body_envelopes_must_match(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory_payload = fixture("tmux-inventory-v1.json")
        error_payload = {
            "schemaVersion": 1,
            "ok": False,
            "error": {"code": "operation_failed", "message": "failed"},
        }
        active = {
            "nativeHostname": "native",
            "active": {},
            "claudeActive": {},
            "opencodeActive": {},
        }
        for returncode, payload in ((2, inventory_payload), (0, error_payload)):
            with self.subTest(returncode=returncode):
                backend = ContractBackend(
                    "rofi-ssh-plus",
                    "rofi-tmux-plus",
                    runner=lambda argv, payload=payload, returncode=returncode, **_kwargs: output(
                        argv, payload, code=returncode
                    ),
                )
                backend.mesh = mesh
                backend._active = lambda _host, _deadline: (None, active)  # type: ignore[method-assign]
                backend._provider_results = lambda *_args: (  # type: ignore[method-assign]
                    [],
                    {"installed": False, "sessions": []},
                    {"installed": False, "sessions": []},
                )
                events = backend._once(PickerConfig())
                self.assertTrue(any(error.get("stage") == "tmux" for error in events[1]["errors"]))

    def test_inventory_starts_before_provider_discovery_completes(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory_started = threading.Event()

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            self.assertEqual("inventory", argv[1])
            inventory_started.set()
            return output(argv, fixture("tmux-inventory-v1.json"))

        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=runner)
        backend.mesh = mesh
        active = {
            "nativeHostname": "native",
            "active": {},
            "claudeActive": {},
            "opencodeActive": {},
        }
        backend._active = lambda _host, _deadline: (None, active)  # type: ignore[method-assign]

        def provider_results(*_args: object) -> tuple[object, object, object]:
            self.assertTrue(inventory_started.wait(0.5))
            return [], {"installed": False, "sessions": []}, {"installed": False, "sessions": []}

        backend._provider_results = provider_results  # type: ignore[method-assign]
        events = backend._once(PickerConfig())
        self.assertTrue(inventory_started.is_set())
        self.assertEqual("refresh-finished", events[-1]["event"])
        self.assertEqual(
            [
                {"hostId": "alpha", "display": "Alpha", "local": True},
                {"hostId": "beta", "display": "Beta", "local": False},
            ],
            events[0]["hostCatalog"],
        )

    def test_selected_refresh_emits_the_full_ordered_host_catalog(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = copy.deepcopy(fixture("tmux-inventory-v1.json"))
        hosts = inventory["hosts"]
        assert isinstance(hosts, list)
        inventory["hosts"] = [hosts[1]]
        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: output(argv, inventory),
        )
        backend.mesh = mesh
        active = {
            "nativeHostname": "native",
            "active": {},
            "claudeActive": {},
            "opencodeActive": {},
        }
        backend._active = lambda _host, _deadline: (None, active)  # type: ignore[method-assign]

        events = backend._once(PickerConfig(), host_ids=("beta",))

        self.assertEqual(["beta"], events[0]["hosts"])
        self.assertEqual(
            [
                {"hostId": "alpha", "display": "Alpha", "local": True},
                {"hostId": "beta", "display": "Beta", "local": False},
            ],
            events[0]["hostCatalog"],
        )

    def test_outside_tmux_active_is_preserved_and_inventory_is_subordinate(self) -> None:
        mesh_payload = fixture("mesh-v1.json")
        inventory_payload = fixture("tmux-inventory-v1.json")

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            if argv[1] == "inventory":
                return output(argv, inventory_payload)
            raise AssertionError(argv)

        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=runner)
        backend.mesh = parse_mesh(mesh_payload)
        active = {
            "nativeHostname": "alpha-native",
            "active": {
                THREAD: {"pid": 12345, "ancestors": [12345]},
                "22222222-2222-2222-2222-222222222222": {"pid": 9, "ancestors": [9]},
            },
            "claudeActive": {},
            "opencodeActive": {},
        }
        backend._active = lambda _host, _deadline: (None, active)  # type: ignore[method-assign]
        backend._provider_results = lambda *_args: (
            [{"id": THREAD, "name": "work", "cwd": "/work"}],
            {"installed": False, "sessions": []},
            {"installed": False, "sessions": []},
        )  # type: ignore[method-assign]
        events = backend._once(PickerConfig(max_sessions=40))
        rows = events[1]["sessions"]
        self.assertEqual(2, len(rows))
        correlated = next(row for row in rows if row["id"] == THREAD)
        self.assertEqual("$4", correlated["tmux"]["sessionId"])
        self.assertIs(correlated["providerOptionVerified"], True)
        self.assertNotIn("providerOptionVerified", correlated["tmux"])
        self.assertEqual("active", correlated["activityState"])
        rendered = render_snapshot({"sessions": [correlated], "errors": []})
        info = rendered.split("\x00info\x1f", 1)[1].split("\x1fmeta\x1f", 1)[0]
        selected = _parse_selection(info)
        self.assertIs(selected["providerOptionVerified"], True)
        self.assertNotIn("providerOptionVerified", selected["tmux"])
        outside = next(row for row in rows if row["id"].startswith("2222"))
        self.assertTrue(outside["active"])
        self.assertNotIn("tmux", outside)

    def test_stale_mesh_retries_once_before_any_events_escape(self) -> None:
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = parse_mesh(fixture("mesh-v1.json"))
        events = [
            {
                "event": "refresh-started",
                "hosts": ["alpha"],
                "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                "backend": backend.identity,
            }
        ]
        backend._once = mock.Mock(side_effect=[StaleMeshError("changed"), events])  # type: ignore[method-assign]
        backend.prepare = mock.Mock()  # type: ignore[method-assign]
        self.assertEqual(events, backend.stream(PickerConfig()))
        backend.prepare.assert_called_once()

    def test_only_typed_tmux_stale_envelope_retries_the_stream(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            if argv[1] != "inventory":
                raise AssertionError(argv)
            return output(
                argv,
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "error": {"code": "stale_mesh", "message": "stale"},
                },
                code=2,
            )

        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=runner)
        backend.mesh = mesh
        backend._active = lambda _host, _deadline: (
            None,
            {  # type: ignore[method-assign]
                "nativeHostname": "native",
                "active": {},
                "claudeActive": {},
                "opencodeActive": {},
            },
        )
        backend._provider_results = lambda *_args: (  # type: ignore[method-assign]
            [],
            {"installed": False, "sessions": []},
            {"installed": False, "sessions": []},
        )
        with self.assertRaises(StaleMeshError):
            backend._once(PickerConfig())

        backend._run = lambda argv, **_kwargs: CommandOutput(  # type: ignore[method-assign]
            tuple(argv), 2, "{}", "a stale_mesh-looking diagnostic"
        )
        events = backend._once(PickerConfig())
        self.assertIn("tmux", str(events[1]["errors"]))

    def test_rofi_open_fails_closed_without_a_prepared_contract(self) -> None:
        with self.assertRaisesRegex(PickerError, "prepared authority"):
            _open_selection(
                {"contractMode": True, "kind": "codex", "id": THREAD},
                PickerConfig(),
            )
        self.assertFalse(hasattr(engine, "resolve_host_target"))

    def test_rendered_contract_info_round_trips_and_cannot_fall_into_legacy_open(self) -> None:
        row = {
            "contractMode": True,
            "backend": {
                "kind": "contract",
                "capability": CONTRACT_CAPABILITY,
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
            },
            "hostId": "beta",
            "kind": "codex",
            "id": THREAD,
            "name": "session",
            "host": "Beta",
            "cwd": "/work",
            "tmux": {
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
                "serverGeneration": "tmux-v1:beta",
                "sessionId": "$4",
                "createdAt": 5,
                "observedName": None,
            },
        }
        rendered = render_snapshot({"sessions": [row], "errors": []})
        info = rendered.split("\x00info\x1f", 1)[1].split("\x1fmeta\x1f", 1)[0]
        selected = _parse_selection(info)
        self.assertTrue(selected["contractMode"])
        with self.assertRaisesRegex(PickerError, "prepared authority"):
            _open_selection(selected, PickerConfig())
        self.assertFalse(hasattr(engine, "launch_attach"))


class ContractCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = PickerConfig(max_sessions=40)

    def test_provider_retention_clears_old_option_proof_for_process_only_tmux(self) -> None:
        old = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "providerOptionVerified": True,
            "tmux": {"sessionId": "$1"},
            "tmuxSession": "old",
        }
        fresh = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "tmux": {"sessionId": "$2"},
            "tmuxSession": "new",
        }
        merged = _merge_host_snapshot(
            {"sessions": [old]},
            {
                "sessions": [fresh],
                "errors": [{"stage": "threads", "message": "provider unavailable"}],
            },
        )
        row = merged["sessions"][0]
        self.assertEqual("$2", row["tmux"]["sessionId"])
        self.assertNotIn("providerOptionVerified", row)

    def test_provider_retention_carries_fresh_option_proof(self) -> None:
        old = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "tmux": {"sessionId": "$1"},
        }
        fresh = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "providerOptionVerified": True,
            "tmux": {"sessionId": "$2"},
        }
        merged = _merge_host_snapshot(
            {"sessions": [old]},
            {
                "sessions": [fresh],
                "errors": [{"stage": "threads", "message": "provider unavailable"}],
            },
        )
        self.assertIs(merged["sessions"][0]["providerOptionVerified"], True)

    def test_authoritative_missing_tmux_also_clears_option_proof(self) -> None:
        old = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "providerOptionVerified": True,
            "tmux": {"sessionId": "$1"},
            "tmuxSession": "old",
        }
        fresh = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
        }
        merged = _merge_host_snapshot(
            {"sessions": [old]},
            {
                "sessions": [fresh],
                "errors": [{"stage": "tmux-missing", "message": "tmux unavailable"}],
            },
        )
        row = merged["sessions"][0]
        self.assertNotIn("tmux", row)
        self.assertNotIn("providerOptionVerified", row)

    def test_scoped_lifecycle_refresh_keeps_peer_ttl_stale_for_next_full_refresh(self) -> None:
        backend = {
            "kind": "contract",
            "capability": CONTRACT_CAPABILITY,
            "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        }
        stale_at = int(time.time()) - self.config.refresh_seconds - 1
        selected_at = int(time.time())
        previous = {
            "version": CACHE_VERSION,
            "fingerprint": self.config.fingerprint,
            "generatedAt": stale_at,
            "backend": backend,
            "hostCatalog": [
                {"hostId": "alpha", "display": "Alpha", "local": True},
                {"hostId": "beta", "display": "Beta", "local": False},
            ],
            "hosts": {
                "alpha": {
                    "generatedAt": stale_at,
                    "sessions": [{"hostId": "alpha", "kind": "codex", "id": THREAD}],
                    "errors": [],
                },
                "beta": {
                    "generatedAt": stale_at,
                    "sessions": [{"hostId": "beta", "kind": "codex", "id": THREAD}],
                    "errors": [],
                },
            },
            "sessions": [],
            "errors": [],
        }
        scoped = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": backend,
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "generatedAt": selected_at,
                        "sessions": [
                            {"hostId": "alpha", "kind": "codex", "id": THREAD, "recencyAt": 2}
                        ],
                        "errors": [],
                        "backend": backend,
                    },
                    {"event": "refresh-finished", "backend": backend},
                ]
            ),
            previous,
            now=selected_at,
            retain_unselected_hosts=True,
        )
        self.assertEqual(stale_at, scoped["generatedAt"])
        self.assertEqual(selected_at, scoped["hosts"]["alpha"]["generatedAt"])
        self.assertEqual(previous["hosts"]["beta"], scoped["hosts"]["beta"])
        self.assertEqual(previous["hostCatalog"], scoped["hostCatalog"])

        with tempfile.TemporaryDirectory() as temporary:
            store = CacheStore(Path(temporary) / "cache")
            store.write(scoped)
            calls: list[object] = []

            def full_discovery(
                _config: PickerConfig,
                prior: object,
            ):
                calls.append(prior)
                return iter(
                    [
                        {
                            "event": "refresh-started",
                            "hosts": ["alpha", "beta"],
                            "hostCatalog": [
                                {"hostId": "alpha", "display": "Alpha", "local": True},
                                {"hostId": "beta", "display": "Beta", "local": False},
                            ],
                            "backend": backend,
                        },
                        {
                            "event": "host-complete",
                            "host": "alpha",
                            "sessions": [],
                            "errors": [],
                            "backend": backend,
                        },
                        {
                            "event": "host-complete",
                            "host": "beta",
                            "sessions": [],
                            "errors": [],
                            "backend": backend,
                        },
                        {"event": "refresh-finished", "backend": backend},
                    ]
                )

            store.refresh(self.config, discover=full_discovery)
        self.assertEqual(1, len(calls))

        unseeded = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": backend,
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "sessions": [],
                        "errors": [],
                        "backend": backend,
                    },
                    {"event": "refresh-finished", "backend": backend},
                ]
            ),
            now=selected_at,
            retain_unselected_hosts=True,
        )
        self.assertEqual(0, unseeded["generatedAt"])

    def test_contract_prunes_removed_hosts_and_does_not_bless_partial_data(self) -> None:
        previous = {
            "version": CACHE_VERSION,
            "fingerprint": self.config.fingerprint,
            "generatedAt": 100,
            "backend": {
                "kind": "contract",
                "capability": CONTRACT_CAPABILITY,
                "meshRevision": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            },
            "hosts": {
                "alpha": {
                    "sessions": [{"hostId": "alpha", "kind": "codex", "id": THREAD}],
                    "errors": [],
                },
                "removed": {"sessions": [], "errors": []},
            },
            "sessions": [],
            "errors": [],
        }
        events = iter(
            [
                {
                    "event": "refresh-started",
                    "hosts": ["alpha"],
                    "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                    "backend": {
                        "kind": "contract",
                        "capability": CONTRACT_CAPABILITY,
                        "meshRevision": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                    },
                },
                {
                    "event": "host-complete",
                    "host": "alpha",
                    "sessions": [],
                    "errors": [],
                    "backend": {
                        "kind": "contract",
                        "capability": CONTRACT_CAPABILITY,
                        "meshRevision": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                    },
                },
                {
                    "event": "refresh-finished",
                    "backend": {
                        "kind": "contract",
                        "capability": CONTRACT_CAPABILITY,
                        "meshRevision": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                    },
                },
            ]
        )
        snapshot = build_snapshot(self.config, events, previous, now=200)
        self.assertEqual({"alpha"}, set(snapshot["hosts"]))
        self.assertEqual(
            "sha256:2222222222222222222222222222222222222222222222222222222222222222",
            snapshot["backend"]["meshRevision"],
        )
        self.assertEqual(200, snapshot["generatedAt"])
        self.assertEqual([], snapshot["sessions"])

        partial = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": {
                            "kind": "contract",
                            "capability": CONTRACT_CAPABILITY,
                            "meshRevision": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                        },
                    }
                ]
            ),
            previous,
            now=300,
        )
        self.assertEqual(100, partial["generatedAt"])
        self.assertIn("incomplete contract host coverage", json.dumps(partial["errors"]))

    def test_typed_tmx_failure_retains_only_a_marked_stale_subordinate_ref(self) -> None:
        old_session = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "active": False,
            "activityState": "idle",
            "tmuxSession": "old",
            "tmux": {"sessionId": "$1"},
        }
        previous = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "sessions": [old_session],
                        "errors": [],
                    },
                    {"event": "refresh-finished"},
                ]
            ),
            now=100,
        )
        fresh = dict(old_session, active=True, activityState="active")
        fresh.pop("tmux")
        fresh.pop("tmuxSession")
        current = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "sessions": [fresh],
                        "errors": [{"host": "alpha", "stage": "tmux", "message": "offline"}],
                    },
                    {"event": "refresh-finished"},
                ]
            ),
            previous,
            now=200,
        )
        row = current["sessions"][0]
        self.assertTrue(row["active"])
        self.assertEqual("active", row["activityState"])
        self.assertEqual("$1", row["tmux"]["sessionId"])
        self.assertTrue(row["tmuxStale"])

    def test_provider_failure_keeps_metadata_but_authoritative_tmux_clear_wins(self) -> None:
        backend = {
            "kind": "contract",
            "capability": CONTRACT_CAPABILITY,
            "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        }
        old = {
            "contractMode": True,
            "backend": backend,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "name": "valuable name",
            "cwd": "/valuable",
            "active": False,
            "activityState": "idle",
            "tmux": {"sessionId": "$1"},
            "tmuxSession": "old",
        }
        previous = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": backend,
                    },
                    {"event": "host-complete", "host": "alpha", "sessions": [old], "errors": []},
                    {"event": "refresh-finished", "backend": backend},
                ]
            ),
            now=100,
        )
        active_only = {
            "contractMode": True,
            "backend": backend,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "name": THREAD[:8],
            "active": True,
            "activityState": "active",
        }
        current = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": backend,
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "sessions": [active_only],
                        "errors": [{"host": "alpha", "stage": "threads", "message": "offline"}],
                    },
                    {"event": "refresh-finished", "backend": backend},
                ]
            ),
            previous,
            now=200,
        )
        row = current["sessions"][0]
        self.assertEqual("valuable name", row["name"])
        self.assertTrue(row["active"])
        self.assertNotIn("tmux", row)
        self.assertNotIn("tmuxSession", row)

    def test_active_failure_keeps_only_activity_when_fresh_tmux_evidence_changed(self) -> None:
        backend = {
            "kind": "contract",
            "capability": CONTRACT_CAPABILITY,
            "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        }
        old = {
            "contractMode": True,
            "backend": backend,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "name": "provider name",
            "active": True,
            "activityState": "active",
            "tmux": {"sessionId": "$1", "createdAt": 1},
            "tmuxSession": "old-tmux-name",
        }
        previous = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": backend,
                    },
                    {"event": "host-complete", "host": "alpha", "sessions": [old], "errors": []},
                    {"event": "refresh-finished", "backend": backend},
                ]
            ),
        )
        fresh = {
            "contractMode": True,
            "backend": backend,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "name": "provider name",
            "active": False,
            "activityState": "idle",
            "tmux": {"sessionId": "$2", "createdAt": 2},
            "tmuxSession": "renamed-tmux-session",
        }
        current = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": backend,
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "sessions": [fresh],
                        "errors": [{"host": "alpha", "stage": "active", "message": "ps failed"}],
                    },
                    {"event": "refresh-finished", "backend": backend},
                ]
            ),
            previous,
        )
        row = current["sessions"][0]
        self.assertTrue(row["active"])
        self.assertEqual("active", row["activityState"])
        self.assertEqual("$2", row["tmux"]["sessionId"])
        self.assertEqual("renamed-tmux-session", row["tmuxSession"])

    def test_typed_tmux_status_and_row_ambiguity_only_retain_stale_when_unavailable(self) -> None:
        backend = {
            "kind": "contract",
            "capability": CONTRACT_CAPABILITY,
            "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        }
        old = {
            "contractMode": True,
            "backend": backend,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "tmux": {"sessionId": "$1"},
            "tmuxSession": "old",
        }
        previous = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                        "backend": backend,
                    },
                    {"event": "host-complete", "host": "alpha", "sessions": [old], "errors": []},
                    {"event": "refresh-finished", "backend": backend},
                ]
            ),
        )
        for stage, expect_stale in (
            ("tmux", True),
            ("tmux-missing", False),
            ("tmux-correlation", False),
        ):
            with self.subTest(stage=stage):
                fresh = {
                    key: value for key, value in old.items() if key not in {"tmux", "tmuxSession"}
                }
                if stage == "tmux-correlation":
                    fresh["tmuxAmbiguous"] = True
                snapshot = build_snapshot(
                    self.config,
                    iter(
                        [
                            {
                                "event": "refresh-started",
                                "hosts": ["alpha"],
                                "hostCatalog": [
                                    {"hostId": "alpha", "display": "Alpha", "local": True}
                                ],
                                "backend": backend,
                            },
                            {
                                "event": "host-complete",
                                "host": "alpha",
                                "sessions": [fresh],
                                "errors": [{"host": "alpha", "stage": stage, "message": stage}],
                            },
                            {"event": "refresh-finished", "backend": backend},
                        ]
                    ),
                    previous,
                )
                row = snapshot["sessions"][0]
                self.assertEqual(expect_stale, bool(row.get("tmuxStale")))
                self.assertEqual(expect_stale, "tmux" in row)

    def test_cache_identity_and_background_marker_are_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CacheStore(Path(temporary) / "cache")
            snapshot = {
                "version": CACHE_VERSION,
                "fingerprint": self.config.fingerprint,
                "generatedAt": int(time.time()),
                "backend": {
                    "kind": "contract",
                    "capability": CONTRACT_CAPABILITY,
                    "meshRevision": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
                "hosts": {},
                "sessions": [],
                "errors": [],
            }
            store.write(snapshot)
            self.assertIsNone(
                store.load(
                    self.config.fingerprint,
                    {"kind": "legacy", "capability": "legacy-v1", "meshRevision": None},
                )
            )
            self.assertIsNone(
                store.load(
                    self.config.fingerprint,
                    {
                        "kind": "contract",
                        "capability": CONTRACT_CAPABILITY,
                        "meshRevision": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    },
                )
            )
            with mock.patch("rofi_agent_plus.cache.subprocess.Popen") as popen:
                popen.return_value.pid = 7
                self.assertTrue(store.spawn_background(["fake"], scope={"fingerprint": "a"}))
                self.assertFalse(store.background_active(scope={"fingerprint": "b"}))
                self.assertTrue(store.spawn_background(["fake"], scope={"fingerprint": "b"}))
            self.assertTrue(store.background_active(scope={"fingerprint": "b"}))

    def test_background_owner_cannot_clear_a_stale_replacement_or_strand_spawn_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CacheStore(Path(temporary) / "cache")
            with mock.patch("rofi_agent_plus.cache.subprocess.Popen"):
                self.assertTrue(store.spawn_background(["fake"], scope={"fingerprint": "a"}))
                old = json.loads(store.background_path.read_text())
                os.utime(store.background_path, (0, 0))
                self.assertTrue(store.spawn_background(["fake"], scope={"fingerprint": "a"}))
            replacement = json.loads(store.background_path.read_text())
            old_worker = CacheStore(store.root)
            old_worker._background_owner = old["owner"]
            old_worker.clear_owned_background_marker()
            self.assertEqual(
                replacement["owner"], json.loads(store.background_path.read_text())["owner"]
            )
            store.clear_background_marker(owner=replacement["owner"], scope={"fingerprint": "a"})
            with mock.patch("rofi_agent_plus.cache.subprocess.Popen", side_effect=OSError("nope")):
                self.assertFalse(store.spawn_background(["fake"], scope={"fingerprint": "a"}))
            self.assertFalse(store.background_path.exists())

    def test_late_owner_cannot_write_after_authority_switch(self) -> None:
        old = {
            "kind": "contract",
            "capability": CONTRACT_CAPABILITY,
            "meshRevision": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        }
        new = {
            "kind": "contract",
            "capability": CONTRACT_CAPABILITY,
            "meshRevision": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        }

        class Backend:
            def __init__(self, identity: dict[str, object]) -> None:
                self.identity = identity

            def prepare(self) -> None:
                return None

            def stream(self, _config: PickerConfig):
                yield {
                    "event": "refresh-started",
                    "hosts": ["alpha"],
                    "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                    "backend": old,
                }
                yield {
                    "event": "host-complete",
                    "host": "alpha",
                    "sessions": [],
                    "errors": [],
                    "backend": old,
                }
                yield {"event": "refresh-finished", "backend": old}

        with tempfile.TemporaryDirectory() as temporary:
            selector = mock.Mock(side_effect=[Backend(old), Backend(new)])
            store = CacheStore(Path(temporary) / "cache", backend_selector=selector)
            returned = store.refresh(self.config, force=True)
            self.assertEqual(new, returned["backend"])
            self.assertEqual(0, returned["generatedAt"])
            self.assertIsNone(store.load(self.config.fingerprint, old))
            self.assertIsNone(store.load(self.config.fingerprint, new))

    def test_contract_prepare_error_is_cached_and_presentation_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CacheStore(
                Path(temporary) / "cache",
                backend_selector=mock.Mock(side_effect=ContractError("bad mesh")),
            )
            written = store.refresh(self.config, force=True)
            context = store.presentation_context(self.config)
            visible = store.load_current(self.config, context)
            self.assertEqual("contract-error", written["backend"]["kind"])
            self.assertEqual(written, visible)
            self.assertIn("bad mesh", json.dumps(visible["errors"]))

    def test_structural_rofi_callback_renders_a_present_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CacheStore(
                Path(temporary) / "cache",
                backend_selector=mock.Mock(side_effect=ContractError("bad mesh now")),
            )
            rendered = io.StringIO()
            with mock.patch("sys.stdout", rendered):
                self.assertEqual(
                    0,
                    run_rofi(
                        {"ROFI_RETV": "0"},
                        store=store,
                        config=self.config,
                    ),
                )
            self.assertIn("Contract refresh failed", rendered.getvalue())
            self.assertIn("bad mesh now", rendered.getvalue())

    def test_refresh_uses_the_prepared_presentation_context_for_its_stream(self) -> None:
        identity = {
            "kind": "contract",
            "capability": CONTRACT_CAPABILITY,
            "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
        }

        class Backend:
            def __init__(self) -> None:
                self.prepared = 0
                self.streamed = 0
                self.identity = identity

            def prepare(self) -> None:
                self.prepared += 1

            def stream(self, _config: PickerConfig):
                self.streamed += 1
                yield {
                    "event": "refresh-started",
                    "hosts": ["alpha"],
                    "hostCatalog": [{"hostId": "alpha", "display": "Alpha", "local": True}],
                    "backend": identity,
                }
                yield {"event": "host-complete", "host": "alpha", "sessions": [], "errors": []}
                yield {"event": "refresh-finished", "backend": identity}

        with tempfile.TemporaryDirectory() as temporary:
            prepared = Backend()
            authority_check = Backend()
            store = CacheStore(
                Path(temporary) / "cache",
                backend_selector=mock.Mock(side_effect=[prepared, authority_check]),
            )
            context = store.presentation_context(self.config)
            store.refresh(self.config, force=True, context=context)
            self.assertEqual(1, prepared.streamed)
            self.assertEqual(1, prepared.prepared)
