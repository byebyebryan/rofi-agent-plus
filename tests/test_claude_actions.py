"""Native cue and identity validation for supervised Claude creation."""

from __future__ import annotations

import unittest

from rofi_agent_plus.claude_actions import (
    CLAUDE_BACKGROUND_SHA256,
    ClaudeActionError,
    background_session,
    parse_background_job_id,
)
from rofi_agent_plus.observer_contract import ObserverSnapshot

HOST = "snap"
NAMESPACE = "sha256:claude-test-namespace"
CONFIG_HOME = "/home/test/.claude"
CONFIG_HOME_KIND = "default"
CWD = "/work/Agent Plus"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "abcdef01"


def native_output(job_id: str = JOB_ID) -> bytes:
    return (
        f"backgrounded \u00b7 {job_id}\n  claude attach {job_id}  open in this terminal\n"
    ).encode()


def row(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": "claude",
        "id": SESSION_ID,
        "namespace": NAMESPACE,
        "cwd": CWD,
        "sessionKind": "bg",
        "nativeIds": {"sessionId": SESSION_ID, "jobId": JOB_ID},
        "job": {"id": JOB_ID, "state": "working", "tempo": "active"},
        "presence": {"value": "present", "health": "current"},
        "observerIdentity": {
            "hostScope": HOST,
            "provider": "claude",
            "namespace": NAMESPACE,
            "nativeIdKind": "session",
            "nativeId": SESSION_ID,
        },
        "observerSource": {
            "schemaVersion": 1,
            "provider": "claude",
            "namespace": NAMESPACE,
            "configHome": CONFIG_HOME,
            "configHomeKind": CONFIG_HOME_KIND,
            "runtime": {
                "version": "2.1.287",
                "binarySha256": CLAUDE_BACKGROUND_SHA256,
                "topology": "private_session_registry_and_job_store",
            },
            "sourceHealth": "current",
        },
    }
    value.update(changes)
    return value


class ClaudeBackgroundCueTest(unittest.TestCase):
    def test_accepts_pinned_cue_with_sgr_and_repeated_same_short_id(self) -> None:
        output = (
            "\x1b[36mbackgrounded \u00b7 abcdef01\x1b[0m\n"
            "\x1b[2m  claude attach abcdef01  open in this terminal\x1b[0m\n"
            "backgrounded \u00b7 abcdef01\n"
        ).encode()
        self.assertEqual(JOB_ID, parse_background_job_id(output))

    def test_rejects_duplicate_or_conflicting_native_cues(self) -> None:
        cases = (
            native_output() + b"  claude attach abcdef01  open in this terminal\n",
            native_output() + "backgrounded \u00b7 12345678\n".encode(),
            "backgrounded \u00b7 abcdef01\n  claude attach 12345678  open in this terminal\n".encode(),
            "backgrounded \u00b7 abcdef01\n  claude attach abcdef01 extra\n".encode(),
            "backgrounded \u00b7 abcdef01\n\x1b]0;title\x07  claude attach abcdef01  open in this terminal\n".encode(),
        )
        for output in cases:
            with self.subTest(size=len(output)), self.assertRaises(ClaudeActionError):
                parse_background_job_id(output)

    def test_rejects_missing_oversized_and_invalid_utf8_output(self) -> None:
        for output in (b"", b"\xff", b"x" * (32 * 1024 + 1)):
            with self.subTest(size=len(output)), self.assertRaises(ClaudeActionError):
                parse_background_job_id(output)


class CreatedBackgroundProjectionTest(unittest.TestCase):
    def project(
        self,
        value: dict[str, object],
        *,
        health: str = "current",
        errors: tuple[dict[str, str], ...] = (),
    ):
        return ObserverSnapshot((value,), errors, health)

    def lookup(self, snapshot: ObserverSnapshot):
        return background_session(
            snapshot,
            host_id=HOST,
            cwd=CWD,
            config_home=CONFIG_HOME,
            config_home_kind=CONFIG_HOME_KIND,
            namespace=NAMESPACE,
            job_id=JOB_ID,
        )

    def test_returns_only_exact_pinned_present_background_identity(self) -> None:
        value = row()
        self.assertEqual(value, self.lookup(self.project(value)))

        # Codex failed independently, but this exact Claude row remains
        # current and usable in the aggregate partial snapshot.
        partial = self.project(
            value,
            health="partial",
            errors=({"host": HOST, "stage": "threads", "message": "source unavailable"},),
        )
        self.assertEqual(value, self.lookup(partial))

    def test_waits_for_presence_but_fails_closed_on_mismatches_and_duplicates(self) -> None:
        absent = row(presence={"value": "absent", "health": "current"})
        self.assertIsNone(self.lookup(self.project(absent)))
        self.assertIsNone(self.lookup(self.project(row(), health="unavailable")))

        wrong_config = row(
            observerSource={
                **row()["observerSource"],
                "configHome": "/other",
            }
        )
        wrong_artifact = row(
            observerSource={
                **row()["observerSource"],
                "runtime": {
                    **row()["observerSource"]["runtime"],
                    "binarySha256": "0" * 64,
                },
            }
        )
        cases = (
            row(namespace="sha256:other"),
            row(cwd="/work/other"),
            wrong_config,
            wrong_artifact,
            row(observerSource={**row()["observerSource"], "configHomeKind": "explicit"}),
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ClaudeActionError):
                self.lookup(self.project(value))

        # A duplicate with the same short ID remains ambiguous even if every
        # projected field otherwise agrees.
        with self.assertRaises(ClaudeActionError):
            self.lookup(ObserverSnapshot((row(), row()), (), "current"))

    def test_workspace_trust_error_is_matched_without_retaining_cwd(self) -> None:
        from rofi_agent_plus.claude_actions import workspace_trust_required

        stderr = (
            "Workspace not trusted. Run `claude` in "
            f"{CWD} once and accept the trust prompt, then retry.\n"
        ).encode()
        self.assertTrue(workspace_trust_required(stderr, cwd=CWD))
        self.assertFalse(workspace_trust_required(stderr, cwd="/different"))
        self.assertFalse(workspace_trust_required(stderr + b"details", cwd=CWD))
