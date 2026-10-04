"""Hermetic Tmux Session v1 lifecycle consumer tests."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus.cache import CacheStore, PresentationContext, build_snapshot
from rofi_agent_plus.config import PickerConfig
from rofi_agent_plus.contract_backend import CommandOutput
from rofi_agent_plus.contract_lifecycle import (
    ContractLifecycle,
    LifecycleError,
    _provider_row,
    _run_json,
    _success_response,
    fast_open_selection,
)
from rofi_agent_plus.rofi import _open_selection, run_rofi, selection_payload
from rofi_agent_plus.view_preferences import ViewPreferenceStore

ROOT = Path(__file__).parent / "fixtures" / "contract" / "lifecycle"
THREAD = "11111111-1111-1111-1111-111111111111"
REVISION = "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262"
BACKEND = {
    "kind": "contract",
    "capability": "host-mesh-v1+tmux-session-v1",
    "meshRevision": REVISION,
}


def fixture(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text())


def descriptor(
    *,
    session_id: str = "$4",
    created_at: int = 5,
    name: str = "agent-codex-11111111",
    pending: bool = False,
) -> dict[str, object]:
    return {
        "hostId": "alpha",
        "serverGeneration": "tmux-v1:alpha",
        "sessionId": session_id,
        "createdAt": created_at,
        "name": name,
        "activityAt": 6,
        "lastAttachedAt": None,
        "attachedClients": 0,
        "pending": pending,
        "windowCount": 1,
        "sessionPath": "/work",
        "currentWindow": "shell",
        "currentPath": "/work",
    }


def success(session: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "ok": True,
        "meshRevision": REVISION,
        "session": session,
        "focused": False,
        "terminalLaunched": True,
    }


def failure(code: str, message: str = "failed") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "ok": False,
        "error": {"code": code, "message": message, "hostId": "alpha"},
    }


def row(
    kind: str = "codex",
    *,
    tmux: bool = True,
    active: bool = False,
    activity: str = "idle",
    cwd: str = "/work",
    session_id: str = "$4",
    created_at: int = 5,
) -> dict[str, object]:
    from observer_fixtures import document, native_row

    from rofi_agent_plus.observer_contract import parse_snapshot

    raw = native_row("alpha", kind)
    raw["cwd"] = cwd
    payload = document("alpha", sessions=[raw])
    item = dict(
        parse_snapshot(json.dumps(payload).encode(), expected_host_scope="alpha").sessions[0]
    )
    item.update(
        contractMode=True,
        backend=dict(BACKEND),
        hostId="alpha",
        host="Alpha",
        active=active,
        activityState=activity,
        name="native name",
    )
    if tmux:
        item["tmux"] = {
            "meshRevision": REVISION,
            "serverGeneration": "tmux-v1:alpha",
            "sessionId": session_id,
            "createdAt": created_at,
            "observedName": "old-name",
        }
        item["tmuxSession"] = "old-name"
    return item


class FakeBackend:
    tmux_command = "rofi-tmux-plus"

    def __init__(
        self, rows: list[dict[str, object]] | list[list[dict[str, object]]], responses: list[object]
    ):
        self.identity = dict(BACKEND)
        self._rows = rows if rows and isinstance(rows[0], list) else [rows]  # type: ignore[index]
        self._responses = list(responses)
        self.calls: list[tuple[list[str], float]] = []
        self.preflights: list[tuple[str, str, str, float]] = []
        self.preflight_error: Exception | None = None
        self.prepared = 0
        self.writes = []
        self.write_errors = []
        self.write_result = None
        self.stream_kwargs: list[dict[str, object]] = []
        self.streams = 0
        self.errors: list[dict[str, object]] = []
        self.stream_errors: list[Exception] = []

    def prepare(self) -> None:
        self.prepared += 1

    def observer_write(self, request, *, deadline):
        from observer_fixtures import result_for

        self.writes.append(copy.deepcopy(request))
        if self.write_errors:
            raise self.write_errors.pop(0)
        return self.write_result or result_for(request)

    def stream(self, _config: PickerConfig, **kwargs: object):
        self.stream_kwargs.append(dict(kwargs))
        if self.stream_errors:
            raise self.stream_errors.pop(0)
        index = min(self.streams, len(self._rows) - 1)
        self.streams += 1
        rows = self._rows[index]
        return [
            {
                "event": "refresh-started",
                "hosts": ["alpha"],
                "hostCatalog": [
                    {"hostId": "alpha", "display": "Alpha", "local": True},
                ],
                "backend": dict(self.identity),
            },
            {
                "event": "host-complete",
                "host": "alpha",
                "sessions": copy.deepcopy(rows),
                "errors": copy.deepcopy(self.errors),
                "backend": dict(self.identity),
            },
            {"event": "refresh-finished", "backend": dict(self.identity)},
        ]

    def _run(self, argv: list[str], **kwargs: object) -> CommandOutput:
        self.calls.append((list(argv), float(kwargs["timeout"])))
        response = self._responses.pop(0)
        if isinstance(response, CommandOutput):
            return response
        if isinstance(response, tuple):
            code, payload = response
            encoded = json.dumps(payload, separators=(",", ":")) + "\n"
            return CommandOutput(
                tuple(argv), code, encoded, "failure", stdout_bytes=encoded.encode()
            )
        encoded = json.dumps(response, separators=(",", ":")) + "\n"
        return CommandOutput(tuple(argv), 0, encoded, "", stdout_bytes=encoded.encode())


class ContractLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.config = PickerConfig(max_sessions=40)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def harness(
        self,
        rows: list[dict[str, object]] | list[list[dict[str, object]]],
        responses: list[object],
    ) -> tuple[ContractLifecycle, CacheStore, FakeBackend, PresentationContext]:
        backend = FakeBackend(rows, responses)
        store = CacheStore(Path(self.temporary.name) / "cache", backend_selector=lambda: backend)
        context = PresentationContext(self.config.fingerprint, dict(BACKEND), selected=backend)
        return ContractLifecycle(store, self.config, context), store, backend, context

    @staticmethod
    def selection(kind: str = "codex") -> dict[str, object]:
        return {
            "contractMode": True,
            "backend": dict(BACKEND),
            "hostId": "alpha",
            "kind": kind,
            "id": row(kind)["id"],
            "namespace": row(kind)["namespace"],
            "nativeIdKind": "thread" if kind == "codex" else "session",
        }

    def test_lifecycle_exit_and_body_envelopes_must_match(self) -> None:
        for returncode, payload in (
            (2, success(descriptor())),
            (0, failure("operation_failed")),
        ):
            with self.subTest(returncode=returncode):
                encoded = json.dumps(payload, separators=(",", ":")) + "\n"
                command = CommandOutput(
                    ("rofi-tmux-plus", "open"),
                    returncode,
                    encoded,
                    "",
                    stdout_bytes=encoded.encode(),
                )
                lifecycle, _store, backend, _context = self.harness([row()], [command])
                with self.assertRaises(LifecycleError):
                    _run_json(
                        backend,
                        ("rofi-tmux-plus", "open", "--json"),
                        time.monotonic() + 1,
                        "alpha",
                        REVISION,
                        opening=True,
                    )

    def test_launch_option_does_not_authorize_current_viewer_action(self) -> None:
        for kind in ("codex", "claude"):
            with self.subTest(kind=kind):
                selected = self.selection(kind)
                selected["providerOptionVerified"] = True
                selected["tmux"] = copy.deepcopy(row(kind)["tmux"])
                backend = FakeBackend([], [success(descriptor())])
                with self.assertRaises(LifecycleError) as caught:
                    fast_open_selection(selected, backend=backend)
                self.assertEqual("unsupported_binding", caught.exception.code)
                self.assertEqual([], backend.calls)

    def test_lifecycle_revalidates_only_the_selected_host(self) -> None:
        lifecycle, _store, backend, _context = self.harness([row()], [success(descriptor())])
        lifecycle.open_or_create(self.selection())
        self.assertEqual(("alpha",), backend.stream_kwargs[0]["host_ids"])

    def test_local_only_lifecycle_omits_mesh_revision(self) -> None:
        local_backend = FakeBackend([], [])
        local_backend.identity = {**BACKEND, "meshRevision": None}
        local_row = row()
        local_row["backend"] = dict(local_backend.identity)
        local_row["tmux"] = {**local_row["tmux"], "meshRevision": None}
        local_backend._rows = [[local_row]]
        local_response = success(descriptor())
        local_response["meshRevision"] = None
        local_backend._responses = [local_response]
        store = CacheStore(
            Path(self.temporary.name) / "local-cache", backend_selector=lambda: local_backend
        )
        context = PresentationContext(
            self.config.fingerprint, dict(local_backend.identity), selected=local_backend
        )
        selection = self.selection()
        selection["backend"] = dict(local_backend.identity)
        lifecycle = ContractLifecycle(store, self.config, context)
        lifecycle.open_or_create(selection)
        self.assertNotIn("--mesh-revision", local_backend.calls[0][0])

    def test_new_session_here_rejects_noncurrent_and_changed_provider_context(self) -> None:
        selected = self.selection()
        selected["cwd"] = "/work"
        activity_only = row()
        activity_only["sourceObservation"] = "activity-only"
        for label, selected_row in (
            ("activity-only", activity_only),
            ("changed-cwd", row(cwd="/other")),
        ):
            with self.subTest(label=label):
                rows = selected_row if isinstance(selected_row, list) else [selected_row]
                lifecycle, _store, backend, _context = self.harness(rows, [])
                with self.assertRaises(LifecycleError):
                    lifecycle.new_session_here(selected)
                self.assertEqual([], backend.preflights)
                self.assertEqual([], backend.calls)

        retained_backend = FakeBackend([row()], [])
        retained_store = CacheStore(
            Path(self.temporary.name) / "retained-cache", backend_selector=lambda: retained_backend
        )
        retained_context = PresentationContext(
            self.config.fingerprint,
            dict(BACKEND),
            selected=retained_backend,
        )
        retained_store.refresh(self.config, force=True, context=retained_context)
        retained_backend._rows = [[]]
        retained_backend.errors = [{"host": "alpha", "stage": "threads", "message": "offline"}]
        lifecycle = ContractLifecycle(retained_store, self.config, retained_context)
        with self.assertRaises(LifecycleError):
            lifecycle.new_session_here(selected)
        self.assertEqual([], retained_backend.preflights)
        self.assertEqual([], retained_backend.calls)

        duplicate_snapshot = {"backend": dict(BACKEND), "sessions": [row(), row()]}
        with self.assertRaisesRegex(LifecycleError, "no longer unambiguous"):
            _provider_row(duplicate_snapshot, BACKEND, selected)

    def test_new_session_here_uses_current_provider_source_despite_old_tmux_uncertainty(
        self,
    ) -> None:
        selected = self.selection()
        selected["cwd"] = "/work"
        current = row()
        current["tmuxStale"] = True
        current["tmuxAmbiguous"] = True
        current["activityState"] = "unknown"
        lifecycle, _store, backend, _context = self.harness(
            [current], [success(descriptor(pending=True))]
        )
        backend.errors = [
            {"host": "alpha", "stage": "tmux", "message": "inventory unavailable"},
            {"host": "alpha", "stage": "active", "message": "activity unavailable"},
        ]

        lifecycle.new_session_here(selected)

        self.assertEqual(1, len(backend.writes))
        self.assertEqual(1, len(backend.calls))
        self.assertEqual("create", backend.calls[0][0][1])

    def test_incomplete_scoped_revalidation_never_uses_a_retained_local_or_remote_row(self) -> None:
        for host_id in ("alpha", "beta"):
            with self.subTest(host_id=host_id):
                stale = row(tmux=False)
                stale["hostId"] = host_id
                stale["host"] = host_id.title()
                backend = FakeBackend([], [])
                store = CacheStore(
                    Path(self.temporary.name) / f"incomplete-{host_id}",
                    backend_selector=lambda backend=backend: backend,
                )
                context = PresentationContext(
                    self.config.fingerprint,
                    dict(BACKEND),
                    selected=backend,
                )
                previous = build_snapshot(
                    self.config,
                    iter(
                        [
                            {
                                "event": "refresh-started",
                                "hosts": [host_id],
                                "hostCatalog": [
                                    {
                                        "hostId": host_id,
                                        "display": host_id.title(),
                                        "local": True,
                                    }
                                ],
                                "backend": dict(BACKEND),
                            },
                            {
                                "event": "host-complete",
                                "host": host_id,
                                "sessions": [stale],
                                "errors": [],
                                "backend": dict(BACKEND),
                            },
                            {"event": "refresh-finished", "backend": dict(BACKEND)},
                        ]
                    ),
                    now=1,
                )
                store.write(previous)
                backend.stream = mock.Mock(  # type: ignore[method-assign]
                    return_value=[
                        {
                            "event": "refresh-started",
                            "hosts": [host_id],
                            "hostCatalog": [
                                {
                                    "hostId": host_id,
                                    "display": host_id.title(),
                                    "local": True,
                                }
                            ],
                            "backend": dict(BACKEND),
                        }
                    ]
                )
                selection = self.selection()
                selection["hostId"] = host_id
                lifecycle = ContractLifecycle(store, self.config, context)

                with self.assertRaisesRegex(LifecycleError, "incomplete contract host coverage"):
                    lifecycle.open_or_create(selection)

                backend.stream.assert_called_once()
                self.assertEqual((host_id,), backend.stream.call_args.kwargs["host_ids"])
                self.assertEqual([], backend.calls)
                retained = store.load(self.config.fingerprint, BACKEND)
                assert retained is not None
                self.assertEqual(host_id, retained["sessions"][0]["hostId"])

    def test_lifecycle_revalidation_does_not_use_stale_cache_while_lock_is_held(self) -> None:
        lifecycle, store, backend, context = self.harness(
            [row(tmux=False)], [success(descriptor())]
        )
        stale = store.refresh(self.config, force=True, context=context)
        stale["generatedAt"] = 0
        store.write(stale)
        lifecycle.deadline = time.monotonic() + 0.01
        with store.lock() as acquired:
            self.assertTrue(acquired)
            with self.assertRaisesRegex(LifecycleError, "revalidation is already in progress"):
                lifecycle.open_or_create(self.selection())
        self.assertEqual([], backend.calls)

    def test_lifecycle_revalidation_never_consumes_a_recent_other_host_scoped_cache(self) -> None:
        alpha = row(tmux=False)
        beta = row(tmux=False)
        beta["hostId"] = "beta"
        beta["host"] = "Beta"
        backend = FakeBackend([], [success(descriptor())])
        store = CacheStore(
            Path(self.temporary.name) / "recent-other-host",
            backend_selector=lambda: backend,
        )
        context = PresentationContext(self.config.fingerprint, dict(BACKEND), selected=backend)
        full = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha", "beta"],
                        "hostCatalog": [
                            {"hostId": "alpha", "display": "Alpha", "local": True},
                            {"hostId": "beta", "display": "Beta", "local": False},
                        ],
                        "backend": BACKEND,
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "sessions": [alpha],
                        "errors": [],
                        "backend": BACKEND,
                    },
                    {
                        "event": "host-complete",
                        "host": "beta",
                        "sessions": [beta],
                        "errors": [],
                        "backend": BACKEND,
                    },
                    {"event": "refresh-finished", "backend": BACKEND},
                ]
            ),
            now=int(time.time()),
        )
        other_host_scoped = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["alpha"],
                        "hostCatalog": [
                            {"hostId": "alpha", "display": "Alpha", "local": True},
                        ],
                        "backend": BACKEND,
                    },
                    {
                        "event": "host-complete",
                        "host": "alpha",
                        "sessions": [alpha],
                        "errors": [],
                        "backend": BACKEND,
                    },
                    {"event": "refresh-finished", "backend": BACKEND},
                ]
            ),
            full,
            now=int(time.time()),
            retain_unselected_hosts=True,
        )
        self.assertLessEqual(time.time() - other_host_scoped["generatedAt"], 1.0)
        store.write(other_host_scoped)
        selection = self.selection()
        selection["hostId"] = "beta"
        lifecycle = ContractLifecycle(store, self.config, context)
        lifecycle.deadline = time.monotonic() + 0.01

        with store.lock() as acquired:
            self.assertTrue(acquired)
            with self.assertRaisesRegex(LifecycleError, "revalidation is already in progress"):
                lifecycle.open_or_create(selection)

        self.assertEqual([], backend.calls)

    def test_malformed_and_rollback_responses_do_not_reconcile(self) -> None:
        rollback = fixture("setup-rollback.json")["response"]
        for response in (
            {"schemaVersion": 2, "ok": True},
            "{" + "x" * (256 * 1024) + "}",
            (2, rollback),
        ):
            with self.subTest(response_type=type(response).__name__):
                lifecycle, store, backend, _context = self.harness([row(tmux=False)], [response])
                if isinstance(response, str):
                    backend._responses = [CommandOutput((), 0, response, "")]
                with self.assertRaises(LifecycleError):
                    lifecycle.open_or_create(self.selection())
                self.assertEqual(1, len(backend.calls))
                cached = store.load(self.config.fingerprint, BACKEND)
                assert cached is not None
                self.assertNotIn("tmux", cached["sessions"][0])

    def test_copied_lifecycle_fixtures_exercise_deferred_and_open_shapes(self) -> None:
        """Producer fixtures remain hermetic but drive the consumer validator."""

        deferred = fixture("create-deferred.json")["response"]
        assert isinstance(deferred, dict)
        deferred = copy.deepcopy(deferred)
        deferred["meshRevision"] = REVISION
        deferred_session = deferred["session"]
        assert isinstance(deferred_session, dict)
        deferred_session["hostId"] = "alpha"
        reference = _success_response(
            CommandOutput((), 0, json.dumps(deferred) + "\n", ""),
            "alpha",
            REVISION,
            opening=False,
        )
        self.assertTrue(deferred_session["pending"])
        self.assertEqual("$8", reference.session_id)

        opened = fixture("external-rename.json")["open"]
        assert isinstance(opened, dict)
        opened = copy.deepcopy(opened)
        opened["meshRevision"] = REVISION
        opened_session = opened["session"]
        assert isinstance(opened_session, dict)
        opened_session["hostId"] = "alpha"
        renamed = _success_response(
            CommandOutput((), 0, json.dumps(opened) + "\n", ""),
            "alpha",
            REVISION,
            opening=True,
        )
        self.assertEqual("renamed-outside", renamed.observed_name)

    def test_rendered_rofi_info_revalidates_current_authority_then_opens(self) -> None:
        backend = FakeBackend([row()], [success(descriptor())])
        store = CacheStore(Path(self.temporary.name) / "cache", backend_selector=lambda: backend)
        selected = json.loads(selection_payload(row()))
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            self.assertEqual(
                0,
                run_rofi(
                    {"ROFI_RETV": "1", "ROFI_INFO": json.dumps(selected)},
                    store=store,
                    config=self.config,
                    preference_store=ViewPreferenceStore(Path(self.temporary.name) / "state"),
                ),
            )
        self.assertEqual("create", backend.calls[0][0][1])
        # The Rofi path gives contract lifecycle its 30-second action budget,
        # not the legacy four-second discovery default.
        self.assertGreater(backend.calls[0][1], 10.0)

    def test_explicit_open_timeout_is_honored_and_reconciliation_has_no_extra_wait(self) -> None:
        lifecycle, store, backend, context = self.harness([row()], [success(descriptor())])
        _open_selection(self.selection(), self.config, timeout=0.5, store=store, context=context)
        self.assertLessEqual(backend.calls[0][1], 0.5)

        snapshot = store.load(self.config.fingerprint, BACKEND)
        assert snapshot is not None
        started = time.monotonic()
        self.assertFalse(
            store.reconcile_contract_reference(
                self.config,
                context,
                host_id="alpha",
                kind="codex",
                identifier=THREAD,
                namespace=row()["namespace"],
                native_id_kind="thread",
                reference={
                    "meshRevision": REVISION,
                    "serverGeneration": "tmux-v1:alpha",
                    "sessionId": "$7",
                    "createdAt": 8,
                    "observedName": "late",
                },
                wait_seconds=1.0,
                deadline=started,
            )
        )
        self.assertLess(time.monotonic() - started, 0.1)

    def test_selection_must_match_the_prepared_authority(self) -> None:
        lifecycle, _store, backend, _context = self.harness([row()], [])
        selected = self.selection()
        selected["backend"] = {
            **BACKEND,
            "meshRevision": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        }
        with self.assertRaisesRegex(LifecycleError, "older Mesh revision"):
            lifecycle.open_or_create(selected)
        self.assertEqual([], backend.calls)

    def test_reconciliation_preserves_freshness_and_rejects_changed_authority(self) -> None:
        lifecycle, store, _backend, context = self.harness([row()], [])
        snapshot = store.refresh(self.config, force=True, context=context)
        snapshot["generatedAt"] = 123
        store.write(snapshot)
        ref = {
            "meshRevision": REVISION,
            "serverGeneration": "tmux-v1:alpha",
            "sessionId": "$9",
            "createdAt": 10,
            "observedName": "new-name",
        }
        self.assertTrue(
            store.reconcile_contract_reference(
                self.config,
                context,
                host_id="alpha",
                kind="codex",
                identifier=THREAD,
                namespace=row()["namespace"],
                native_id_kind="thread",
                reference=ref,
            )
        )
        current = store.load(self.config.fingerprint, BACKEND)
        assert current is not None
        self.assertEqual(123, current["generatedAt"])
        changed = PresentationContext(
            self.config.fingerprint,
            {
                **BACKEND,
                "meshRevision": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
            },
            selected=lifecycle.backend,
        )
        self.assertFalse(
            store.reconcile_contract_reference(
                self.config,
                changed,
                host_id="alpha",
                kind="codex",
                identifier=THREAD,
                namespace=row()["namespace"],
                native_id_kind="thread",
                reference=ref,
            )
        )
        current = store.load(self.config.fingerprint, BACKEND)
        assert current is not None
        self.assertEqual("new-name", current["sessions"][0]["tmuxSession"])
