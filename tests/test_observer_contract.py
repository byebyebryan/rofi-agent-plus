"""Consumer conformance against the frozen, independently accepted corpus."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unittest

from agent_observer.contract import validate_watch
from observer_fixtures import ROOT, document, fixture, native_row

from rofi_agent_plus.observer_contract import (
    ObserverContractError,
    parse_snapshot,
    session_sort_key,
)
from rofi_agent_plus.rofi import _activity_label


def encode(value):
    return json.dumps(value, separators=(",", ":")).encode()


class PublicObserverTest(unittest.TestCase):
    def parse(self, payload):
        return parse_snapshot(encode(payload), expected_host_scope=payload["host"]["authority"])

    def test_corpus_hashes_and_both_provider_projection(self):
        manifest = fixture("SOURCE.json")
        for name, digest in manifest["files"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        for name in ("snapshot.json", "partial.json", "claude-ready.json"):
            payload = fixture(name)
            snapshot = self.parse(payload)
            self.assertEqual(len(payload["sessions"]), len(snapshot.sessions))
        validate_watch(fixture("gap.json"))

    def test_phase_order_age_and_creation_remain_separate(self):
        payload = document()
        rows = [
            native_row("alpha", "codex", phase=phase) for phase in ("working", "waiting", "blocked")
        ]
        for index, row in enumerate(rows):
            identifier = f"00000000-0000-0000-0000-{index + 1:012d}"
            row["identity"]["nativeId"] = row["nativeIds"]["threadId"] = identifier
        payload["sessions"] = rows
        projected = sorted(self.parse(payload).sessions, key=session_sort_key)
        self.assertEqual(
            ["blocked", "waiting", "working"], [row["activityState"] for row in projected]
        )
        self.assertTrue(all(row["recencyAt"] is None for row in projected))
        self.assertTrue(all(row["createdAt"] is not None for row in projected))
        self.assertEqual(
            ["Blocked", "Waiting", "Working"], [_activity_label(row) for row in projected]
        )

    def test_runtime_worker_outcome_and_phase_clocks_remain_independent(self):
        raw = fixture("claude-ready.json")
        row = next(
            row
            for row in self.parse(raw).sessions
            if row["kind"] == "claude" and row["phase"]["value"] == "waiting"
        )
        self.assertTrue(row["active"])
        self.assertEqual("completed", row["outcome"]["value"])
        self.assertEqual("native", row["phase"]["clock"])
        self.assertEqual("sample", row["worker"]["clock"])

    def test_partial_history_warning_preserves_current_live_phase(self):
        payload = document()
        payload["sources"][1]["errors"] = ["history_timeout"]
        payload["sources"][1]["sourceHealth"] = "partial"
        payload["sources"][1]["coverage"]["saved"]["status"] = "unavailable"
        result = self.parse(payload)
        self.assertEqual(["claude-history"], [error["stage"] for error in result.errors])
        self.assertEqual("current", result.sessions[1]["sourceObservation"])
        self.assertEqual("waiting", result.sessions[1]["phase"]["value"])
        self.assertEqual(("claude",), result.incomplete_providers)

    def test_host_store_schema_ids_duplicate_and_unknown_fields_fail_closed(self):
        baseline = document()
        mutations = []
        for field, value in (
            ("schemaVersion", 1),
            ("sessions", baseline["sessions"] + [copy.deepcopy(baseline["sessions"][0])]),
        ):
            raw = copy.deepcopy(baseline)
            raw[field] = value
            mutations.append(raw)
        raw = copy.deepcopy(baseline)
        raw["sources"][0]["configHome"] = "/different"
        mutations.append(raw)
        raw = copy.deepcopy(baseline)
        raw["sessions"][0]["identity"]["hostScope"] = "other"
        mutations.append(raw)
        raw = copy.deepcopy(baseline)
        raw["sessions"][0]["nativeIds"]["threadId"] = raw["sessions"][1]["nativeIds"]["sessionId"]
        mutations.append(raw)
        raw = copy.deepcopy(baseline)
        raw["sessions"][0]["providerPayload"] = "private-sentinel"
        mutations.append(raw)
        for raw in mutations:
            with self.assertRaises(ObserverContractError) as raised:
                self.parse(raw)
            self.assertNotIn("private-sentinel", str(raised.exception))
        with self.assertRaises(ObserverContractError):
            parse_snapshot(encode(baseline), expected_host_scope="another")

    def test_public_scope_wire_caps_and_duplicate_keys(self):
        raw = encode(document())
        for data in (
            raw + raw,
            raw.replace(b'"schemaVersion":2', b'"schemaVersion":2,"schemaVersion":2', 1),
        ):
            with self.assertRaises(ObserverContractError):
                parse_snapshot(data, expected_host_scope="alpha")
        with self.assertRaises(ObserverContractError):
            parse_snapshot(raw, expected_host_scope="alpha", limit=1)
        with self.assertRaises(ObserverContractError):
            parse_snapshot(raw, expected_host_scope="alpha", expected_providers=("codex",))

    def test_public_projection_does_not_load_collectors_or_write_runtime(self):
        program = "import sys; import rofi_agent_plus.observer_contract; assert not any(name in sys.modules for name in ['agent_observer.collection','agent_observer.codex_snapshot','agent_observer.claude_metadata','agent_observer.write_client'])"
        subprocess.run([sys.executable, "-c", program], check=True)
