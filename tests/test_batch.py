from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import batch, contract_viewers
from rofi_agent_plus.cache import CacheStore, PresentationContext
from rofi_agent_plus.config import PickerConfig
from rofi_agent_plus.rofi import (
    ROFI_RETV_CUSTOM_4,
    ROFI_RETV_CUSTOM_7,
    ROFI_RETV_CUSTOM_8,
    BatchUIState,
    ContinuationState,
    NavigationState,
    _batch_row_info,
    _refresh_data,
    _root_after_batch,
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
            ["$1", "$9"], [inspect.call_args_list[i].args[1].session_id for i in range(2)]
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
                        "mode": "already_closed",
                        "viewers": [],
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

            self.assertEqual(0, batch.worker_main(job_id, store=state))
            self.assertEqual("complete", state.current_job(job_id)["status"])

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
    def test_return_to_individual_context_uses_conversation_offset(self) -> None:
        for rows, expected in (([], 0), ([_row("local", "codex", LOCAL_ID, "$1", 1)], 1)):
            with self.subTest(expected=expected):
                cache = mock.Mock(spec=CacheStore)
                cache.load.return_value = {"sessions": rows, "errors": []}
                rendered = _root_after_batch(
                    cache,
                    PickerConfig(),
                    NavigationState(),
                    ContinuationState(),
                    "resume",
                    None,
                    None,
                )
                headers, records = _records(rendered)
                self.assertIn(f"\x00new-selection\x1f{expected}", headers)
                self.assertEqual("batch", json.loads(_options(records[0])[1]["info"])["type"])

    def test_leading_row_is_typed_and_conversation_focus_includes_offset(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        snapshot = _snapshot([row], [])
        rendered = render_snapshot(
            snapshot,
            selected_identity=("local", "codex", LOCAL_ID),
            initial_open=True,
        )
        headers, rows = _records(rendered)
        _, batch_options = _options(rows[0])
        _, session_options = _options(rows[1])
        self.assertEqual({"type": "batch"}, json.loads(batch_options["info"]))
        self.assertEqual("all active sessions", batch_options["meta"])
        self.assertTrue(rows[0].startswith("All active sessions (1)"))
        self.assertIn("All active sessions (1)", batch_options["display"])
        self.assertEqual(LOCAL_ID, json.loads(session_options["info"])["id"])
        self.assertIn("\x00new-selection\x1f1", headers)
        self.assertIn("\x00no-custom\x1ffalse", headers)

    def test_empty_page_selects_group_and_alt_a_enters_without_preferences_or_queries(self) -> None:
        empty = {"sessions": [], "errors": []}
        cache = mock.Mock(spec=CacheStore)
        cache.load.return_value = empty
        preferences = mock.Mock(spec=ViewPreferenceStore)
        with tempfile.TemporaryDirectory() as temporary:
            state = batch.BatchStateStore(Path(temporary))
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                run_rofi(
                    {"ROFI_RETV": str(ROFI_RETV_CUSTOM_4)},
                    store=cache,
                    config=PickerConfig(),
                    preference_store=preferences,
                    batch_state_store=state,
                )
            self.assertIn("All active sessions (0) · Resume all", output.getvalue())
            self.assertIn(
                'Enter: <span foreground="#42a5f5" weight="bold">[Resume all]</span>'
                " · Close all windows  │  Tab: Cycle actions",
                output.getvalue(),
            )
            self.assertIn("All active sessions · Scope:", output.getvalue())
            self.assertIn("All active sessions (0)", output.getvalue())
            self.assertIn("batch-ui:", output.getvalue())
            self.assertIn("\x00new-selection\x1f0", output.getvalue())
            preferences.save.assert_not_called()
            self.assertFalse(cache.presentation_context.called)

        headers, rows = _records(render_snapshot(empty, initial_open=True))
        _, options = _options(rows[0])
        self.assertEqual("batch", json.loads(options["info"])["type"])
        self.assertIn("\x00new-selection\x1f0", headers)

    def test_group_context_shows_uncapped_scoped_active_rows_and_deduplicates(self) -> None:
        local1 = _row("local", "codex", LOCAL_ID, "$1", 1)
        local2 = _row(
            "local",
            "claude",
            "00000000-0000-0000-0000-000000000003",
            "$3",
            3,
            active=False,
            activityState="waiting",
        )
        remote = _row("remote", "claude", REMOTE_ID, "$9", 9)
        snapshot = _snapshot([local1, local2], [remote])
        state = BatchUIState("group", None, batch.ACTION_RESUME)

        all_output = render_snapshot(snapshot, batch_state=state, continuation=True)
        _, all_rows = _records(all_output)
        self.assertIn("All active sessions (3)", all_rows[0])
        self.assertEqual(4, len(all_rows))  # control plus all three uncapped rows
        self.assertIn("Active set in scope · observed", all_output)
        self.assertIn("waiting", all_output)

        local_output = render_snapshot(
            snapshot,
            navigation=NavigationState("local"),
            batch_state=state,
            continuation=True,
        )
        _, local_rows = _records(local_output)
        self.assertIn("All active sessions (2)", local_rows[0])
        self.assertNotIn(REMOTE_ID, local_output)

        retained_duplicate = dict(local1)
        retained_duplicate["sourceObservation"] = "retained"
        duplicate_snapshot = _snapshot([retained_duplicate, dict(local1)], [])
        duplicate_output = render_snapshot(
            duplicate_snapshot,
            batch_state=state,
            continuation=True,
        )
        self.assertIn("All active sessions (1)", duplicate_output)
        self.assertNotIn("Last known", duplicate_output)

    def test_group_action_cycle_is_cache_only_and_does_not_touch_preferences(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([row], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        group = BatchUIState("group", ("local", "codex", LOCAL_ID), batch.ACTION_RESUME)
        data = _refresh_data(navigation=NavigationState(), batch_state=group)
        with tempfile.TemporaryDirectory() as temporary:
            batch_store = batch.BatchStateStore(Path(temporary))

            def cycle(retv: int, prior_data: str, info: str | None = None) -> str:
                output = io.StringIO()
                environ = {"ROFI_RETV": str(retv), "ROFI_DATA": prior_data}
                if info is not None:
                    environ["ROFI_INFO"] = info
                with mock.patch("sys.stdout", output):
                    run_rofi(
                        environ,
                        store=store,
                        config=PickerConfig(),
                        preference_store=preferences,
                        batch_state_store=batch_store,
                    )
                return output.getvalue()

            close = cycle(ROFI_RETV_CUSTOM_7, data)
            close_data = next(
                value.split("\x1f", 1)[1]
                for value in _records(close)[0]
                if value.startswith("\x00data\x1f")
            )
            self.assertEqual("close", parse_continuation_state(close_data).batch_state.action)
            self.assertIn(
                'Resume all · <span foreground="#42a5f5" weight="bold">[Close all windows]</span>',
                close,
            )
            resume = cycle(ROFI_RETV_CUSTOM_8, close_data)
            resume_data = next(
                value.split("\x1f", 1)[1]
                for value in _records(resume)[0]
                if value.startswith("\x00data\x1f")
            )
            self.assertEqual("resume", parse_continuation_state(resume_data).batch_state.action)
            self.assertEqual(0, store.context_calls)
            self.assertEqual([], store.refresh_calls)
            preferences.save.assert_not_called()

    def test_enter_conversation_in_group_returns_to_individual_actions_without_open(self) -> None:
        row = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([row], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        group = BatchUIState("group", None, batch.ACTION_RESUME)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch("sys.stdout", output),
                mock.patch("rofi_agent_plus.rofi._try_fast_open") as fast_open,
                mock.patch("rofi_agent_plus.rofi._open_selection") as open_selection,
            ):
                run_rofi(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_DATA": _refresh_data(batch_state=group),
                        "ROFI_INFO": selection_payload(row),
                    },
                    store=store,
                    config=PickerConfig(),
                    preference_store=preferences,
                    batch_state_store=batch.BatchStateStore(Path(temporary)),
                )
        fast_open.assert_not_called()
        open_selection.assert_not_called()
        preferences.save.assert_not_called()
        self.assertNotIn('"screen":"group"', output.getvalue())
        self.assertIn("Enter: <span", output.getvalue())
        self.assertIn("\x00new-selection\x1f1", output.getvalue())

    def test_inline_preview_marks_only_exact_targets_and_shows_exclusions(self) -> None:
        target = _row("local", "codex", LOCAL_ID, "$1", 1)
        excluded = _row(
            "local",
            "claude",
            "00000000-0000-0000-0000-000000000003",
            "$3",
            3,
            tmuxAmbiguous=True,
        )
        store = FakeStore(_snapshot([target, excluded], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        group = BatchUIState("group", None, batch.ACTION_RESUME)
        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
            return_value=contract_viewers.ViewerInspection("none", (), True),
        ) as inspect:
            with tempfile.TemporaryDirectory() as temporary:
                batch_store = batch.BatchStateStore(Path(temporary))
                output = io.StringIO()
                with mock.patch("sys.stdout", output):
                    run_rofi(
                        {
                            "ROFI_RETV": "1",
                            "ROFI_DATA": _refresh_data(batch_state=group),
                            "ROFI_INFO": _batch_row_info("batch-group", batch.ACTION_RESUME),
                        },
                        store=store,
                        config=PickerConfig(),
                        preference_store=preferences,
                        batch_state_store=batch_store,
                    )
                rendered = output.getvalue()
                self.assertIn("Confirm Resume all · 1 fixed targets", rendered)
                self.assertIn("Will open existing session", rendered)
                self.assertIn("Excluded · claude-local", rendered)
                self.assertNotIn("Active set in scope · observed", rendered)
                self.assertEqual(1, len(store.refresh_calls))
                inspect.assert_called_once()
                preferences.save.assert_not_called()

    def test_inline_confirm_consumes_once_and_renders_job_with_rows(self) -> None:
        target = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([target], []))
        preferences = mock.Mock(spec=ViewPreferenceStore)
        group = BatchUIState("group", None, batch.ACTION_RESUME)
        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
            return_value=contract_viewers.ViewerInspection("none", (), True),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                batch_store = batch.BatchStateStore(Path(temporary))
                preview_output = io.StringIO()
                with mock.patch("sys.stdout", preview_output):
                    run_rofi(
                        {
                            "ROFI_RETV": "1",
                            "ROFI_DATA": _refresh_data(batch_state=group),
                            "ROFI_INFO": _batch_row_info("batch-group", batch.ACTION_RESUME),
                        },
                        store=store,
                        config=PickerConfig(),
                        preference_store=preferences,
                        batch_state_store=batch_store,
                    )
                preview_headers, preview_rows = _records(preview_output.getvalue())
                preview_data = next(
                    value.split("\x1f", 1)[1]
                    for value in preview_headers
                    if value.startswith("\x00data\x1f")
                )
                preview_state = parse_continuation_state(preview_data).batch_state
                self.assertEqual("preview", preview_state.screen)
                preview_id = preview_state.record_id
                confirm_info = _options(preview_rows[0])[1]["info"]
                self.assertEqual("batch-confirm", json.loads(confirm_info)["type"])
                self.assertNotIn("\x00keep-filter\x1ftrue", preview_output.getvalue())

                worker_spawn = mock.patch("rofi_agent_plus.batch._spawn_worker")
                with worker_spawn as spawn:
                    job_output = io.StringIO()
                    with mock.patch("sys.stdout", job_output):
                        run_rofi(
                            {
                                "ROFI_RETV": "1",
                                "ROFI_DATA": preview_data,
                                "ROFI_INFO": confirm_info,
                            },
                            store=store,
                            config=PickerConfig(),
                            preference_store=preferences,
                            batch_state_store=batch_store,
                        )
                    spawn.assert_called_once()
                    job = batch_store.current_job()
                    self.assertEqual("queued", job["status"])
                    self.assertIsNone(batch_store.read_preview(preview_id))
                    job_headers, _ = _records(job_output.getvalue())
                    job_data = next(
                        value.split("\x1f", 1)[1]
                        for value in job_headers
                        if value.startswith("\x00data\x1f")
                    )
                    self.assertEqual("job", parse_continuation_state(job_data).batch_state.screen)
                    self.assertIn("Batch queued · Done 0", job_output.getvalue())
                    self.assertIn("codex-local", job_output.getvalue())
                    self.assertIn("Will open existing session", job_output.getvalue())
                    self.assertIn("delay: 1", job_output.getvalue())

                    # A stale replay sees the already-claimed job and cannot
                    # submit the private preview a second time.
                    replay = io.StringIO()
                    with mock.patch("sys.stdout", replay):
                        run_rofi(
                            {
                                "ROFI_RETV": "1",
                                "ROFI_DATA": preview_data,
                                "ROFI_INFO": confirm_info,
                            },
                            store=store,
                            config=PickerConfig(),
                            preference_store=preferences,
                            batch_state_store=batch_store,
                        )
                    spawn.assert_called_once()
                    replay_headers, _ = _records(replay.getvalue())
                    replay_data = next(
                        value.split("\x1f", 1)[1]
                        for value in replay_headers
                        if value.startswith("\x00data\x1f")
                    )
                    self.assertEqual(
                        "job", parse_continuation_state(replay_data).batch_state.screen
                    )
                preferences.save.assert_not_called()

    def test_preview_action_cycle_discards_confirm_and_requires_new_preview(self) -> None:
        target = _row("local", "codex", LOCAL_ID, "$1", 1)
        store = FakeStore(_snapshot([target], []))
        group = BatchUIState("group", None, batch.ACTION_RESUME)
        with mock.patch(
            "rofi_agent_plus.batch.contract_viewers.inspect_viewers",
            return_value=contract_viewers.ViewerInspection("none", (), True),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                batch_store = batch.BatchStateStore(Path(temporary))
                preview_output = io.StringIO()
                with mock.patch("sys.stdout", preview_output):
                    run_rofi(
                        {
                            "ROFI_RETV": "1",
                            "ROFI_DATA": _refresh_data(batch_state=group),
                            "ROFI_INFO": _batch_row_info("batch-group", batch.ACTION_RESUME),
                        },
                        store=store,
                        config=PickerConfig(),
                        batch_state_store=batch_store,
                    )
                preview_headers, _ = _records(preview_output.getvalue())
                preview_data = next(
                    value.split("\x1f", 1)[1]
                    for value in preview_headers
                    if value.startswith("\x00data\x1f")
                )
                preview_state = parse_continuation_state(preview_data).batch_state
                self.assertEqual("preview", preview_state.screen)
                old_preview_id = preview_state.record_id
                cycled = io.StringIO()
                with mock.patch("sys.stdout", cycled):
                    run_rofi(
                        {"ROFI_RETV": str(ROFI_RETV_CUSTOM_7), "ROFI_DATA": preview_data},
                        store=store,
                        config=PickerConfig(),
                        batch_state_store=batch_store,
                    )
                cycled_headers, _ = _records(cycled.getvalue())
                cycled_data = next(
                    value.split("\x1f", 1)[1]
                    for value in cycled_headers
                    if value.startswith("\x00data\x1f")
                )
                state = parse_continuation_state(cycled_data).batch_state
                self.assertEqual("group", state.screen)
                self.assertEqual(batch.ACTION_CLOSE, state.action)
                self.assertIsNone(batch_store.read_preview(old_preview_id))
                self.assertIn("[Close all windows]</span>", cycled.getvalue())
                self.assertIsNone(batch_store.active_job())

                # Replaying the stale preview data and its old typed Confirm
                # row cannot recreate the private preview or start a job.
                stale_confirm = io.StringIO()
                with (
                    mock.patch("sys.stdout", stale_confirm),
                    mock.patch("rofi_agent_plus.batch._spawn_worker") as spawn,
                ):
                    run_rofi(
                        {
                            "ROFI_RETV": "1",
                            "ROFI_DATA": preview_data,
                            "ROFI_INFO": _batch_row_info(
                                "batch-confirm", batch.ACTION_RESUME, old_preview_id
                            ),
                        },
                        store=store,
                        config=PickerConfig(),
                        batch_state_store=batch_store,
                    )
                spawn.assert_not_called()
                self.assertIsNone(batch_store.active_job())
                self.assertIn("Confirmation is stale", stale_confirm.getvalue())

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
        self.assertNotIn("Active set in scope · observed", rendered)
        headers, rows = _records(rendered)
        session_row = next(row for row in rows if row.startswith("codex-local"))
        _, options = _options(session_row)
        self.assertNotIn("Will open existing session", options["display"])
        self.assertNotIn("foreground=", options["display"])

    def test_page_exit_uses_page_ring_and_saves_only_page_preference(self) -> None:
        local = _row("local", "codex", LOCAL_ID, "$1", 1)
        remote = _row("remote", "claude", REMOTE_ID, "$9", 9)
        snapshot = _snapshot([local], [remote])
        store = FakeStore(snapshot)
        preferences = mock.Mock(spec=ViewPreferenceStore)
        group = BatchUIState("group", None, batch.ACTION_RESUME)
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                run_rofi(
                    {
                        "ROFI_RETV": "11",
                        "ROFI_DATA": _refresh_data(batch_state=group),
                    },
                    store=store,
                    config=PickerConfig(),
                    preference_store=preferences,
                    batch_state_store=batch.BatchStateStore(Path(temporary)),
                )
        self.assertIn("Agents › Local", output.getvalue())
        self.assertIn("\x00new-selection\x1f1", output.getvalue())
        preferences.save.assert_called_once()
        self.assertEqual(0, store.context_calls)
        self.assertEqual([], store.refresh_calls)

    def test_control_payload_with_session_fields_cannot_reach_fast_open(self) -> None:
        cache = mock.Mock(spec=CacheStore)
        cache.load.return_value = {"sessions": [], "errors": []}
        prefs = mock.Mock(spec=ViewPreferenceStore)
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
            state = batch.BatchStateStore(Path(temporary))
            output = io.StringIO()
            with (
                mock.patch("sys.stdout", output),
                mock.patch("rofi_agent_plus.rofi._try_fast_open") as fast_open,
            ):
                run_rofi(
                    {"ROFI_RETV": "1", "ROFI_INFO": json.dumps(forged)},
                    store=cache,
                    config=PickerConfig(),
                    preference_store=prefs,
                    batch_state_store=state,
                )
        fast_open.assert_not_called()
        self.assertIn("control rows cannot be opened", output.getvalue())
        prefs.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
