from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import batch, contract_viewers
from rofi_agent_plus.cache import PresentationContext
from rofi_agent_plus.config import PickerConfig
from rofi_agent_plus.rofi import (
    ACTION_CLOSE,
    ACTION_NEW,
    ACTION_ORDER,
    ACTION_RESUME,
    ROFI_RETV_CUSTOM_4,
    ROFI_RETV_CUSTOM_7,
    ROFI_RETV_CUSTOM_19,
    BatchUIState,
    NavigationState,
    _refresh_data,
    parse_continuation_state,
    render_snapshot,
    run_rofi,
    selection_payload,
)
from rofi_agent_plus.view_preferences import ViewPreferenceStore

REVISION = "sha256:" + "a" * 64
LOCAL_ID = "00000000-0000-0000-0000-000000000001"
REMOTE_ID = "00000000-0000-0000-0000-000000000002"
BACKEND_IDENTITY = {
    "kind": "contract",
    "capability": "host-mesh-v1+tmux-session-v1",
    "meshRevision": REVISION,
}
CATALOG = [
    {"hostId": "local", "display": "Workstation", "local": True},
    {"hostId": "remote", "display": "Snap", "local": False},
]


class FakeBackend:
    tmux_command = "rofi-tmux-plus"

    def _run(self, _argv: list[str], **_kwargs: object) -> object:
        raise AssertionError("viewer calls are patched in these tests")


class FakeStore:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        self.context = PresentationContext("fingerprint", BACKEND_IDENTITY, selected=FakeBackend())
        self.refresh_calls: list[dict[str, object]] = []
        self.context_calls = 0

    def presentation_context(self, _config: PickerConfig) -> PresentationContext:
        self.context_calls += 1
        return self.context

    def load_current(self, _config: PickerConfig, _context: object = None) -> dict[str, object]:
        return self.snapshot

    def load(self, _fingerprint: str | None = None) -> dict[str, object]:
        return self.snapshot

    def refresh(self, _config: PickerConfig, **kwargs: object) -> dict[str, object]:
        self.refresh_calls.append(kwargs)
        return self.snapshot

    def is_fresh(self, _snapshot: object, _refresh_seconds: int) -> bool:
        return True

    def background_active(self, **_kwargs: object) -> bool:
        return False


def _reference(host_id: str, session_id: str, created_at: int) -> dict[str, object]:
    return {
        "hostId": host_id,
        "meshRevision": REVISION,
        "serverGeneration": f"server-{host_id}",
        "sessionId": session_id,
        "createdAt": created_at,
        "observedName": f"agent-{host_id}-{created_at}",
    }


def _row(
    host_id: str,
    kind: str,
    identifier: str,
    session_id: str,
    created_at: int,
    **changes: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "contractMode": True,
        "backend": dict(BACKEND_IDENTITY),
        "hostId": host_id,
        "host": "Workstation" if host_id == "local" else "Snap",
        "kind": kind,
        "id": identifier,
        "name": f"{kind}-{host_id}",
        "active": True,
        "activityState": "active",
        "sourceObservation": "current",
        "providerOptionVerified": True,
        "tmux": _reference(host_id, session_id, created_at),
    }
    row.update(changes)
    return row


def _snapshot(
    local_rows: list[dict[str, object]], remote_rows: list[dict[str, object]]
) -> dict[str, object]:
    observation = {
        stage: {"outcome": "ok"} for stage in ("activity", "codex", "claude", "opencode", "tmux")
    }
    return {
        "backend": dict(BACKEND_IDENTITY),
        "hostCatalog": CATALOG,
        # Deliberately model the flattened All list's ordinary cap.
        "sessions": [*local_rows[:1]],
        "hosts": {
            "local": {"sessions": local_rows, "errors": [], "observations": observation},
            "remote": {"sessions": remote_rows, "errors": [], "observations": observation},
        },
        "errors": [],
    }


def _records(output: str) -> tuple[list[str], list[str]]:
    delimiter = "\x00delim\x1f\\t\n"
    if delimiter in output:
        headers, body = output.split(delimiter, 1)
        return headers.splitlines() + [delimiter.rstrip("\n")], body.removesuffix("\t").split("\t")
    values = output.removesuffix("\t").split("\t")
    return [value for value in values if value.startswith("\x00")], [
        value for value in values if value and not value.startswith("\x00")
    ]


def _options(row: str) -> tuple[str, dict[str, str]]:
    visible, separator, encoded = row.partition("\x00")
    if not separator:
        raise AssertionError("row has no Rofi options")
    fields = encoded.split("\x1f")
    return visible, dict(zip(fields[::2], fields[1::2], strict=True))


