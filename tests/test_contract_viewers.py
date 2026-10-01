"""Viewer commands must never cross identity or create provider sessions."""

from __future__ import annotations

import copy
import json
import unittest

from rofi_agent_plus.contract_backend import CommandOutput
from rofi_agent_plus.contract_lifecycle import StableReference
from rofi_agent_plus.contract_viewers import (
    ViewerError,
    close_viewer,
    inspect_viewers,
    open_existing_viewer,
)

REVISION = "sha256:" + "a" * 64
REFERENCE = StableReference("snap", REVISION, "tmux-v1:fixture", "$4", 1234, "fixture")
PUBLIC_REF = {
    "hostId": "snap",
    "serverGeneration": "tmux-v1:fixture",
    "sessionId": "$4",
    "createdAt": 1234,
}


def reply(**fields: object) -> dict[str, object]:
    return {"schemaVersion": 1, "ok": True, "meshRevision": REVISION, **fields}


def inspection(**fields: object) -> dict[str, object]:
    return (
        reply(
            sessionRef=PUBLIC_REF,
            status="verified",
            viewers=[{"viewerId": "viewer-v1:one", "windowId": 42}],
            closeSafe=True,
        )
        | fields
    )


class Backend:
    tmux_command = "rofi-tmux-plus"

    def __init__(self, value: object, returncode: int = 0) -> None:
        self.value = value
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def _run(self, arguments: list[str], **_kwargs: object) -> CommandOutput:
        self.calls.append(arguments)
        raw = (
            self.value
            if isinstance(self.value, bytes)
            else (json.dumps(self.value) + "\n").encode()
        )
        return CommandOutput(tuple(arguments), self.returncode, "", "", stdout_bytes=raw)


class ViewerContractTest(unittest.TestCase):
    def test_inspection_and_closure_preserve_reference_and_frozen_viewer(self) -> None:
        backend = Backend(inspection())
        observed = inspect_viewers(
            backend, REFERENCE, required_option=("@codex_thread_id", "native-id")
        )
        self.assertEqual(
            ("verified", True, 42),
            (observed.status, observed.close_safe, observed.viewers[0].window_id),
        )
        arguments = backend.calls[0]
        for option, expected in (
            ("--host", "snap"),
            ("--mesh-revision", REVISION),
            ("--server-generation", REFERENCE.server_generation),
            ("--session-id", "$4"),
            ("--created-at", "1234"),
            ("--expected-name", "fixture"),
            ("--require-option", "@codex_thread_id=native-id"),
        ):
            self.assertEqual(expected, arguments[arguments.index(option) + 1])
        backend.value = reply(
            sessionRef=PUBLIC_REF, viewerId="viewer-v1:one", closed=True, alreadyClosed=False
        )
        self.assertTrue(close_viewer(backend, REFERENCE, observed.viewers[0].viewer_id))
        self.assertEqual("close-viewer", backend.calls[-1][1])
        self.assertEqual("viewer-v1:one", backend.calls[-1][-1])

    def test_resume_uses_strict_existing_reference_open_only(self) -> None:
        descriptor = {
            **PUBLIC_REF,
            "name": "fixture",
            "activityAt": 1234,
            "lastAttachedAt": None,
            "attachedClients": 1,
            "pending": False,
            "windowCount": 1,
            "sessionPath": "/work",
            "currentWindow": "agent",
            "currentPath": "/work",
        }
        backend = Backend(reply(session=descriptor, focused=True, terminalLaunched=False))
        self.assertFalse(open_existing_viewer(backend, REFERENCE))
        self.assertEqual("open", backend.calls[0][1])
        self.assertIn("--verified-viewer", backend.calls[0])
        self.assertNotIn("create", backend.calls[0])
        backend.value = reply(session=descriptor, focused=False, terminalLaunched=True)
        self.assertTrue(open_existing_viewer(backend, REFERENCE))

    def test_invalid_wire_or_identity_stops_the_batch(self) -> None:
        cases = [
            b'{"schemaVersion":1,"schemaVersion":1,"ok":true}\n',
            json.dumps(inspection()).encode(),
            inspection(schemaVersion=True),
            inspection(meshRevision=None),
            inspection(sessionRef={**PUBLIC_REF, "createdAt": True}),
            inspection(sessionRef={**PUBLIC_REF, "serverGeneration": "replaced"}),
            inspection(closeSafe=1),
            inspection(status="none"),
            inspection(viewers=[{"viewerId": "one", "windowId": True}]),
            inspection(viewers=[{"viewerId": "one\n", "windowId": 42}]),
            inspection(viewers=[{"viewerId": "one", "windowId": 42}] * 2),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ViewerError) as caught:
                inspect_viewers(Backend(value), REFERENCE)
            self.assertTrue(caught.exception.stop_batch)

    def test_close_reply_cannot_claim_another_viewer_or_contradict_outcome(self) -> None:
        valid = reply(sessionRef=PUBLIC_REF, viewerId="one", closed=False, alreadyClosed=True)
        self.assertFalse(close_viewer(Backend(valid), REFERENCE, "one"))
        for fields in ({"viewerId": "two"}, {"closed": True}, {"closed": 0}):
            with self.subTest(fields=fields), self.assertRaises(ViewerError) as caught:
                close_viewer(Backend(valid | fields), REFERENCE, "one")
            self.assertTrue(caught.exception.stop_batch)

    def test_target_errors_continue_and_authority_errors_stop(self) -> None:
        failure = {
            "schemaVersion": 1,
            "ok": False,
            "error": {"code": "stale_session", "message": "Changed", "hostId": "snap"},
        }
        for code, stop in (
            ("stale_session", False),
            ("future_target_error", False),
            ("stale_mesh", True),
        ):
            value = copy.deepcopy(failure)
            value["error"]["code"] = code
            with self.subTest(code=code), self.assertRaises(ViewerError) as caught:
                inspect_viewers(Backend(value, 1), REFERENCE)
            self.assertEqual(stop, caught.exception.stop_batch)
        with self.assertRaises(ViewerError) as caught:
            inspect_viewers(Backend(failure, 0), REFERENCE)
        self.assertTrue(caught.exception.stop_batch)


if __name__ == "__main__":
    unittest.main()
