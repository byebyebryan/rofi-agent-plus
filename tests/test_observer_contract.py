"""Strict consumer validation for the provisional Observer snapshot wire."""

from __future__ import annotations

import copy
import json
import unittest

from rofi_agent_plus.observer_contract import ObserverContractError, parse_snapshot

HOST = "snap"
CODEX_ID = "11111111-1111-1111-1111-111111111111"
CLAUDE_ID = "22222222-2222-2222-2222-222222222222"
SAVED_CLAUDE_ID = "44444444-4444-4444-8444-444444444444"
COLLECTION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BOOT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
DIGEST = "c" * 64


def evidence(
    value: str,
    *,
    source: str | None = "codex_rpc",
    health: str = "current",
    observed_at: int | None = 100,
    reason: str = "native_snapshot",
) -> dict[str, object]:
    return {
        "value": value,
        "observedAt": observed_at,
        "source": source,
        "health": health,
        "reason": reason,
    }


def source(provider: str, *, health: str = "current", errors: list[dict[str, str]] | None = None):
    codex = provider == "codex"
    runtime = (
        {
            "version": "0.160.0",
            "versionEvidence": "verified_executable_sha256",
            "binarySha256": DIGEST,
            "pid": 123,
            "startTicks": 456,
            "bootId": BOOT_ID,
            "topology": "native_managed_endpoint",
            "endpoint": "/run/user/1000/codex.sock",
        }
        if codex
        else {
            "version": "2.1.287",
            "versionEvidence": "verified_installed_executable_sha256",
            "binarySha256": DIGEST,
            "bootId": BOOT_ID,
            "topology": "private_session_registry_and_job_store",
        }
    )
    coverage: dict[str, object] = (
        {
            "saved": {"complete": True, "reason": "native_snapshot"},
            "loaded": {"complete": True, "reason": "native_snapshot"},
            "work": {"supported": True, "supportedValues": ["working", "settled"]},
            "workerPresence": {"supported": False, "reason": "not_observed"},
            "clientBinding": {"supported": False, "reason": "source_not_established"},
        }
        if codex
        else {
            "saved": {"complete": False, "reason": "not_observed"},
            "sessionRegistry": "complete",
            "jobStore": "complete",
            "workerPresence": "complete",
            "attachment": "unsupported",
            "work": {"supportedValues": ["working", "settled", "needs_input"]},
            "clientBinding": {"supported": False, "reason": "source_not_established"},
        }
    )
    return {
        "schemaVersion": 1,
        "collectionId": COLLECTION_ID,
        "collectedAt": 101,
        "provider": provider,
        "configHome": "/home/test/.codex" if codex else "/home/test/.claude",
        "namespace": f"sha256:{provider}-namespace",
        "runtime": runtime,
        "coverage": coverage,
        "errors": errors or [],
        "limitations": ["study_contract", "no_watch"],
        "sourceHealth": health,
        "durationMs": 1.25,
    }


def codex_row(**changes: object) -> dict[str, object]:
    return {
        "identity": {
            "hostScope": HOST,
            "provider": "codex",
            "namespace": "sha256:codex-namespace",
            "nativeIdKind": "thread",
            "nativeId": CODEX_ID,
        },
        "work": evidence("working"),
        "presence": evidence("present"),
        "attachment": evidence(
            "unknown", source=None, health="unsupported", observed_at=None, reason="unsupported"
        ),
        "nativeIds": {"threadId": CODEX_ID, "sessionId": "33333333-3333-4333-8333-333333333333"},
        "nativeHistoryTimes": {"createdAt": 1_700_000_000_000, "updatedAt": 1_700_000_123_000},
        "title": "Synthetic Codex title",
        "cwd": "/work/codex",
        "runtimeVersion": "0.160.0",
        "sourceKind": "cli",
        "threadKind": "user",
        "metadataIssues": [],
        "inventory": "live",
        "presenceKind": "server_thread_loaded",
        "nativeState": {"type": "active", "activeFlags": []},
        "waitReason": "unknown",
        **changes,
    }


