"""Hermetic Host Mesh/Tmux Session v1 consumer tests."""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import engine
from rofi_agent_plus.cache import CACHE_VERSION, CacheStore, _merge_host_snapshot, build_snapshot
from rofi_agent_plus.config import PickerConfig
from rofi_agent_plus.contract_backend import (
    CommandOutput,
    ContractBackend,
    ContractError,
    StaleMeshError,
    _error_envelope_code,
    _inventory,
    _inventory_args,
    _raise_command_failure,
    _run_bounded,
    parse_mesh,
    select_backend,
)
from rofi_agent_plus.engine import PickerError
from rofi_agent_plus.observer_contract import ObserverSnapshot, parse_snapshot
from rofi_agent_plus.rofi import _open_selection, _parse_selection, render_snapshot, run_rofi
from rofi_agent_plus.view_preferences import ViewPreferenceStore

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
        beta["sessions"][0]["options"].pop("@claude_name")
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
        with tempfile.TemporaryDirectory() as directory:
            in_directory = _run_bounded(
                [sys.executable, "-c", "import os;print(os.getcwd())"],
                cwd=directory,
                timeout=2,
            )
            self.assertEqual(directory, in_directory.stdout.strip())

    def test_timeout_stops_descendant_after_parent_exits(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            marker = Path(raw_directory) / "descendant-survived"
            child = f"import time,pathlib;time.sleep(.25);pathlib.Path({str(marker)!r}).touch()"
            parent = (
                "import subprocess,sys;"
                f"subprocess.Popen([sys.executable,'-c',{child!r}],"
                "stdout=sys.stdout,stderr=sys.stderr)"
            )
            with self.assertRaisesRegex(ContractError, "timed out"):
                _run_bounded([sys.executable, "-c", parent], timeout=0.05)
            time.sleep(0.3)
            self.assertFalse(marker.exists())

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


def _observer_row(
    host_scope: str, provider: str = "codex", *, work: str = "waiting"
) -> dict[str, object]:
    from observer_fixtures import native_row

    return native_row(host_scope, provider, phase=work)


def _observer_document(
    host_scope: str, *, sessions=None, unavailable=(), providers=("codex", "claude")
):
    from observer_fixtures import document

    return document(
        host_scope, sessions=sessions or [], unavailable=unavailable, providers=providers
    )


class ContractBackendAssemblyTest(unittest.TestCase):
    def test_selected_provider_is_sent_to_local_and_remote_observer(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        for provider in ("codex", "claude"):
            for host in mesh.hosts:
                with self.subTest(provider=provider, host=host.host_id):
                    payload = _observer_document(host.host_id, providers=(provider,))
                    calls: list[list[str]] = []
                    backend = ContractBackend(
                        "rofi-ssh-plus",
                        "rofi-tmux-plus",
                        runner=lambda argv, calls=calls, payload=payload, **_kwargs: (
                            calls.append(argv) or output(argv, payload)
                        ),
                    )
                    backend.mesh = mesh
                    backend._remote_command = mock.Mock(
                        return_value=("beta-first.test", output([], payload))
                    )  # type: ignore[method-assign]
                    _route, snapshot = backend._observer_snapshot(
                        host, time.monotonic() + 2, providers=(provider,)
                    )
                    self.assertEqual((), snapshot.errors)
                    requested = (
                        calls[0]
                        if host == mesh.local
                        else backend._remote_command.call_args.kwargs["remote_argv"]
                    )
                    self.assertEqual(
                        [
                            "agent-observer",
                            "snapshot",
                            "--host-scope",
                            host.host_id,
                            "--provider",
                            provider,
                        ],
                        requested,
                    )

    def test_host_policy_limits_discovery_and_failure_stages(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = fixture("tmux-inventory-v1.json")
        for provider, stage in (("codex", "threads"), ("claude", "claude")):
            with self.subTest(provider=provider):
                calls: dict[str, tuple[str, ...]] = {}
                backend = ContractBackend(
                    "rofi-ssh-plus",
                    "rofi-tmux-plus",
                    runner=lambda argv, **_kwargs: output(argv, inventory),
                )
                backend.mesh = mesh

                def observe(host, _deadline, *, providers=("codex", "claude"), calls=calls):
                    calls[host.host_id] = providers
                    if host.host_id == "beta":
                        return None, ContractError("Observer transport unavailable")
                    return None, ObserverSnapshot((), (), "current")

                backend._observer_snapshot = observe  # type: ignore[method-assign]
                config = PickerConfig().with_overrides(host_providers={"beta": [provider]})
                events = backend._once(config)
                self.assertEqual({"alpha": ("codex", "claude"), "beta": (provider,)}, calls)
                host_events = {
                    event["host"]: event
                    for event in events
                    if event.get("event") == "host-complete"
                }
                self.assertEqual([], host_events["alpha"]["errors"])
                self.assertEqual(
                    [stage], [error["stage"] for error in host_events["beta"]["errors"]]
                )

    def test_local_snapshot_uses_only_the_host_scoped_observer_command(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        calls: list[list[str]] = []
        payload = _observer_document("alpha")
        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: calls.append(argv) or output(argv, payload),
        )
        backend.mesh = mesh
        route, snapshot = backend._observer_snapshot(mesh.local, time.monotonic() + 2)
        self.assertIsNone(route)
        self.assertIsInstance(snapshot, ObserverSnapshot)
        self.assertEqual([["agent-observer", "snapshot", "--host-scope", "alpha"]], calls)

    def test_remote_snapshot_passes_exact_host_scoped_command_through_mesh_transport(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = mesh
        payload = _observer_document("beta")
        backend._remote_command = mock.Mock(return_value=("beta-first.test", output([], payload)))  # type: ignore[method-assign]
        route, snapshot = backend._observer_snapshot(mesh.hosts[1], time.monotonic() + 2)
        self.assertEqual("beta-first.test", route)
        self.assertIsInstance(snapshot, ObserverSnapshot)
        self.assertEqual(
            ["agent-observer", "snapshot", "--host-scope", "beta"],
            backend._remote_command.call_args.kwargs["remote_argv"],  # type: ignore[attr-defined]
        )
        self.assertIsNone(backend._remote_command.call_args.kwargs["input_data"])  # type: ignore[attr-defined]

    def test_observer_rows_remain_unassociated_with_matching_tmux_provider_options(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = fixture("tmux-inventory-v1.json")
        inventory["hosts"] = [inventory["hosts"][0]]
        snapshot = parse_snapshot(
            (
                json.dumps(
                    _observer_document("alpha", sessions=[_observer_row("alpha", work="blocked")]),
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
            expected_host_scope="alpha",
        )
        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: output(argv, inventory),
        )
        backend.mesh = mesh
        backend._observer_snapshot = lambda _host, _deadline: (None, snapshot)  # type: ignore[method-assign]
        events = backend._once(PickerConfig(), host_ids=("alpha",))
        row = events[1]["sessions"][0]
        self.assertEqual("codex", row["kind"])
        self.assertEqual("thread", row["nativeIdKind"])
        self.assertEqual("blocked", row["activityState"])
        self.assertTrue(row["active"])
        self.assertEqual("22222222-2222-4222-8222-222222222222", row["nativeIds"]["sessionId"])
        self.assertEqual("native_managed_endpoint", row["observerSource"]["runtime"]["topology"])
        self.assertIn("pid", row["observerSource"]["runtime"])
        self.assertNotIn("tmux", row)
        self.assertNotIn("providerOptionVerified", row)

    def test_picker_excludes_only_confirmed_codex_children_from_partial_snapshot(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = fixture("tmux-inventory-v1.json")
        inventory["hosts"] = [inventory["hosts"][0]]

        def classified_row(thread_id, session_id, *, source_kind, thread_kind):
            row = _observer_row("alpha")
            row["identity"]["nativeId"] = thread_id
            row["nativeIds"]["threadId"] = thread_id
            row["nativeIds"]["sessionId"] = session_id
            row["kind"] = thread_kind
            return row

        user = classified_row(
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            source_kind="cli",
            thread_kind="user",
        )
        unknown = classified_row(
            "33333333-3333-4333-8333-333333333333",
            "44444444-4444-4444-8444-444444444444",
            source_kind="unknown",
            thread_kind="unknown",
        )
        child = classified_row(
            "55555555-5555-4555-8555-555555555555",
            "66666666-6666-4666-8666-666666666666",
            source_kind="subagent",
            thread_kind="child",
        )
        snapshot = parse_snapshot(
            (
                json.dumps(
                    _observer_document(
                        "alpha",
                        sessions=[user, unknown, child],
                        unavailable=("claude",),
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
            expected_host_scope="alpha",
        )
        self.assertEqual(3, len(snapshot.sessions))
        self.assertEqual("child", snapshot.sessions[2]["threadKind"])

        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: output(argv, inventory),
        )
        backend.mesh = mesh
        backend._observer_snapshot = lambda _host, _deadline: (None, snapshot)  # type: ignore[method-assign]
        events = backend._once(PickerConfig(), host_ids=("alpha",))

        host_event = events[1]
        self.assertEqual(
            {user["identity"]["nativeId"], unknown["identity"]["nativeId"]},
            {row["id"] for row in host_event["sessions"]},
        )
        self.assertEqual(["user", "unknown"], [row["threadKind"] for row in host_event["sessions"]])
        self.assertEqual(["claude"], [error["stage"] for error in host_event["errors"]])

    def test_partial_provider_failure_keeps_healthy_rows_in_host_event(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = fixture("tmux-inventory-v1.json")
        inventory["hosts"] = [inventory["hosts"][0]]
        payload = _observer_document(
            "alpha", sessions=[_observer_row("alpha")], unavailable=("claude",)
        )
        snapshot = parse_snapshot(
            (json.dumps(payload, separators=(",", ":")) + "\n").encode(),
            expected_host_scope="alpha",
        )
        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: output(argv, inventory),
        )
        backend.mesh = mesh
        backend._observer_snapshot = lambda _host, _deadline: (None, snapshot)  # type: ignore[method-assign]
        events = backend._once(PickerConfig(), host_ids=("alpha",))
        self.assertEqual(["codex"], [row["kind"] for row in events[1]["sessions"]])
        self.assertEqual(["claude"], [error["stage"] for error in events[1]["errors"]])

    def test_malformed_observer_command_fails_closed_without_provider_fallback(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        calls: list[list[str]] = []
        inventory = fixture("tmux-inventory-v1.json")
        inventory["hosts"] = [inventory["hosts"][0]]

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            calls.append(argv)
            if argv[0] == "agent-observer":
                return CommandOutput(tuple(argv), 0, "not-json\n", "", stdout_bytes=b"not-json\n")
            return output(argv, inventory)

        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=runner)
        backend.mesh = mesh
        events = backend._once(PickerConfig(), host_ids=("alpha",))
        self.assertEqual([], events[1]["sessions"])
        self.assertEqual(
            ["claude", "threads"], sorted(error["stage"] for error in events[1]["errors"])
        )
        self.assertEqual({"agent-observer", "rofi-tmux-plus"}, {call[0] for call in calls})

    def test_tmux_failure_is_reported_independently_of_observer_rows(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        snapshot = parse_snapshot(
            (
                json.dumps(
                    _observer_document("alpha", sessions=[_observer_row("alpha")]),
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
            expected_host_scope="alpha",
        )
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = mesh
        backend._observer_snapshot = lambda _host, _deadline: (None, snapshot)  # type: ignore[method-assign]
        backend._inventory_document = mock.Mock(side_effect=ContractError("inventory unavailable"))  # type: ignore[method-assign]
        events = backend._once(PickerConfig(), host_ids=("alpha",))
        self.assertEqual(1, len(events[1]["sessions"]))
        self.assertEqual(["tmux"], [error["stage"] for error in events[1]["errors"]])

    def test_inventory_starts_before_observer_collection_completes(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory_started = threading.Event()
        inventory = fixture("tmux-inventory-v1.json")

        def runner(argv: list[str], **_kwargs: object) -> CommandOutput:
            inventory_started.set()
            return output(argv, inventory)

        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=runner)
        backend.mesh = mesh

        def observe(_host: object, _deadline: float):
            self.assertTrue(inventory_started.wait(0.5))
            return None, ObserverSnapshot((), (), "current")

        backend._observer_snapshot = observe  # type: ignore[method-assign]
        events = backend._once(PickerConfig(), host_ids=("alpha",))
        self.assertTrue(inventory_started.is_set())
        self.assertEqual("refresh-finished", events[-1]["event"])

    def test_selected_refresh_emits_the_full_ordered_host_catalog(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        inventory = copy.deepcopy(fixture("tmux-inventory-v1.json"))
        inventory["hosts"] = [inventory["hosts"][1]]
        backend = ContractBackend(
            "rofi-ssh-plus",
            "rofi-tmux-plus",
            runner=lambda argv, **_kwargs: output(argv, inventory),
        )
        backend.mesh = mesh
        backend._observer_snapshot = lambda _host, _deadline: (
            None,
            ObserverSnapshot((), (), "current"),
        )  # type: ignore[method-assign]
        events = backend._once(PickerConfig(), host_ids=("beta",))
        self.assertEqual(["beta"], events[0]["hosts"])
        self.assertEqual(["alpha", "beta"], [row["hostId"] for row in events[0]["hostCatalog"]])

    def test_stale_mesh_retries_once_before_any_events_escape(self) -> None:
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = parse_mesh(fixture("mesh-v1.json"))
        events = [{"event": "refresh-finished"}]
        backend._once = mock.Mock(side_effect=[StaleMeshError("changed"), events])  # type: ignore[method-assign]
        backend.prepare = mock.Mock()  # type: ignore[method-assign]
        self.assertEqual(events, backend.stream(PickerConfig()))
        backend.prepare.assert_called_once()

    def test_only_typed_tmux_stale_envelope_retries_the_stream(self) -> None:
        mesh = parse_mesh(fixture("mesh-v1.json"))
        backend = ContractBackend("rofi-ssh-plus", "rofi-tmux-plus", runner=lambda *_a, **_k: None)
        backend.mesh = mesh
        backend._observer_snapshot = lambda _host, _deadline: (
            None,
            ObserverSnapshot((), (), "current"),
        )  # type: ignore[method-assign]
        backend._run = lambda argv, **_kwargs: output(
            argv,
            {"schemaVersion": 1, "ok": False, "error": {"code": "stale_mesh", "message": "stale"}},
            code=2,
        )  # type: ignore[method-assign]
        with self.assertRaises(StaleMeshError):
            backend._once(PickerConfig(), host_ids=("alpha",))
        backend._run = lambda argv, **_kwargs: CommandOutput(
            tuple(argv), 2, "{}", "a stale_mesh-looking diagnostic"
        )  # type: ignore[method-assign]
        events = backend._once(PickerConfig(), host_ids=("alpha",))
        self.assertIn("tmux", str(events[1]["errors"]))

    def test_rofi_open_fails_closed_without_a_prepared_contract(self) -> None:
        with self.assertRaisesRegex(PickerError, "prepared authority"):
            _open_selection({"contractMode": True, "kind": "codex", "id": THREAD}, PickerConfig())
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
        }
        rendered = render_snapshot({"sessions": [row], "errors": []})
        info = rendered.split("\x00info\x1f", 2)[2].split("\x1fmeta\x1f", 1)[0]
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
        }
        fresh = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "tmux": {"sessionId": "$2"},
            "tmuxSession": "new",
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
        }
        fresh = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "providerOptionVerified": True,
            "tmux": {"sessionId": "$2"},
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
        }
        fresh = {
            "contractMode": True,
            "hostId": "alpha",
            "kind": "codex",
            "id": THREAD,
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
                    "sessions": [
                        {
                            "hostId": "alpha",
                            "kind": "codex",
                            "id": THREAD,
                            "namespace": "sha256:codex-namespace",
                            "nativeIdKind": "thread",
                        }
                    ],
                    "errors": [],
                },
                "beta": {
                    "generatedAt": stale_at,
                    "sessions": [
                        {
                            "hostId": "beta",
                            "kind": "codex",
                            "id": THREAD,
                            "namespace": "sha256:codex-namespace",
                            "nativeIdKind": "thread",
                        }
                    ],
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
                            {
                                "hostId": "alpha",
                                "kind": "codex",
                                "id": THREAD,
                                "recencyAt": 2,
                                "namespace": "sha256:codex-namespace",
                                "nativeIdKind": "thread",
                            }
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
                    "sessions": [
                        {
                            "hostId": "alpha",
                            "kind": "codex",
                            "id": THREAD,
                            "namespace": "sha256:codex-namespace",
                            "nativeIdKind": "thread",
                        }
                    ],
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
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
                        preference_store=ViewPreferenceStore(Path(temporary) / "state"),
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