class BatchPreparationTest(unittest.TestCase):
    def test_inspections_are_bounded_concurrent_and_keep_catalog_order(self) -> None:
        rows = [
            _row("local", "codex", f"{i:08d}-0000-0000-0000-000000000001", f"${i}", i)
            for i in range(1, 9)
        ]
        barrier = threading.Barrier(4)
        lock = threading.Lock()
        active = 0
        maximum = 0

        def inspect(_backend: object, reference: object, **kwargs: object) -> object:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                barrier.wait(timeout=5)
                self.assertEqual(
                    ("@codex_thread_id", rows[reference.created_at - 1]["id"]),
                    kwargs["required_option"],
                )
                return contract_viewers.ViewerInspection("none", (), True)
            finally:
                with lock:
                    active -= 1

        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers", side_effect=inspect
        ):
            preview = batch.build_preview(
                FakeStore(_snapshot(rows, [])), PickerConfig(), batch.Scope("all"), ACTION_RESUME
            )
        self.assertEqual(4, maximum)
        self.assertEqual(
            [row["id"] for row in rows], [target["id"] for target in preview["targets"]]
        )

    def test_protocol_failure_stops_before_next_inspection_chunk(self) -> None:
        rows = [
            _row("local", "codex", f"{i:08d}-0000-0000-0000-000000000001", f"${i}", i)
            for i in range(1, 9)
        ]

        def inspect(_backend: object, reference: object, **_kwargs: object) -> object:
            if reference.session_id == "$1":
                raise contract_viewers.ViewerError(
                    "stale_mesh", "authority changed", stop_batch=True
                )
            return contract_viewers.ViewerInspection("none", (), True)

        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers", side_effect=inspect
        ) as inspect_mock:
            preview = batch.build_preview(
                FakeStore(_snapshot(rows, [])), PickerConfig(), batch.Scope("all"), ACTION_RESUME
            )
        self.assertEqual(4, inspect_mock.call_count)
        self.assertEqual([], preview["targets"])
        self.assertIn("stopReason", preview)

    def test_preparation_cancellation_superseding_and_expiry_prevent_late_publish(self) -> None:
        store = FakeStore(_snapshot([_row("local", "codex", LOCAL_ID, "$1", 1)], []))
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            first = state.start_preparation(ACTION_RESUME, batch.Scope("all"), spawn=False)
            self.assertEqual(
                first, state.start_preparation(ACTION_RESUME, batch.Scope("all"), spawn=False)
            )
            with mock.patch(
                "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                return_value=contract_viewers.ViewerInspection("none", (), True),
            ):
                preview = batch.build_preview(
                    store, PickerConfig(), batch.Scope("all"), ACTION_RESUME
                )
            state.discard_preparation(first)
            self.assertFalse(state.finish_preparation(first, preview))
            second = state.start_preparation(ACTION_RESUME, batch.Scope("all"), spawn=False)
            third = state.start_preparation(ACTION_CLOSE, batch.Scope("local"), spawn=False)
            self.assertNotEqual(second, third)
            self.assertFalse(state.finish_preparation(second, preview))
            state.fail_preparation(second, "old helper failed")
            self.assertEqual("pending", state.read_preparation(third)["status"])
            state.discard_preparation(second)
            self.assertIsNotNone(state.read_preparation(third))
            with mock.patch("rofi_agent_plus.batch.time.time", return_value=time.time() + 121):
                self.assertIsNone(state.read_preparation(third))
                self.assertFalse(state.finish_preparation(third, preview))
            self.assertIsNone(state.current_job())
            self.assertFalse(state.preview_path.exists())

    def test_pending_helper_cannot_replace_a_single_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            request = state.start_preparation(ACTION_CLOSE, batch.Scope("all"), spawn=False)
            preview = {
                "action": ACTION_CLOSE,
                "backend": BACKEND_IDENTITY,
                "scope": "Selected conversation",
                "targets": [],
                "exclusions": [],
            }
            preview_id = state.write_preview(preview)
            self.assertFalse(state.finish_preparation(request, preview))
            self.assertIsNotNone(state.read_preview(preview_id))

    def test_helper_failure_and_invalid_private_records_are_visible_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            request = state.start_preparation(ACTION_RESUME, batch.Scope("all"), spawn=False)
            with mock.patch(
                "rofi_agent_plus.batch.load_config", side_effect=batch.BatchError("bad config")
            ):
                self.assertEqual(1, batch.preparation_main(request, store=state))
            self.assertEqual("failed", state.read_preparation(request)["status"])
            self.assertEqual("bad config", state.read_preparation(request)["error"])
            with state.locked():
                malformed = dict(state._read(state.preparation_path))
                malformed["status"] = []
                state._write(state.preparation_path, malformed)
            self.assertIsNone(state.read_preparation(request))
            self.assertEqual(2, batch.preparation_main("invalid", store=state))
            self.assertIsNone(state.current_job())

    def test_preparation_spawner_removes_rofi_environment_and_reports_spawn_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            with (
                mock.patch.dict(os.environ, {"ROFI_RETV": "1", "ROFI_DATA": "state"}),
                mock.patch("rofi_agent_plus.batch.subprocess.Popen") as popen,
            ):
                request = state.start_preparation(ACTION_RESUME, batch.Scope("host", "remote"))
            self.assertEqual(["_batch-prepare", request], popen.call_args.args[0][-2:])
            self.assertFalse(any(key.startswith("ROFI_") for key in popen.call_args.kwargs["env"]))
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            state.discard_preparation(request)
            with mock.patch(
                "rofi_agent_plus.batch.subprocess.Popen", side_effect=OSError("missing")
            ):
                with self.assertRaises(batch.BatchError):
                    state.start_preparation(ACTION_CLOSE, batch.Scope("all"))
            record = state._read(state.preparation_path)
            self.assertEqual("failed", record["status"])
            self.assertIsNone(state.current_job())

    def test_all_page_uses_uncapped_per_host_rows_and_mixed_owner_hosts(self) -> None:
        local = _row("local", "codex", LOCAL_ID, "$1", 1)
        remote = _row("remote", "claude", REMOTE_ID, "$9", 9)
        store = FakeStore(_snapshot([local], [remote]))

        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
            return_value=contract_viewers.ViewerInspection("none", (), True),
        ) as inspect:
            preview = batch.build_preview(
                store, PickerConfig(), batch.Scope("all"), batch.ACTION_RESUME
            )

        self.assertIsNone(store.refresh_calls[0]["host_ids"])
        self.assertEqual([LOCAL_ID, REMOTE_ID], [target["id"] for target in preview["targets"]])
        self.assertEqual(
            ["$1", "$9"], sorted(call.args[1].session_id for call in inspect.call_args_list)
        )
        self.assertIn("beyond the ordinary list cap", preview["scope"])

    def test_local_scope_and_exclusions_use_current_unambiguous_associations(self) -> None:
        local = _row("local", "codex", LOCAL_ID, "$1", 1)
        duplicate_runtime = _row("remote", "claude", REMOTE_ID, "$1", 1)
        second_catalog_row = _row(
            "remote", "codex", "00000000-0000-0000-0000-000000000006", "$1", 1
        )
        no_ref = _row("remote", "codex", "00000000-0000-0000-0000-000000000003", "$3", 3)
        no_ref.pop("tmux")
        retained = _row("remote", "claude", "00000000-0000-0000-0000-000000000004", "$4", 4)
        retained["sourceObservation"] = "retained"
        ambiguous = _row("remote", "opencode", "ses_0319af718ffegy8N1IoMEggx4B", "$5", 5)
        ambiguous["tmuxAmbiguous"] = True
        store = FakeStore(
            _snapshot([local], [duplicate_runtime, second_catalog_row, no_ref, retained, ambiguous])
        )

        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
            return_value=contract_viewers.ViewerInspection("none", (), True),
        ):
            local_preview = batch.build_preview(
                store, PickerConfig(), batch.Scope("local"), batch.ACTION_CLOSE
            )
            all_preview = batch.build_preview(
                store, PickerConfig(), batch.Scope("all"), batch.ACTION_RESUME
            )

        self.assertEqual(("local",), store.refresh_calls[0]["host_ids"])
        self.assertEqual([LOCAL_ID], [target["id"] for target in local_preview["targets"]])
        self.assertEqual(2, len(all_preview["targets"]))
        self.assertEqual([LOCAL_ID, REMOTE_ID], [target["id"] for target in all_preview["targets"]])
        reasons = {item["reason"] for item in all_preview["exclusions"]}
        self.assertIn("duplicate tmux reference already listed", reasons)
        self.assertIn("duplicate tmux reference already listed", reasons)
        self.assertIn("no tmux association", reasons)
        self.assertIn("stale observation", reasons)
        self.assertIn("ambiguous tmux association", reasons)

    def test_close_excludes_unverified_and_destroy_unsafe_viewers(self) -> None:
        local = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([local], []))
        inspections = iter((contract_viewers.ViewerInspection("verified", (), False),))
        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
            side_effect=lambda *_args, **_kwargs: next(inspections),
        ):
            preview = batch.build_preview(
                store, PickerConfig(), batch.Scope("local"), batch.ACTION_CLOSE
            )
        self.assertEqual([], preview["targets"])
        self.assertIn(
            "closing could destroy the tmux session",
            {item["reason"] for item in preview["exclusions"]},
        )


