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
    ContinuationState,
    NavigationState,
    _root_after_batch,
    render_snapshot,
    run_rofi,
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

    def presentation_context(self, _config: PickerConfig) -> PresentationContext:
        return self.context

    def load_current(self, _config: PickerConfig, _context: object = None) -> dict[str, object]:
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
    def test_back_returns_to_first_conversation_or_empty_page_batch_row(self) -> None:
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
        self.assertEqual("actions", batch_options["meta"])
        self.assertEqual("batch", rows[0].split("\x00", 1)[0])
        self.assertEqual("Batch actions…", batch_options["display"])
        self.assertEqual(LOCAL_ID, json.loads(session_options["info"])["id"])
        self.assertIn("\x00new-selection\x1f1", headers)
        self.assertIn("\x00no-custom\x1ffalse", headers)

    def test_empty_page_selects_batch_row_and_custom4_opens_menu_without_saving(self) -> None:
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
            self.assertIn("Close all windows", output.getvalue())
            self.assertIn("Resume all active sessions", output.getvalue())
            self.assertIn("batch-ui:", output.getvalue())
            preferences.save.assert_not_called()
            self.assertFalse(cache.presentation_context.called)

        headers, rows = _records(render_snapshot(empty, initial_open=True))
        _, options = _options(rows[0])
        self.assertEqual("batch", json.loads(options["info"])["type"])
        self.assertIn("\x00new-selection\x1f0", headers)

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