def claude_row(**changes: object) -> dict[str, object]:
    return {
        "identity": {
            "hostScope": HOST,
            "provider": "claude",
            "namespace": "sha256:claude-namespace",
            "nativeIdKind": "session",
            "nativeId": CLAUDE_ID,
        },
        "work": evidence("needs_input", source="claude_registry", observed_at=200_000),
        "presence": evidence("present", source="claude_registry"),
        "attachment": evidence(
            "unknown", source=None, health="unsupported", observed_at=None, reason="unsupported"
        ),
        "nativeIds": {"sessionId": CLAUDE_ID, "jobId": "abcdef01"},
        "title": "Claude " + CLAUDE_ID,
        "sessionKind": "bg",
        "nativeStatus": {"value": "waiting", "observedAt": 100},
        "job": {
            "id": "abcdef01",
            "retained": True,
            "state": "working",
            "tempo": "blocked",
            "terminalObservedAt": None,
        },
        "cwd": "/work/claude",
        "cwdSource": "claude_registry",
        "metadataIssues": [],
        "inventory": "live",
        "presenceKind": "os_worker",
        "waitReason": "approval",
        **changes,
    }


def claude_saved_row(
    *, created_at: int | None = 1_700_000_000_000, file_modified_at: int | None = None
) -> dict[str, object]:
    unknown = evidence(
        "unknown", source=None, health="unavailable", observed_at=None, reason="unobserved"
    )
    row = claude_row(
        **{
            "identity": {
                "hostScope": HOST,
                "provider": "claude",
                "namespace": "sha256:claude-namespace",
                "nativeIdKind": "session",
                "nativeId": SAVED_CLAUDE_ID,
            },
            "work": copy.deepcopy(unknown),
            "presence": copy.deepcopy(unknown),
            "nativeIds": {"sessionId": SAVED_CLAUDE_ID, "jobId": None},
            "title": "Saved Claude history",
            "sessionKind": "unknown",
            "nativeStatus": {"value": "unknown", "observedAt": None},
            "job": None,
            "cwdSource": "claude_history",
            "history": {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": created_at,
                "fileModifiedAt": file_modified_at,
            },
            "waitReason": "unknown",
            "inventory": "saved",
        }
    )
    row.pop("presenceKind")
    return row


def envelope(
    *, providers: list[str] | None = None, sessions: list[dict[str, object]] | None = None
):
    providers = providers if providers is not None else ["codex", "claude"]
    sources = [source(provider) for provider in providers]
    rows = sessions if sessions is not None else [codex_row(), claude_row()]
    return {
        "schemaVersion": 1,
        "collectionId": COLLECTION_ID,
        "collectedAt": 102,
        "host": {
            "authority": HOST,
            "authoritySource": "caller",
            "nativeHostname": "snap-native",
            "uid": 1000,
        },
        "sources": sources,
        "sessions": rows,
        "errors": [],
        "limitations": ["study_contract", "host_local_only", "no_watch"],
        "sourceHealth": "current",
        "durationMs": 2.5,
    }