class BatchWorkerTest(unittest.TestCase):
    def test_state_reader_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            state.ensure_root()
            os.mkfifo(state.preview_path, 0o600)
            self.assertIsNone(state._read(state.preview_path))

    def test_preview_claim_is_one_confirmation_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            preview = {
                "action": batch.ACTION_CLOSE,
                "backend": dict(BACKEND_IDENTITY),
                "scope": "Active · all hosts",
                "targets": [
                    {
                        "hostId": "remote",
                        "kind": "claude",
                        "id": REMOTE_ID,
                        "name": "claude-remote",
                        "host": "Snap",
                        "reference": _reference("remote", "$9", 9),
                        "requiredOption": None,
                        "mode": "close",
                        "viewers": [{"viewerId": "viewer-1", "windowId": 71}],
                    }
                ],
                "exclusions": [],
            }
            preview_id = state.write_preview(preview)
            self.assertIsNotNone(state.read_preview(preview_id))
            self.assertEqual(0o700, stat.S_IMODE(state.root.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(state.preview_path.stat().st_mode))

            job_id, queued = state.consume_and_submit(preview_id, spawn=False)
            self.assertEqual("queued", queued["status"])
            self.assertIsNone(state.read_preview(preview_id))
            with self.assertRaises(batch.BatchBusy):
                state.consume_and_submit(preview_id, spawn=False)
            self.assertEqual(job_id, state.current_job(job_id)["jobId"])

            with mock.patch(
                "rofi_agent_plus.batch._run_job_target",
                return_value=("already", "viewer disappeared", False),
            ):
                self.assertEqual(0, batch.worker_main(job_id, store=state))
            self.assertEqual("complete", state.current_job(job_id)["status"])

    def test_confirmation_omits_already_satisfied_viewers_and_rejects_no_work(self) -> None:
        for action in (batch.ACTION_RESUME, batch.ACTION_CLOSE):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temporary:
                state = batch.BatchStateStore(Path(temporary))
                target = {
                    "hostId": "local",
                    "kind": "codex",
                    "id": LOCAL_ID,
                    "name": "already satisfied",
                    "host": "Workstation",
                    "reference": _reference("local", "$1", 1),
                    "requiredOption": None,
                    "mode": "already_open" if action == batch.ACTION_RESUME else "already_closed",
                    "viewers": (
                        [{"viewerId": "viewer-1", "windowId": 71}]
                        if action == batch.ACTION_RESUME
                        else []
                    ),
                }
                preview = {
                    "action": action,
                    "backend": dict(BACKEND_IDENTITY),
                    "scope": "Local",
                    "targets": [target],
                    "exclusions": [],
                }
                preview_id = state.write_preview(preview)
                with mock.patch("rofi_agent_plus.batch._spawn_worker") as spawn:
                    with self.assertRaises(batch.BatchError):
                        state.consume_and_submit(preview_id)
                    spawn.assert_not_called()
                self.assertIsNone(state.current_job())
                self.assertIsNotNone(state.read_preview(preview_id))

                operation = {
                    **target,
                    "id": REMOTE_ID,
                    "reference": _reference("local", "$2", 2),
                    "mode": "open" if action == batch.ACTION_RESUME else "close",
                    "viewers": (
                        []
                        if action == batch.ACTION_RESUME
                        else [{"viewerId": "viewer-2", "windowId": 72}]
                    ),
                }
                preview_id = state.write_preview({**preview, "targets": [target, operation]})
                job_id, queued = state.consume_and_submit(preview_id, spawn=False)
                self.assertEqual([operation], queued["targets"])
                with mock.patch(
                    "rofi_agent_plus.batch._run_job_target",
                    return_value=("done", "operation completed", False),
                ) as run_target:
                    self.assertEqual(0, batch.worker_main(job_id, store=state))
                run_target.assert_called_once()
                self.assertEqual(operation, run_target.call_args.args[3])

    def test_discard_preview_only_removes_the_matching_current_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            preview = {"action": batch.ACTION_CLOSE, "targets": [], "exclusions": []}
            old_id = state.write_preview(preview)
            new_id = state.write_preview(preview)

            self.assertFalse(state.discard_preview(old_id))
            self.assertEqual(new_id, state._read_preview()["previewId"])
            self.assertTrue(state.discard_preview(new_id))
            self.assertIsNone(state._read_preview())

    def test_submit_rejects_halted_empty_and_stale_previews(self) -> None:
        target: dict[str, object] = {
            "hostId": "remote",
            "kind": "claude",
            "id": REMOTE_ID,
            "name": "claude-remote",
            "host": "Snap",
            "reference": _reference("remote", "$9", 9),
            "requiredOption": ["@claude_session_id", REMOTE_ID],
            "mode": "open",
            "viewers": [],
        }
        common = {
            "action": batch.ACTION_RESUME,
            "backend": dict(BACKEND_IDENTITY),
            "scope": "Active · all hosts",
            "exclusions": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            halted_id = state.write_preview(
                {**common, "targets": [target], "stopReason": "viewer protocol failure"}
            )
            with self.assertRaises(batch.BatchError):
                state.consume_and_submit(halted_id, spawn=False)
            self.assertIsNotNone(state.read_preview(halted_id))

            empty_id = state.write_preview({**common, "targets": []})
            with self.assertRaises(batch.BatchError):
                state.consume_and_submit(empty_id, spawn=False)

            with mock.patch("rofi_agent_plus.batch.time.time", return_value=1_000):
                stale_id = state.write_preview({**common, "targets": [target]})
            with mock.patch(
                "rofi_agent_plus.batch.time.time",
                return_value=1_000 + batch._PREVIEW_SECONDS + 1,
            ):
                with self.assertRaises(batch.BatchError):
                    state.consume_and_submit(stale_id, spawn=False)

    def test_worker_spawn_removes_rofi_callback_environment(self) -> None:
        with mock.patch.dict(os.environ, {"ROFI_RETV": "1", "ROFI_DATA": "targets"}):
            with mock.patch("rofi_agent_plus.batch.subprocess.Popen") as popen:
                batch._spawn_worker("a" * 32)
        environment = popen.call_args.kwargs["env"]
        self.assertFalse(any(key.startswith("ROFI_") for key in environment))
        self.assertEqual(["_batch-worker", "a" * 32], popen.call_args.args[0][-2:])

    def test_resume_revalidates_same_active_reference_and_calls_existing_open_only(self) -> None:
        row = _row("remote", "claude", REMOTE_ID, "$9", 9)
        snapshot = _snapshot([], [row])
        store = FakeStore(snapshot)
        target: dict[str, object] = {
            "hostId": "remote",
            "kind": "claude",
            "id": REMOTE_ID,
            "name": row["name"],
            "host": "Snap",
            "reference": _reference("remote", "$9", 9),
            "requiredOption": ["@claude_session_id", REMOTE_ID],
            # A verified viewer in the preview may close before confirmation;
            # Resume still revalidates and ensures that exact session is open.
            "mode": "already_open",
            "viewers": [{"viewerId": "viewer-1", "windowId": 71}],
        }
        job = {"action": batch.ACTION_RESUME, "backend": dict(BACKEND_IDENTITY)}

        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.open_existing_viewer", return_value=True
        ) as open_existing:
            result, reason, stop = batch._run_job_target(store, PickerConfig(), job, target)

        self.assertEqual(
            ("done", "viewer launched for the existing tmux session", False), (result, reason, stop)
        )
        self.assertEqual(("remote",), store.refresh_calls[0]["host_ids"])
        open_existing.assert_called_once()
        self.assertEqual(
            ("@claude_session_id", REMOTE_ID), open_existing.call_args.kwargs["required_option"]
        )

    def test_worker_main_dispatches_nonempty_target_through_cache_store(self) -> None:
        row = _row("remote", "claude", REMOTE_ID, "$9", 9)
        cache_store = FakeStore(_snapshot([], [row]))
        target: dict[str, object] = {
            "hostId": "remote",
            "kind": "claude",
            "id": REMOTE_ID,
            "name": row["name"],
            "host": "Snap",
            "reference": _reference("remote", "$9", 9),
            "requiredOption": ["@claude_session_id", REMOTE_ID],
            "mode": "open",
            "viewers": [],
        }
        preview = {
            "action": batch.ACTION_RESUME,
            "backend": dict(BACKEND_IDENTITY),
            "scope": "Active · all hosts",
            "targets": [target],
            "exclusions": [],
        }

        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            preview_id = state.write_preview(preview)
            job_id, _queued = state.consume_and_submit(preview_id, spawn=False)
            with mock.patch(
                "rofi_agent_plus.batch.contract_viewers.open_existing_viewer", return_value=True
            ) as open_existing:
                result = batch.worker_main(job_id, store=state, cache_store=cache_store)

            self.assertEqual(0, result)
            completed = state.current_job(job_id)

        self.assertEqual("complete", completed["status"])
        self.assertEqual({"done": 1, "already": 0, "skipped": 0, "failed": 0}, completed["counts"])
        self.assertEqual(("remote",), cache_store.refresh_calls[0]["host_ids"])
        open_existing.assert_called_once()

    def test_replaced_resume_target_is_skipped_without_open(self) -> None:
        current = _row("remote", "claude", REMOTE_ID, "$10", 10)
        store = FakeStore(_snapshot([], [current]))
        target: dict[str, object] = {
            "hostId": "remote",
            "kind": "claude",
            "id": REMOTE_ID,
            "name": "old name",
            "host": "Snap",
            "reference": _reference("remote", "$9", 9),
            "requiredOption": ["@claude_session_id", REMOTE_ID],
            "mode": "open",
            "viewers": [],
        }
        job = {"action": batch.ACTION_RESUME, "backend": dict(BACKEND_IDENTITY)}

        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.open_existing_viewer"
        ) as open_existing:
            result, reason, stop = batch._run_job_target(store, PickerConfig(), job, target)

        self.assertEqual("skipped", result)
        self.assertIn("replaced", reason)
        self.assertFalse(stop)
        open_existing.assert_not_called()


