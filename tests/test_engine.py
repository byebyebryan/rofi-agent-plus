"""Focused tests for Agent Plus provider-native helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import engine

THREAD = "11111111-1111-1111-1111-111111111111"
OPENCODE = "ses_0319af718ffegy8N1IoMEggx4B"


class ProviderCorrelationTest(unittest.TestCase):
    def test_root_rollout_wins_over_a_subagent_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fd = root / "proc" / "42" / "fd"
            fd.mkdir(parents=True)
            root_rollout = root / f"rollout-{THREAD}.jsonl"
            root_rollout.write_text('{"type":"session_meta","payload":{"source":"cli"}}\n')
            child_rollout = root / "rollout-22222222-2222-2222-2222-222222222222.jsonl"
            child_rollout.write_text(
                '{"type":"session_meta","payload":{"source":{"subagent":{}}}}\n'
            )
            (fd / "3").symlink_to(child_rollout)
            (fd / "4").symlink_to(root_rollout)
            self.assertEqual(THREAD, engine._thread_id_for_process(42, root / "proc"))

    def test_provider_active_snapshot_never_queries_tmux(self) -> None:
        with (
            mock.patch.object(
                engine,
                "_process_table",
                return_value=({10: 1}, {10}, set(), set()),
            ),
            mock.patch.object(engine, "_thread_id_for_process", return_value=THREAD),
        ):
            result = engine.provider_active_snapshot()
        self.assertEqual([10], result["active"][THREAD]["candidates"][0]["ancestors"])
        self.assertNotIn("tmuxSession", result["active"][THREAD]["candidates"][0])

    def test_provider_active_snapshot_reports_probe_failure_without_a_traceback(self) -> None:
        with mock.patch.object(engine, "_process_table", side_effect=OSError("ps unavailable")):
            with self.assertRaisesRegex(engine.PickerError, "provider activity probe failed"):
                engine.provider_active_snapshot()

    def test_merge_preserves_provider_rows_and_partial_failure(self) -> None:
        result = engine.merge_provider_results(
            "local",
            "local",
            [{"id": THREAD, "name": "Codex", "cwd": "/work", "recencyAt": 5}],
            engine.PickerError("Claude unavailable"),
            {"installed": True, "sessions": [{"id": OPENCODE, "cwd": "/code", "recencyAt": 6}]},
            {
                "active": {THREAD: {"candidates": [{"pid": 10, "ancestors": [10]}]}},
                "claudeActive": {},
                "opencodeActive": {},
            },
            40,
        )
        self.assertEqual([OPENCODE, THREAD], [row["id"] for row in result["sessions"]])
        self.assertTrue(result["sessions"][1]["active"])
        self.assertEqual("claude", result["errors"][0]["stage"])


class RetiredLifecycleTest(unittest.TestCase):
    def test_agent_engine_has_no_generic_lifecycle_surface(self) -> None:
        for name in (
            "HostTarget",
            "SshPolicy",
            "parse_host_routes",
            "stream_session_events",
            "resolve_open_target",
            "launch_attach",
            "focus_existing_window",
        ):
            self.assertFalse(hasattr(engine, name), name)