def encode(value: object) -> bytes:
    return (json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n").encode()


class ObserverWireTest(unittest.TestCase):
    def parse(self, value: object, expected_providers: tuple[str, ...] = ("codex", "claude")):
        return parse_snapshot(
            encode(value), expected_host_scope=HOST, expected_providers=expected_providers
        )

    def test_selected_provider_does_not_require_the_other_source(self) -> None:
        for provider, row in (("codex", codex_row()), ("claude", claude_row())):
            with self.subTest(provider=provider):
                result = parse_snapshot(
                    encode(envelope(providers=[provider], sessions=[row])),
                    expected_host_scope=HOST,
                    expected_providers=(provider,),
                )
                self.assertEqual((), result.errors)
                self.assertEqual([provider], [item["kind"] for item in result.sessions])

    def test_missing_or_unavailable_selected_source_remains_an_error(self) -> None:
        value = envelope(providers=[], sessions=[])
        result = parse_snapshot(
            encode(value), expected_host_scope=HOST, expected_providers=("codex",)
        )
        self.assertEqual(["threads"], [error["stage"] for error in result.errors])
        value = envelope(providers=["claude"], sessions=[])
        value["sources"][0]["sourceHealth"] = "unavailable"
        value["sources"][0]["errors"] = [{"code": "runtime_artifact_unavailable"}]
        result = parse_snapshot(
            encode(value), expected_host_scope=HOST, expected_providers=("claude",)
        )
        self.assertEqual(["claude"], [error["stage"] for error in result.errors])
        self.assertIn("runtime_artifact_unavailable", result.errors[0]["message"])

    def test_unrequested_source_or_session_fails_closed(self) -> None:
        for value in (
            envelope(sessions=[]),
            envelope(providers=["codex"], sessions=[claude_row()]),
        ):
            with self.subTest(value=value), self.assertRaises(ObserverContractError):
                parse_snapshot(
                    encode(value), expected_host_scope=HOST, expected_providers=("codex",)
                )

    def test_invalid_requested_provider_selection_is_bounded(self) -> None:
        for providers in ((), ("codex", "codex"), ("opencode",), ([],), "codex", ["codex"]):
            with (
                self.subTest(providers=providers),
                self.assertRaisesRegex(ObserverContractError, "provider selection is invalid"),
            ):
                parse_snapshot(
                    encode(envelope()), expected_host_scope=HOST, expected_providers=providers
                )

    def test_claude_configuration_mode_is_explicit_and_missing_stays_unknown(self):
        for mode in ("default", "explicit"):
            value = envelope()
            value["sources"][1]["configHomeKind"] = mode
            claude = self.parse(value).sessions[1]
            self.assertEqual(mode, claude["observerSource"]["configHomeKind"])
        self.assertEqual(
            "unknown", self.parse(envelope()).sessions[1]["observerSource"]["configHomeKind"]
        )
        for malformed in (True, [], "guessed"):
            value = envelope()
            value["sources"][1]["configHomeKind"] = malformed
            with self.assertRaises(ObserverContractError):
                self.parse(value)
        value = envelope()
        value["sources"][0]["configHomeKind"] = "default"
        with self.assertRaises(ObserverContractError):
            self.parse(value)

    def test_projects_independent_namespaces_and_presence_work_and_attachment(self) -> None:
        result = self.parse(envelope())
        self.assertEqual([], list(result.errors))
        self.assertEqual(2, len(result.sessions))
        codex, claude = result.sessions
        self.assertEqual("sha256:codex-namespace", codex["namespace"])
        self.assertEqual("thread", codex["nativeIdKind"])
        self.assertEqual(
            {"threadId": CODEX_ID, "sessionId": "33333333-3333-4333-8333-333333333333"},
            codex["nativeIds"],
        )
        self.assertEqual("current", codex["observerSource"]["sourceHealth"])
        self.assertEqual("0.160.0", codex["observerSource"]["runtime"]["version"])
        self.assertNotIn("pid", codex["observerSource"]["runtime"])
        self.assertNotIn("endpoint", codex["observerSource"]["runtime"])
        self.assertIn("collectionId", codex["observerSource"])
        self.assertIn("collectedAt", codex["observerSource"])
        self.assertTrue(codex["active"])
        self.assertEqual("working", codex["activityState"])
        self.assertEqual(1_700_000_000, codex["createdAt"])
        self.assertEqual(1_700_000_123, codex["updatedAt"])
        self.assertEqual(1_700_000_123, codex["recencyAt"])
        self.assertNotIn("tmux", codex)
        self.assertNotIn("providerOptionVerified", codex)
        self.assertTrue(claude["active"])
        self.assertEqual("waiting", claude["activityState"])
        self.assertEqual(200, claude["recencyAt"])
        self.assertNotIn("updatedAt", claude)
        self.assertEqual("abcdef01", claude["job"]["id"])
        self.assertEqual("approval", claude["waitReason"])
        self.assertEqual("sha256:claude-namespace", claude["observerIdentity"]["namespace"])

    def test_codex_child_enum_is_preserved_and_strictly_correlated(self) -> None:
        child = self.parse(
            envelope(sessions=[codex_row(sourceKind="subagent", threadKind="child")])
        ).sessions[0]
        self.assertEqual("subagent", child["sourceKind"])
        self.assertEqual("child", child["threadKind"])

        uncertain = self.parse(
            envelope(sessions=[codex_row(sourceKind="subagent", threadKind="unknown")])
        ).sessions[0]
        self.assertEqual("unknown", uncertain["threadKind"])

        explicit_thread_source = self.parse(
            envelope(sessions=[codex_row(sourceKind="unknown", threadKind="child")])
        ).sessions[0]
        self.assertEqual("child", explicit_thread_source["threadKind"])
        self.assertEqual("unknown", explicit_thread_source["sourceKind"])
        source_origin = self.parse(
            envelope(sessions=[codex_row(sourceKind="cli", threadKind="child")])
        ).sessions[0]
        self.assertEqual("child", source_origin["threadKind"])

        missing_source_kind = codex_row(threadKind="child")
        missing_source_kind.pop("sourceKind")
        malformed_rows = (
            codex_row(sourceKind="other", threadKind="child"),
            codex_row(sourceKind="subagent", threadKind="user"),
            codex_row(sourceKind="subagent"),
            missing_source_kind,
            codex_row(sourceKind="cli", threadKind="main"),
        )
        for row in malformed_rows:
            with self.subTest(row=row), self.assertRaises(ObserverContractError):
                self.parse(envelope(sessions=[row]))

    def test_raw_codex_source_payload_is_rejected_without_echoing_private_fields(self) -> None:
        row = codex_row(
            sourceKind="subagent",
            threadKind="child",
            source={"subAgent": {"thread_spawn": {"agent_role": "synthetic_private_role"}}},
        )
        with self.assertRaises(ObserverContractError) as raised:
            self.parse(envelope(sessions=[row]))
        self.assertNotIn("synthetic_private_role", str(raised.exception))

    def test_work_state_requires_current_clock_and_presence_is_independent(self) -> None:
        rows = [
            codex_row(
                **{
                    "work": evidence(
                        "unknown",
                        source=None,
                        health="unsupported",
                        observed_at=None,
                        reason="unsupported",
                    ),
                    "presence": evidence("present"),
                }
            ),
            claude_row(
                **{
                    "work": evidence("settled", source="claude_job_store"),
                    "presence": evidence(
                        "unknown",
                        source=None,
                        health="unavailable",
                        observed_at=None,
                        reason="source_unavailable",
                    ),
                }
            ),
        ]
        result = self.parse(envelope(sessions=rows))
        self.assertEqual(
            (True, "unknown"), (result.sessions[0]["active"], result.sessions[0]["activityState"])
        )
        self.assertIsNone(result.sessions[1]["active"])
        self.assertEqual("idle", result.sessions[1]["activityState"])

    def test_codex_missing_update_time_does_not_guess_recency_from_creation(self) -> None:
        row = codex_row(
            nativeHistoryTimes={"createdAt": 1_700_000_000_000, "updatedAt": None},
            metadataIssues=["native_history_time_unavailable"],
        )
        result = self.parse(envelope(sessions=[row]))
        session = result.sessions[0]
        self.assertEqual(1_700_000_000, session["createdAt"])
        self.assertIsNone(session["updatedAt"])
        self.assertIsNone(session["recencyAt"])

    def test_claude_recency_uses_work_event_clock_and_ignores_refreshed_presence(self) -> None:
        first_row = claude_row(
            **{
                "work": evidence("needs_input", source="claude_job_store", observed_at=300_000),
                "presence": evidence("present", source="claude_registry", observed_at=400_000),
            }
        )
        later_presence = copy.deepcopy(first_row)
        later_presence["presence"] = evidence(
            "present", source="claude_registry", observed_at=900_000
        )
        first = self.parse(envelope(sessions=[first_row])).sessions[0]
        later = self.parse(envelope(sessions=[later_presence])).sessions[0]
        self.assertEqual(300, first["recencyAt"])
        self.assertEqual(first["recencyAt"], later["recencyAt"])
        unknown_work = claude_row(
            **{
                "work": evidence(
                    "unknown",
                    source=None,
                    health="unsupported",
                    observed_at=None,
                    reason="unsupported",
                )
            }
        )
        unknown = self.parse(envelope(sessions=[unknown_work])).sessions[0]
        self.assertIsNone(unknown["recencyAt"])

    def test_claude_saved_history_projects_unknown_evidence_and_creation_order(self) -> None:
        payload = envelope(
            providers=["claude"],
            sessions=[
                claude_saved_row(
                    created_at=1_700_000_000_000,
                    file_modified_at=4_000_000_000_000,
                )
            ],
        )
        payload["sources"][0]["coverage"]["saved"] = {
            "complete": False,
            "reason": "metadata_scan",
        }
        session = self.parse(payload, ("claude",)).sessions[0]
        self.assertEqual("saved", session["inventory"])
        self.assertEqual("unknown", session["sessionKind"])
        self.assertIsNone(session["job"])
        self.assertEqual("claude_history", session["cwdSource"])
        self.assertEqual(
            {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": 1_700_000_000_000,
                "fileModifiedAt": 4_000_000_000_000,
            },
            session["history"],
        )
        self.assertEqual(1_700_000_000, session["createdAt"])
        self.assertEqual(1_700_000_000, session["recencyAt"])
        self.assertEqual("unknown", session["work"]["value"])
        self.assertEqual("unobserved", session["work"]["reason"])
        self.assertEqual("unavailable", session["work"]["health"])
        self.assertIsNone(session["work"]["observedAt"])
        self.assertIsNone(session["work"]["source"])
        self.assertEqual("unknown", session["presence"]["value"])
        self.assertEqual("unobserved", session["presence"]["reason"])
        self.assertEqual("current", session["sourceObservation"])

        no_creation_clock = claude_saved_row(created_at=None, file_modified_at=4_000_000_000_000)
        payload["sessions"] = [no_creation_clock]
        self.assertIsNone(self.parse(payload, ("claude",)).sessions[0]["recencyAt"])

        payload["sources"][0]["coverage"]["saved"]["complete"] = True
        with self.assertRaises(ObserverContractError):
            self.parse(payload, ("claude",))

    def test_missing_saved_cwd_stays_visible_without_an_invented_path(self):
        row = claude_saved_row()
        row.update(cwd=None, cwdSource=None)
        parsed = self.parse(envelope(providers=["claude"], sessions=[row]), ("claude",))
        self.assertIsNone(parsed.sessions[0]["cwd"])
        self.assertIsNone(parsed.sessions[0]["cwdSource"])
        self.assertEqual("saved", parsed.sessions[0]["inventory"])

    def test_claude_history_metadata_is_strictly_allowlisted(self) -> None:
        invalid_history = (
            {"source": "claude_sdk", "sdkVersion": "0.2.163", "createdAt": None},
            {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": None,
                "fileModifiedAt": None,
                "rawPath": "private_history_path",
            },
            {
                "source": "filesystem",
                "sdkVersion": "0.2.163",
                "createdAt": None,
                "fileModifiedAt": None,
            },
            {
                "source": "claude_sdk",
                "sdkVersion": "0.2.164",
                "createdAt": None,
                "fileModifiedAt": None,
            },
            {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": True,
                "fileModifiedAt": None,
            },
            {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": -1,
                "fileModifiedAt": None,
            },
            {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": 4_000_000_000_001,
                "fileModifiedAt": None,
            },
            {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": None,
                "fileModifiedAt": "1700000000000",
            },
        )
        for history in invalid_history:
            payload = envelope(providers=["claude"], sessions=[claude_saved_row()])
            payload["sessions"][0]["history"] = history
            with self.subTest(history=history), self.assertRaises(ObserverContractError) as raised:
                self.parse(payload, ("claude",))
            self.assertNotIn("private_history_path", str(raised.exception))

    def test_claude_live_history_never_overrides_work_recency_or_names(self) -> None:
        row = claude_row(
            **{
                "title": "Current Claude job",
                "work": evidence("working", source="claude_job_store", observed_at=300_000),
                "history": {
                    "source": "claude_sdk",
                    "sdkVersion": "0.2.163",
                    "createdAt": 100_000,
                    "fileModifiedAt": 4_000_000_000_000,
                },
            }
        )
        session = self.parse(envelope(providers=["claude"], sessions=[row]), ("claude",)).sessions[
            0
        ]
        self.assertEqual("Current Claude job", session["title"])
        self.assertEqual("Current Claude job", session["name"])
        self.assertEqual(300, session["recencyAt"])
        self.assertEqual(100, session["createdAt"])

    def test_claude_history_warning_is_independent_of_healthy_live_rows(self) -> None:
        payload = envelope(providers=["claude"], sessions=[claude_row()])
        payload["sources"][0] = source(
            "claude", health="current", errors=[{"code": "history_timeout"}]
        )
        payload["sourceHealth"] = "current"
        result = self.parse(payload, ("claude",))
        self.assertEqual(
            [
                {
                    "host": HOST,
                    "stage": "claude-history",
                    "message": "Claude history metadata warning (history_timeout)",
                }
            ],
            list(result.errors),
        )
        session = result.sessions[0]
        self.assertEqual("current", session["sourceObservation"])
        self.assertEqual("current", session["observerSource"]["sourceHealth"])
        self.assertEqual("live", session["inventory"])
        self.assertEqual("needs_input", session["work"]["value"])
        self.assertEqual(200, session["recencyAt"])

    def test_claude_history_errors_are_finite(self) -> None:
        payload = envelope(providers=["claude"], sessions=[])
        payload["sources"][0]["errors"] = [{"code": "history_scan_incomplete"}]
        with self.assertRaises(ObserverContractError):
            self.parse(payload, ("claude",))

    def test_native_history_times_are_strict_and_never_leak_invalid_values(self) -> None:
        for malformed in (
            {"createdAt": True, "updatedAt": 1_700_000_123_000},
            {"createdAt": 1_700_000_000_000, "updatedAt": 1_700_000_123_001},
            {"createdAt": "synthetic_private_timestamp", "updatedAt": None},
            {"createdAt": 1_700_000_000_000},
            None,
        ):
            payload = envelope(sessions=[codex_row(nativeHistoryTimes=malformed)])
            with (
                self.subTest(malformed=malformed),
                self.assertRaises(ObserverContractError) as raised,
            ):
                self.parse(payload)
            self.assertNotIn("synthetic_private_timestamp", str(raised.exception))

    def test_nullable_codex_history_times_require_a_finite_issue_code(self) -> None:
        missing_issue = codex_row(
            nativeHistoryTimes={"createdAt": 1_700_000_000_000, "updatedAt": None}
        )
        with self.assertRaises(ObserverContractError):
            self.parse(envelope(sessions=[missing_issue]))
        false_issue = codex_row(
            nativeHistoryTimes={
                "createdAt": 1_700_000_000_000,
                "updatedAt": 1_700_000_123_000,
            },
            metadataIssues=["native_history_time_unavailable"],
        )
        with self.assertRaises(ObserverContractError):
            self.parse(envelope(sessions=[false_issue]))

    def test_partial_provider_failure_keeps_healthy_sibling_rows_and_finite_error(self) -> None:
        payload = envelope()
        payload["sources"][1] = source(
            "claude", health="stale", errors=[{"code": "job_store_partial"}]
        )
        payload["sourceHealth"] = "partial"
        result = self.parse(payload)
        self.assertEqual(2, len(result.sessions))
        self.assertEqual("codex", result.sessions[0]["kind"])
        self.assertEqual(
            [
                {
                    "host": HOST,
                    "stage": "claude",
                    "message": "Observer source stale (job_store_partial)",
                }
            ],
            list(result.errors),
        )

    def test_unavailable_empty_provider_is_not_reported_as_healthy_empty(self) -> None:
        payload = envelope(providers=["codex"])
        payload["sessions"] = [codex_row()]
        payload["sourceHealth"] = "partial"
        result = self.parse(payload)
        self.assertEqual(1, len(result.sessions))
        self.assertEqual(["claude"], [row["stage"] for row in result.errors])

    def test_unavailable_source_without_runtime_preserves_healthy_sibling(self) -> None:
        payload = envelope()
        unavailable = payload["sources"][1]
        unavailable["sourceHealth"] = "unavailable"
        unavailable["errors"] = [{"code": "unsupported_runtime_version"}]
        unavailable.pop("namespace")
        unavailable.pop("runtime")
        payload["sessions"] = [codex_row()]
        payload["sourceHealth"] = "partial"
        result = self.parse(payload)
        self.assertEqual(["codex"], [row["kind"] for row in result.sessions])
        self.assertEqual(["claude"], [row["stage"] for row in result.errors])

    def test_rejects_version_host_provenance_and_identity_mismatch(self) -> None:
        mutations = []
        unsupported = envelope()
        unsupported["schemaVersion"] = 2
        mutations.append((unsupported, "schema"))
        wrong_host = envelope()
        wrong_host["host"]["authority"] = "other"
        mutations.append((wrong_host, "provenance"))
        wrong_row_host = envelope()
        wrong_row_host["sessions"][0]["identity"]["hostScope"] = "other"
        mutations.append((wrong_row_host, "identity"))
        mismatch = envelope()
        mismatch["sessions"][0]["identity"]["namespace"] = "sha256:other"
        mutations.append((mismatch, "namespace"))
        for payload, label in mutations:
            with self.subTest(label=label), self.assertRaises(ObserverContractError):
                self.parse(payload)

    def test_rejects_duplicate_native_keys_and_duplicate_sources(self) -> None:
        duplicate = envelope(sessions=[codex_row(), copy.deepcopy(codex_row())])
        with self.assertRaisesRegex(ObserverContractError, "duplicate native"):
            self.parse(duplicate)
        duplicate_source = envelope(providers=["codex", "codex"])
        with self.assertRaisesRegex(ObserverContractError, "duplicate provider"):
            self.parse(duplicate_source)

    def test_rejects_conflicting_claude_session_and_job_ids(self) -> None:
        conflicting = envelope()
        conflicting["sessions"][1]["job"]["id"] = "deadbeef"
        with self.assertRaises(ObserverContractError):
            self.parse(conflicting)

    def test_rejects_attachment_claim_when_source_disables_client_binding(self) -> None:
        conflicting = envelope()
        conflicting["sessions"][0]["attachment"] = evidence("attached")
        with self.assertRaises(ObserverContractError):
            self.parse(conflicting)

    def test_rejects_bad_types_unknown_fields_and_nonfinite_numbers_without_leaking_values(
        self,
    ) -> None:
        malformed = envelope()
        malformed["sessions"][0]["work"]["value"] = []
        with self.assertRaises(ObserverContractError):
            self.parse(malformed)
        unknown = envelope()
        unknown["sessions"][0]["providerPayload"] = "must not pass"
        with self.assertRaises(ObserverContractError):
            self.parse(unknown)
        nonfinite = (
            json.dumps(envelope()).replace('"durationMs": 2.5', '"durationMs": NaN').encode()
        )
        with self.assertRaisesRegex(ObserverContractError, "wire"):
            parse_snapshot(nonfinite + b"\n", expected_host_scope=HOST)

    def test_rejects_unbounded_coverage_content(self) -> None:
        payload = envelope()
        payload["sources"][0]["coverage"]["clientBinding"]["reason"] = "private prompt text"
        with self.assertRaises(ObserverContractError):
            self.parse(payload)

    def test_rejects_trailing_documents_and_output_over_limit(self) -> None:
        raw = encode(envelope())
        with self.assertRaises(ObserverContractError):
            parse_snapshot(raw + raw, expected_host_scope=HOST)
        with self.assertRaises(ObserverContractError):
            parse_snapshot(raw, expected_host_scope=HOST, limit=1)


if __name__ == "__main__":
    unittest.main()