class BatchPickerTest(unittest.TestCase):
    def _invoke(
        self,
        environ: dict[str, str],
        store: FakeStore,
        *,
        preferences: ViewPreferenceStore | None = None,
        state_store: batch.BatchStateStore | None = None,
    ) -> str:
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                environ,
                store=store,
                config=PickerConfig(),
                preference_store=preferences,
                batch_state_store=state_store,
            )
        return output.getvalue()

    @staticmethod
    def _data(output: str) -> str:
        headers, _ = _records(output)
        return next(
            value.split("\x1f", 1)[1] for value in headers if value.startswith("\x00data\x1f")
        )

    @staticmethod
    def _message(output: str) -> str:
        headers, _ = _records(output)
        return next(value for value in headers if value.startswith("\x00message\x1f"))

    def _stored_preview(
        self,
        store: FakeStore,
        state_store: batch.BatchStateStore,
        action: str = ACTION_RESUME,
    ) -> tuple[BatchUIState, str, dict[str, object]]:
        preview = batch.build_preview(store, PickerConfig(), batch.Scope("all"), action)
        preview_id = state_store.write_preview(preview)
        state = BatchUIState("preview", None, action, preview_id)
        return (
            state,
            _refresh_data(action=action, batch_state=state),
            dict(state_store.read_preview(preview_id) or {}),
        )

    def test_preparing_action_page_and_conversation_exits_cancel_even_after_helper_finishes(
        self,
    ) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        for ready in (False, True):
            for retv, info in (
                ("16", '{"type":"batch-target"}'),
                ("11", '{"type":"batch-target"}'),
                ("13", '{"type":"batch-target"}'),
                ("1", selection_payload(row)),
            ):
                with (
                    self.subTest(ready=ready, retv=retv),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    store = FakeStore(_snapshot([row], []))
                    state_store = batch.BatchStateStore(Path(temporary))
                    request = state_store.start_preparation(
                        ACTION_RESUME, batch.Scope("all"), spawn=False
                    )
                    data = _refresh_data(
                        action=ACTION_RESUME,
                        batch_state=BatchUIState("preparing", None, ACTION_RESUME, request),
                    )
                    if ready:
                        with mock.patch(
                            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                            return_value=contract_viewers.ViewerInspection("none", (), True),
                        ):
                            preview = batch.build_preview(
                                store, PickerConfig(), batch.Scope("all"), ACTION_RESUME
                            )
                        state_store.finish_preparation(request, preview)
                    with mock.patch("rofi_agent_plus.rofi._open_selection") as open_session:
                        output = self._invoke(
                            {"ROFI_RETV": retv, "ROFI_DATA": data, "ROFI_INFO": info},
                            store,
                            preferences=mock.Mock(spec=ViewPreferenceStore),
                            state_store=state_store,
                        )
                    self.assertIsNone(parse_continuation_state(self._data(output)).batch_state)
                    self.assertIsNone(state_store.read_preparation(request))
                    self.assertFalse(state_store.preview_path.exists())
                    self.assertIsNone(state_store.current_job())
                    open_session.assert_not_called()

    def test_preparation_failure_or_expiry_returns_to_list_without_submission(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        with tempfile.TemporaryDirectory() as temporary:
            state_store = batch.BatchStateStore(Path(temporary))
            request = state_store.start_preparation(ACTION_RESUME, batch.Scope("all"), spawn=False)
            data = _refresh_data(
                action=ACTION_RESUME,
                batch_state=BatchUIState("preparing", None, ACTION_RESUME, request),
            )
            state_store.fail_preparation(request, "fixture preparation failed")
            output = self._invoke(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": data},
                FakeStore(_snapshot([row], [])),
                preferences=mock.Mock(spec=ViewPreferenceStore),
                state_store=state_store,
            )
            self.assertIn("fixture preparation failed", output)
            self.assertIn(LOCAL_ID, output)
            self.assertIsNone(parse_continuation_state(self._data(output)).batch_state)
            self.assertIsNone(state_store.current_job())

    def test_leading_row_action_cycle_and_conversation_offset(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        headers, rows = _records(
            render_snapshot(
                _snapshot([row], []),
                selected_identity=("local", "codex", LOCAL_ID),
                initial_open=True,
            )
        )
        _, control = _options(rows[0])
        _, conversation = _options(rows[1])
        self.assertEqual({"type": "batch"}, json.loads(control["info"]))
        self.assertEqual("all active sessions", control["meta"])
        self.assertEqual(LOCAL_ID, json.loads(conversation["info"])["id"])
        self.assertIn("\x00new-selection\x1f1", headers)
        self.assertIn("\x00no-custom\x1ffalse", headers)
        self.assertIn("[Resume]</span> · Close · New", "\t".join(headers))
        self.assertEqual((ACTION_RESUME, ACTION_CLOSE, ACTION_NEW), ACTION_ORDER)

    def test_alt_a_clears_search_selects_control_and_preserves_action_cache_only(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([row], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        output = self._invoke(
            {
                "ROFI_RETV": str(ROFI_RETV_CUSTOM_4),
                "ROFI_DATA": _refresh_data(action=ACTION_CLOSE),
                "ROFI_INFO": selection_payload(row),
            },
            store,
            preferences=preferences,
        )
        headers, rows = _records(output)
        _, control = _options(rows[0])
        self.assertEqual("batch", json.loads(control["info"])["type"])
        self.assertIn("\x00new-selection\x1f0", headers)
        self.assertNotIn("\x00keep-filter\x1ftrue", headers)
        self.assertIn("[Close]</span>", output)
        self.assertNotIn("batch-ui:", output)
        self.assertEqual(0, store.context_calls)
        self.assertEqual([], store.refresh_calls)
        preferences.save.assert_not_called()

    def test_shared_cycle_is_cache_only_and_preserves_root_or_conversation_selection(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([row], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        close_control = self._invoke(
            {
                "ROFI_RETV": str(ROFI_RETV_CUSTOM_7),
                "ROFI_DATA": _refresh_data(action=ACTION_RESUME),
                "ROFI_INFO": '{"type":"batch"}',
            },
            store,
            preferences=preferences,
        )
        headers, _ = _records(close_control)
        close_data = self._data(close_control)
        self.assertEqual(ACTION_CLOSE, parse_continuation_state(close_data).action)
        self.assertIn("[Close]</span>", close_control)
        self.assertIn("\x00new-selection\x1f0", headers)
        self.assertIn("\x00keep-filter\x1ftrue", headers)

        new_conversation = self._invoke(
            {
                "ROFI_RETV": str(ROFI_RETV_CUSTOM_7),
                "ROFI_DATA": close_data,
                "ROFI_INFO": selection_payload(row),
            },
            store,
            preferences=preferences,
        )
        new_headers, _ = _records(new_conversation)
        new_data = self._data(new_conversation)
        self.assertEqual(ACTION_NEW, parse_continuation_state(new_data).action)
        self.assertIn("[New]</span>", new_conversation)
        self.assertIn("\x00new-selection\x1f1", new_headers)
        self.assertEqual(0, store.context_calls)
        self.assertEqual([], store.refresh_calls)
        preferences.save.assert_not_called()

    def test_all_active_new_warns_without_queries_and_timeout_keeps_control_selected(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([row], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        with tempfile.TemporaryDirectory() as temporary:
            state_store = batch.BatchStateStore(Path(temporary))
            with (
                mock.patch("rofi_agent_plus.rofi._new_session_selection") as create,
                mock.patch("rofi_agent_plus.rofi._open_selection") as open_session,
            ):
                warning = self._invoke(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_DATA": _refresh_data(action=ACTION_NEW),
                        "ROFI_INFO": '{"type":"batch"}',
                    },
                    store,
                    preferences=preferences,
                    state_store=state_store,
                )
            self.assertIn("Select a conversation to create a new session.", warning)
            self.assertIn("[New]</span>", warning)
            self.assertIn("\x00new-selection\x1f0", warning)
            self.assertNotIn("batch-ui:", warning)
            self.assertEqual(0, store.context_calls)
            self.assertEqual([], store.refresh_calls)
            create.assert_not_called()
            open_session.assert_not_called()
            self.assertIsNone(state_store.read_preview("a" * 32))

            expired = _refresh_data(
                error_deadline=time.time() - 1,
                error_message="Select a conversation to create a new session.",
                action=ACTION_NEW,
            )
            refreshed = self._invoke(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                    "ROFI_DATA": expired,
                    "ROFI_INFO": '{"type":"batch"}',
                },
                store,
                preferences=preferences,
                state_store=state_store,
            )
            refreshed_headers, refreshed_rows = _records(refreshed)
            _, control = _options(refreshed_rows[0])
            self.assertEqual("batch", json.loads(control["info"])["type"])
            self.assertIn("\x00new-selection\x1f0", refreshed_headers)
            self.assertIn("[New]</span>", refreshed)
            self.assertEqual([], store.refresh_calls)
            preferences.save.assert_not_called()

    def test_all_active_prepares_asynchronously_then_requires_explicit_confirm(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        for action in (ACTION_RESUME, ACTION_CLOSE):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temporary:
                store = FakeStore(_snapshot([row], []))
                preferences = mock.Mock(spec=ViewPreferenceStore)
                state_store = batch.BatchStateStore(Path(temporary))
                inspection = (
                    contract_viewers.ViewerInspection(
                        "verified", (contract_viewers.Viewer("viewer-1", 3),), True
                    )
                    if action == ACTION_CLOSE
                    else contract_viewers.ViewerInspection("none", (), True)
                )
                with (
                    mock.patch("rofi_agent_plus.batch._spawn_helper") as spawn,
                    mock.patch("rofi_agent_plus.batch.contract_viewers.inspect_viewers") as inspect,
                ):
                    output = self._invoke(
                        {
                            "ROFI_RETV": "1",
                            "ROFI_DATA": _refresh_data(action=action),
                            "ROFI_INFO": '{"type":"batch"}',
                        },
                        store,
                        preferences=preferences,
                        state_store=state_store,
                    )
                pending_data = self._data(output)
                state = parse_continuation_state(pending_data).batch_state
                self.assertEqual("preparing", state.screen)
                self.assertIsNone(state.source_identity)
                action_bar = self._message(render_snapshot(store.snapshot, action=action))
                self.assertEqual(action_bar, self._message(output))
                self.assertIn("All active · Preparing…", output)
                self.assertIn('action: "kb-custom-19"', output)
                self.assertIn(LOCAL_ID, output)
                self.assertIn("\x00new-selection\x1f0", output)
                self.assertNotIn("\x00keep-filter\x1ftrue", output)
                self.assertEqual([], store.refresh_calls)
                self.assertEqual(0, store.context_calls)
                inspect.assert_not_called()
                spawn.assert_called_once_with("_batch-prepare", state.record_id)
                _, pending_rows = _records(output)
                _, pending_control = _options(pending_rows[0])
                early_enter = self._invoke(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_DATA": pending_data,
                        "ROFI_INFO": pending_control["info"],
                    },
                    store,
                    preferences=preferences,
                    state_store=state_store,
                )
                self.assertIn("All active · Preparing…", early_enter)
                self.assertEqual(action_bar, self._message(early_enter))
                self.assertIsNone(state_store.current_job())

                with (
                    mock.patch("rofi_agent_plus.batch.load_config", return_value=PickerConfig()),
                    mock.patch(
                        "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                        return_value=inspection,
                    ) as inspect,
                ):
                    self.assertEqual(
                        0,
                        batch.preparation_main(
                            state.record_id, store=state_store, cache_store=store
                        ),
                    )
                ready = self._invoke(
                    {
                        "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                        "ROFI_DATA": pending_data,
                        "ROFI_INFO": pending_control["info"],
                    },
                    store,
                    preferences=preferences,
                    state_store=state_store,
                )
                preview_data = self._data(ready)
                preview_state = parse_continuation_state(preview_data).batch_state
                self.assertEqual("preview", preview_state.screen)
                record = state_store.read_preview(preview_state.record_id)
                self.assertEqual(action, record["action"])
                self.assertEqual(1, len(record["targets"]))
                expected_mode = "close" if action == ACTION_CLOSE else "open"
                self.assertEqual(expected_mode, record["targets"][0]["mode"])
                self.assertIn(f"All active · Confirm {action.title()} (1)", ready)
                self.assertEqual(action_bar, self._message(ready))
                self.assertEqual(1, len(store.refresh_calls))
                inspect.assert_called_once()
                preferences.save.assert_not_called()
                self.assertIsNone(state_store.current_job())
                _, ready_rows = _records(ready)
                _, confirm = _options(ready_rows[0])
                with mock.patch("rofi_agent_plus.batch._spawn_worker") as worker:
                    queued = self._invoke(
                        {"ROFI_RETV": "1", "ROFI_DATA": preview_data, "ROFI_INFO": confirm["info"]},
                        store,
                        preferences=preferences,
                        state_store=state_store,
                    )
                self.assertEqual("queued", state_store.current_job()["status"])
                self.assertEqual(action_bar, self._message(queued))
                worker.assert_called_once()

    def test_preview_counts_and_colors_only_work_and_rejects_noop_confirmation(self) -> None:
        already = _row("local", "codex", LOCAL_ID, "$1", 1, name="already satisfied")
        work = _row("local", "claude", REMOTE_ID, "$2", 2, name="needs operation")
        verified = contract_viewers.ViewerInspection(
            "verified", (contract_viewers.Viewer("viewer-1", 71),), True
        )
        absent = contract_viewers.ViewerInspection("none", (), True)
        for action in (ACTION_RESUME, ACTION_CLOSE):
            for mixed in (False, True):
                with (
                    self.subTest(action=action, mixed=mixed),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    store = FakeStore(_snapshot([already, work] if mixed else [already], []))
                    state_store = batch.BatchStateStore(Path(temporary))

                    def inspect(
                        _backend: object,
                        reference: object,
                        resume: bool = action == ACTION_RESUME,
                        **_kwargs: object,
                    ) -> object:
                        is_already = reference.session_id == "$1"
                        return verified if is_already == resume else absent

                    with mock.patch(
                        "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                        side_effect=inspect,
                    ):
                        state, data, record = self._stored_preview(store, state_store, action)
                    output = self._invoke(
                        {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": data},
                        store,
                        state_store=state_store,
                    )
                    _, rows = _records(output)
                    _, control = _options(rows[0])
                    conversation_options = {
                        info["id"]: options
                        for row in rows
                        if (options := _options(row)[1]).get("info", "").startswith("{")
                        and (info := json.loads(options["info"])).get("id")
                    }
                    self.assertNotIn("foreground=", conversation_options[LOCAL_ID]["display"])
                    self.assertEqual(
                        self._message(render_snapshot(store.snapshot, action=action)),
                        self._message(output),
                    )
                    if mixed:
                        self.assertIn(f"All active · Confirm {action.title()} (1)", output)
                        self.assertIn("foreground=", conversation_options[REMOTE_ID]["display"])
                    else:
                        verb = "open" if action == ACTION_RESUME else "close"
                        self.assertIn(f"All active · No windows to {verb}", output)
                        self.assertNotEqual("batch-confirm", json.loads(control["info"])["type"])
                        self.assertEqual("true", control["nonselectable"])
                    # A stale/forged Confirm must obey the same operation filter.
                    confirm = json.dumps(
                        {"type": "batch-confirm", "action": action, "recordId": state.record_id}
                    )
                    with mock.patch("rofi_agent_plus.batch._spawn_worker") as spawn:
                        self._invoke(
                            {"ROFI_RETV": "1", "ROFI_DATA": data, "ROFI_INFO": confirm},
                            store,
                            state_store=state_store,
                        )
                    if mixed:
                        spawn.assert_called_once()
                        self.assertEqual(
                            [record["targets"][1]], state_store.current_job()["targets"]
                        )
                    else:
                        spawn.assert_not_called()
                        self.assertIsNone(state_store.current_job())

    def test_single_idle_close_freezes_only_exact_viewer_and_confirms_once(self) -> None:
        selected = _row("local", "codex", LOCAL_ID, "$1", 1, active=False, activityState="idle")
        other = _row(
            "local",
            "claude",
            "00000000-0000-0000-0000-000000000003",
            "$3",
            3,
        )
        store = FakeStore(_snapshot([selected, other], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        viewer = contract_viewers.Viewer("viewer-1", 7)
        with tempfile.TemporaryDirectory() as temporary:
            state_store = batch.BatchStateStore(Path(temporary))
            with (
                mock.patch(
                    "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                    return_value=contract_viewers.ViewerInspection("verified", (viewer,), True),
                ) as inspect,
                mock.patch("rofi_agent_plus.rofi._try_fast_open") as fast_open,
                mock.patch("rofi_agent_plus.rofi._open_selection") as open_session,
                mock.patch("rofi_agent_plus.rofi._new_session_selection") as create,
            ):
                preview_output = self._invoke(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_DATA": _refresh_data(action=ACTION_CLOSE),
                        "ROFI_INFO": selection_payload(selected),
                    },
                    store,
                    preferences=preferences,
                    state_store=state_store,
                )
                state = parse_continuation_state(self._data(preview_output)).batch_state
                preview = state_store.read_preview(state.record_id)
                self.assertEqual(ACTION_CLOSE, state.action)
                self.assertEqual(("local", "codex", LOCAL_ID), state.source_identity)
                self.assertEqual(1, len(preview["targets"]))
                target = preview["targets"][0]
                self.assertEqual(LOCAL_ID, target["id"])
                self.assertEqual("$1", target["reference"]["sessionId"])
                self.assertEqual("close", target["mode"])
                self.assertEqual([{"viewerId": "viewer-1", "windowId": 7}], target["viewers"])
                self.assertTrue(preview["scope"].startswith("Selected conversation ·"))
                self.assertIn("Selected · Confirm Close (1)", preview_output)
                inspect.assert_called_once()
                inspected_reference = inspect.call_args.args[1]
                self.assertEqual("$1", inspected_reference.session_id)
                self.assertEqual(
                    ("@codex_thread_id", LOCAL_ID),
                    inspect.call_args.kwargs["required_option"],
                )
                self.assertEqual([], store.refresh_calls)

                confirm = _options(_records(preview_output)[1][0])[1]["info"]
                with mock.patch("rofi_agent_plus.batch._spawn_worker") as spawn:
                    job_output = self._invoke(
                        {
                            "ROFI_RETV": "1",
                            "ROFI_DATA": self._data(preview_output),
                            "ROFI_INFO": confirm,
                        },
                        store,
                        preferences=preferences,
                        state_store=state_store,
                    )
                    spawn.assert_called_once()
                    job = state_store.current_job()
                    self.assertEqual(ACTION_CLOSE, job["action"])
                    self.assertEqual(1, len(job["targets"]))
                    self.assertIn("Selected · Queued", job_output)

                    replay = self._invoke(
                        {
                            "ROFI_RETV": "1",
                            "ROFI_DATA": self._data(preview_output),
                            "ROFI_INFO": confirm,
                        },
                        store,
                        preferences=preferences,
                        state_store=state_store,
                    )
                    spawn.assert_called_once()
                    self.assertIn("Selected · Queued", replay)
                fast_open.assert_not_called()
                open_session.assert_not_called()
                create.assert_not_called()
                preferences.save.assert_not_called()

    def test_single_close_excludes_stale_optionless_and_ended_exact_reference(self) -> None:
        selected = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([selected], []))
        raw_selection = json.loads(selection_payload(selected))
        for label, changes in (
            ("stale", {"tmuxAssociationCurrent": False}),
            ("optionless", {"providerOptionVerified": False}),
            ("missing reference", {"tmux": None}),
        ):
            with self.subTest(label=label):
                selection = {**raw_selection, **changes}
                with mock.patch(
                    "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                    return_value=contract_viewers.ViewerInspection("none", (), True),
                ) as inspect:
                    preview = batch.build_single_close_preview(store, PickerConfig(), selection)
                self.assertEqual([], preview["targets"])
                self.assertTrue(preview["exclusions"])
                self.assertFalse(inspect.called)

        replaced = _row("local", "codex", LOCAL_ID, "$99", 99)
        replaced_store = FakeStore(_snapshot([replaced], []))
        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
            return_value=contract_viewers.ViewerInspection("none", (), True),
        ) as inspect:
            ended = batch.build_single_close_preview(replaced_store, PickerConfig(), raw_selection)
        self.assertEqual([], ended["targets"])
        self.assertEqual("$1", inspect.call_args.args[1].session_id)
        self.assertEqual([], replaced_store.refresh_calls)

    def test_preview_action_cycle_discards_fixed_targets_and_replay_cannot_confirm(self) -> None:
        target = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([target], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        with tempfile.TemporaryDirectory() as temporary:
            state_store = batch.BatchStateStore(Path(temporary))
            with mock.patch(
                "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                return_value=contract_viewers.ViewerInspection("none", (), True),
            ):
                state, data, preview = self._stored_preview(store, state_store)
                start = render_snapshot(
                    store.snapshot,
                    action=ACTION_RESUME,
                    batch_state=state,
                    batch_record=preview,
                )
            confirm = _options(_records(start)[1][0])[1]["info"]
            cycled = self._invoke(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_7),
                    "ROFI_DATA": data,
                    "ROFI_INFO": confirm,
                },
                store,
                preferences=preferences,
                state_store=state_store,
            )
            headers, rows = _records(cycled)
            _, control = _options(rows[0])
            self.assertEqual("batch", json.loads(control["info"])["type"])
            self.assertIn("\x00new-selection\x1f0", headers)
            self.assertIn("[Close]</span>", cycled)
            self.assertNotIn("batch-ui:", cycled)
            self.assertIsNone(state_store.read_preview(state.record_id))
            self.assertEqual(1, len(store.refresh_calls))

            with mock.patch("rofi_agent_plus.batch._spawn_worker") as spawn:
                replay = self._invoke(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_DATA": data,
                        "ROFI_INFO": confirm,
                    },
                    store,
                    preferences=preferences,
                    state_store=state_store,
                )
                spawn.assert_not_called()
            self.assertIn("Preview expired", replay)
            self.assertIsNone(state_store.active_job())
            preferences.save.assert_not_called()

    def test_page_and_conversation_exit_discard_preview_without_provider_action(self) -> None:
        selected = _row("local", "codex", LOCAL_ID, "$1", 1)
        remote = _row("remote", "claude", REMOTE_ID, "$9", 9)
        store = FakeStore(_snapshot([selected], [remote]))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        with tempfile.TemporaryDirectory() as temporary:
            state_store = batch.BatchStateStore(Path(temporary))
            with mock.patch(
                "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                return_value=contract_viewers.ViewerInspection("none", (), True),
            ):
                state, data, _record = self._stored_preview(store, state_store)
            page = self._invoke(
                {"ROFI_RETV": "11", "ROFI_DATA": data},
                store,
                preferences=preferences,
                state_store=state_store,
            )
            self.assertIsNone(state_store.read_preview(state.record_id))
            self.assertIn("Agents › Local", page)
            preferences.save.assert_called_once()
            preferences.reset_mock()

            with mock.patch(
                "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
                return_value=contract_viewers.ViewerInspection("none", (), True),
            ):
                state, data, _record = self._stored_preview(store, state_store)
            with (
                mock.patch("rofi_agent_plus.rofi._try_fast_open") as fast_open,
                mock.patch("rofi_agent_plus.rofi._open_selection") as open_session,
            ):
                returned = self._invoke(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_DATA": data,
                        "ROFI_INFO": selection_payload(selected),
                    },
                    store,
                    preferences=preferences,
                    state_store=state_store,
                )
            self.assertIsNone(state_store.read_preview(state.record_id))
            self.assertIn("\x00new-selection\x1f1", returned)
            self.assertNotIn("batch-ui:", returned)
            fast_open.assert_not_called()
            open_session.assert_not_called()
            preferences.save.assert_not_called()

    def test_changed_reference_is_listed_without_exact_target_tint(self) -> None:
        current = _row("local", "codex", LOCAL_ID, "$99", 99)
        frozen = {
            "hostId": "local",
            "kind": "codex",
            "id": LOCAL_ID,
            "name": "codex-local",
            "host": "Workstation",
            "reference": _reference("local", "$1", 1),
            "requiredOption": ["@codex_session_id", LOCAL_ID],
            "mode": "open",
            "viewers": [],
        }
        preview = {
            "action": batch.ACTION_RESUME,
            "previewId": "a" * 32,
            "scope": "All · all authoritative session-owner hosts",
            "targets": [frozen],
            "exclusions": [],
        }
        rendered = render_snapshot(
            _snapshot([current], []),
            batch_state=BatchUIState("preview", None, batch.ACTION_RESUME, "a" * 32),
            batch_record=preview,
            continuation=True,
        )
        self.assertIn("current tmux association differs; no row was marked", rendered)
        self.assertIn("tmux $1", rendered)
        headers, rows = _records(rendered)
        session_row = next(row for row in rows if row.startswith("codex-local"))
        _, options = _options(session_row)
        self.assertNotIn("Will open existing session", options["display"])
        self.assertNotIn("foreground=", options["display"])

    def test_inline_preview_marks_only_exact_targets_and_shows_exclusions(self) -> None:
        included = _row("local", "codex", LOCAL_ID, "$1", 1, name="included")
        excluded = _row("local", "claude", REMOTE_ID, "$2", 2, name="excluded")
        target = {
            "hostId": "local",
            "kind": "codex",
            "id": LOCAL_ID,
            "name": "included",
            "host": "Workstation",
            "reference": included["tmux"],
            "requiredOption": ["@codex_session_id", LOCAL_ID],
            "mode": "open",
            "viewers": [],
        }
        preview = {
            "action": batch.ACTION_RESUME,
            "previewId": "b" * 32,
            "scope": "Local · current machine as session owner",
            "targets": [target],
            "exclusions": [
                {
                    "name": "excluded",
                    "provider": "claude",
                    "host": "Workstation",
                    "reason": "viewer association is ambiguous",
                }
            ],
        }

        rendered = render_snapshot(
            _snapshot([included, excluded], []),
            navigation=NavigationState("local"),
            batch_state=BatchUIState("preview", None, batch.ACTION_RESUME, "b" * 32),
            batch_record=preview,
            continuation=True,
        )
        _, rows = _records(rendered)
        by_id = {
            json.loads(_options(row)[1]["info"]).get("id"): _options(row)[1]
            for row in rows
            if _options(row)[1].get("info", "").startswith("{")
            and json.loads(_options(row)[1]["info"]).get("type") != "batch"
        }
        self.assertIn(
            '<span foreground="#42a5f5">included</span>',
            by_id[LOCAL_ID]["display"],
        )
        self.assertNotIn("foreground=", by_id[REMOTE_ID]["display"])
        exclusion = next(row for row in rows if row.startswith("Excluded · excluded"))
        self.assertIn("viewer association is ambiguous", _options(exclusion)[1]["display"])

    def test_legacy_group_state_and_forged_controls_fall_back_without_open(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([row], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        legacy = "batch-ui:%7B%22version%22%3A1%2C%22screen%22%3A%22group%22%2C%22action%22%3A%22close%22%7D"
        self.assertIsNone(parse_continuation_state(legacy).batch_state)
        forged = {
            "type": "batch-confirm",
            "kind": "codex",
            "id": LOCAL_ID,
            "contractMode": True,
            "hostId": "local",
            "backend": dict(BACKEND_IDENTITY),
            "tmux": _reference("local", "$1", 1),
            "providerOptionVerified": True,
        }
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("rofi_agent_plus.rofi._try_fast_open") as fast_open:
                output = self._invoke(
                    {"ROFI_RETV": "1", "ROFI_INFO": json.dumps(forged)},
                    store,
                    preferences=preferences,
                    state_store=batch.BatchStateStore(Path(temporary)),
                )
        fast_open.assert_not_called()
        self.assertIn("control rows cannot be opened", output)
        preferences.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
