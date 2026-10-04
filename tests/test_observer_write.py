"""Public write routing, provider effect and native TTY handoff ownership."""

from __future__ import annotations

import copy
import json
import os
import shlex
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import test_contract_lifecycle as lifecycle_fixtures
from observer_fixtures import plan_for, result_for
from test_contract_backend import fixture as mesh_fixture
from test_contract_lifecycle import descriptor, row, success

from rofi_agent_plus.contract_backend import (
    CommandOutput,
    ContractBackend,
    ContractError,
    StaleMeshError,
    WriteOperationError,
    _marker,
    _run_bounded,
    parse_mesh,
)
from rofi_agent_plus.contract_lifecycle import LifecycleError
from rofi_agent_plus.observer_write import (
    WriteBoundaryError,
    completed,
    encoded,
    prepared,
    request_for,
)


def output(argv, value, *, code=0, stderr="", timeout=False):
    raw = encoded(value)
    return CommandOutput(tuple(argv), code, raw.decode(), stderr, timeout, raw, stderr.encode())


class WritePublicBoundaryTest(unittest.TestCase):
    def test_write_timeout_preserves_descendant_outside_launcher_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "child.pid"
            script = "import os,time,sys; p=os.fork(); open(sys.argv[1],'w').write(str(p)) if p else None; time.sleep(10)"
            child = None
            try:
                with self.assertRaises(ContractError):
                    _run_bounded(
                        [sys.executable, "-c", script, str(path)],
                        timeout=0.2,
                        own_process_group=False,
                    )
                child = int(path.read_text())
                os.kill(child, 0)
            finally:
                if child is not None:
                    try:
                        os.kill(child, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def request(self, provider="codex", operation="resume", host="alpha"):
        selected = row(provider)
        selected["hostId"] = host
        selected["observerIdentity"]["hostScope"] = host
        return request_for(selected, operation)

    def test_zero_row_new_request_and_exact_resume_reference(self):
        selected = row()
        new = request_for(selected, "new")
        self.assertIsNone(new["reference"])
        self.assertEqual(selected["cwd"], new["cwd"])
        resume = request_for(selected, "resume")
        self.assertEqual(selected["observerIdentity"], resume["reference"])
        selected["observerSource"]["schemaVersion"] = 1
        with self.assertRaises(WriteBoundaryError):
            request_for(selected, "resume")

    def test_plan_result_context_and_effect_validation(self):
        request = self.request()
        plan = plan_for(request)
        prepared(encoded(plan), request)
        completed(encoded(result_for(request)), plan)
        for defect in ("scope", "request", "guard", "effect"):
            result = result_for(request)
            if defect == "scope":
                result["resultingIdentity"]["namespace"] = "sha256:" + "f" * 64
            elif defect == "request":
                result["requestedIdentity"] = None
            elif defect == "guard":
                result["handoff"]["guard"]["uid"] = 2000
            else:
                result.update(effect="uncertain", status="uncertain")
            with self.subTest(defect=defect), self.assertRaises(WriteBoundaryError):
                completed(encoded(result), plan)

    def test_copied_confirmed_result_keeps_distinct_requested_and_actual_identity(self):
        request = self.request("claude")
        plan = plan_for(request)
        result = result_for(request)
        actual = copy.deepcopy(request["reference"])
        actual["nativeId"] = "55555555-5555-4555-8555-555555555555"
        target = {**request, "reference": actual}
        result.update(
            status="ready",
            effect="confirmed",
            resultingIdentity=actual,
            identityPending=False,
            handoff=plan_for(target),
        )
        accepted = completed(encoded(result), plan)
        self.assertNotEqual(accepted["requestedIdentity"], accepted["resultingIdentity"])

    def test_local_prepare_execute_uses_only_public_client_and_launcher_ownership(self):
        request = self.request()
        plan = plan_for(request)
        calls = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            return output(argv, plan if "prepare" in argv else result_for(request))

        backend = ContractBackend("ssh-plus", "tmux-plus", runner=runner)
        backend.mesh = parse_mesh(mesh_fixture("mesh-v1.json"))
        backend.observer_write(request, deadline=time.monotonic() + 10)
        self.assertEqual(["prepare", "execute"], [call[0][1] for call in calls])
        self.assertTrue(all(call[0][0] == "agent-observer-write" for call in calls))
        self.assertEqual(request, json.loads(calls[0][1]["input_data"]))
        self.assertFalse(calls[1][1]["own_process_group"])

    def test_remote_execute_pins_prepared_route_and_never_falls_through(self):
        request = self.request(host="beta")
        plan = plan_for(request)
        calls = []
        for defect in (
            "missing_marker",
            "timeout",
            "bad_result",
            "transport",
            "uncertain",
            "success",
        ):

            def runner(argv, defect=defect, **kwargs):
                calls.append(list(argv))
                nonce = shlex.split(argv[-1])[4]
                if defect == "transport":
                    raise ContractError("connection reset")
                result = result_for(request)
                if defect == "uncertain":
                    result.update(status="uncertain", effect="uncertain", handoff=None)
                if defect == "bad_result":
                    result["operation"] = "new"
                return output(
                    argv,
                    result,
                    code=3 if defect == "uncertain" else 0,
                    stderr="" if defect == "missing_marker" else _marker(nonce),
                    timeout=defect == "timeout",
                )

            backend = ContractBackend("ssh-plus", "tmux-plus", runner=runner)
            backend.mesh = parse_mesh(mesh_fixture("mesh-v1.json"))
            selected = backend.mesh.hosts[1].routes[-1]
            backend._remote_command = mock.Mock(
                return_value=(selected.destination, output([], plan))
            )
            calls.clear()
            with self.subTest(defect=defect):
                if defect == "success":
                    backend.observer_write(request, deadline=time.monotonic() + 10)
                else:
                    with self.assertRaises(WriteOperationError) as raised:
                        backend.observer_write(request, deadline=time.monotonic() + 10)
                    self.assertEqual("uncertain", raised.exception.effect)
                self.assertEqual(1, len(calls))
                self.assertEqual(selected.destination, calls[0][-2])
                self.assertIn("agent-observer-write execute", calls[0][-1])

    def test_typed_rejection_and_confirmed_handoff_failure_do_not_redispatch(self):
        request = self.request("claude", "new")
        plan = plan_for(request)
        actual = copy.deepcopy(row("claude")["observerIdentity"])
        for mode in ("trust", "confirmed", "malformed"):
            calls = []

            def runner(argv, mode=mode, calls=calls, **kwargs):
                calls.append(list(argv))
                if "prepare" in argv:
                    return output(argv, plan)
                if mode == "malformed":
                    return CommandOutput(tuple(argv), 0, "private-sentinel", "")
                result = result_for(request)
                result.update(
                    status="rejected" if mode == "trust" else "uncertain",
                    reason="trust_required" if mode == "trust" else "handoff_unavailable",
                    effect="none" if mode == "trust" else "confirmed",
                    resultingIdentity=None if mode == "trust" else actual,
                    identityPending=False,
                    handoff=None,
                )
                return output(argv, result, code=3)

            backend = ContractBackend("ssh-plus", "tmux-plus", runner=runner)
            backend.mesh = parse_mesh(mesh_fixture("mesh-v1.json"))
            with self.subTest(mode=mode), self.assertRaises(WriteOperationError) as raised:
                backend.observer_write(request, deadline=time.monotonic() + 10)
            self.assertEqual(
                {"trust": "none", "confirmed": "confirmed", "malformed": "uncertain"}[mode],
                raised.exception.effect,
            )
            self.assertNotIn("private-sentinel", str(raised.exception))
            self.assertEqual(2, len(calls))


class WriteLifecycleTest(unittest.TestCase):
    setUp = lifecycle_fixtures.ContractLifecycleTest.setUp
    tearDown = lifecycle_fixtures.ContractLifecycleTest.tearDown
    harness = lifecycle_fixtures.ContractLifecycleTest.harness
    selection = staticmethod(lifecycle_fixtures.ContractLifecycleTest.selection)

    def test_handoff_invokes_public_entry_and_never_focuses_old_wrapper(self):
        selected = row()
        lifecycle, _store, backend, _context = self.harness([selected], [success(descriptor())])
        lifecycle.open_or_create(self.selection())
        self.assertEqual(1, len(backend.writes))
        self.assertEqual("resume", backend.writes[0]["operation"])
        argv = backend.calls[0][0]
        self.assertEqual("create", argv[1])
        self.assertEqual(
            ["agent-observer-write", "enter", "--plan-json"], argv[argv.index("--") + 1 : -1]
        )
        self.assertEqual(backend.writes[0], json.loads(argv[-1])["request"])

    def test_new_has_no_old_reference_or_launch_options_and_uses_exact_cwd(self):
        selected = row(cwd="/work/a b;$(literal)")
        lifecycle, _store, backend, _context = self.harness([selected], [success(descriptor())])
        lifecycle.new_session_here({**self.selection(), "cwd": selected["cwd"]})
        request = backend.writes[0]
        self.assertIsNone(request["reference"])
        self.assertEqual(selected["cwd"], request["cwd"])
        argv = backend.calls[0][0]
        self.assertNotIn("--set-option", argv)
        self.assertEqual(selected["cwd"], json.loads(argv[-1])["request"]["cwd"])

    def test_uncertain_execute_or_failed_confirmed_viewer_never_recreates(self):
        for after_create in (False, True):
            selected = row("claude")
            lifecycle, _store, backend, _context = self.harness(
                [selected],
                [
                    (
                        2,
                        {
                            "schemaVersion": 1,
                            "ok": False,
                            "error": {"code": "stale_mesh", "message": "stale", "hostId": "alpha"},
                        },
                    )
                ],
            )
            if after_create:
                request = request_for(selected, "new")
                actual = copy.deepcopy(selected["observerIdentity"])
                result = result_for(request)
                result.update(
                    status="ready",
                    effect="confirmed",
                    resultingIdentity=actual,
                    identityPending=False,
                    handoff=plan_for({**request, "operation": "resume", "reference": actual}),
                )
                backend.write_result = result
            else:
                backend.write_errors = [
                    WriteOperationError("uncertain; no retry", effect="uncertain")
                ]
            with self.subTest(after_create=after_create), self.assertRaises(LifecycleError):
                lifecycle.new_session_here({**self.selection("claude"), "cwd": selected["cwd"]})
            self.assertEqual(1, len(backend.writes))
            self.assertEqual(1, backend.streams)

    def test_preparation_stale_can_reobserve_before_one_successful_dispatch(self):
        selected = row()
        lifecycle, _store, backend, _context = self.harness([selected], [success(descriptor())])
        backend.write_errors = [StaleMeshError("preparation authority changed")]
        lifecycle.open_or_create(self.selection())
        self.assertEqual(2, len(backend.writes))
        self.assertEqual(1, len(backend.calls))
        self.assertEqual(2, backend.streams)

    def test_typed_viewer_collision_retries_plan_but_not_write(self):
        lifecycle, _store, backend, _context = self.harness(
            [row()],
            [
                (
                    2,
                    {
                        "schemaVersion": 1,
                        "ok": False,
                        "error": {"code": "session_exists", "message": "exists", "hostId": "alpha"},
                    },
                ),
                success(descriptor()),
            ],
        )
        lifecycle.open_or_create(self.selection())
        self.assertEqual(1, len(backend.writes))
        self.assertEqual(backend.calls[0][0][-1], backend.calls[1][0][-1])
