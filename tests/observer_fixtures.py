"""Metadata-only variants of the independently accepted public fixtures."""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "observer-v2"
THREAD = "11111111-1111-1111-1111-111111111111"
CLAUDE = "33333333-3333-4333-8333-333333333333"


def fixture(name="snapshot.json"):
    return json.loads((ROOT / name).read_text())


def native_row(host="alpha", provider="codex", *, phase="waiting"):
    raw = fixture()
    row = copy.deepcopy(
        next(row for row in raw["sessions"] if row["identity"]["provider"] == provider)
    )
    identifier = THREAD if provider == "codex" else CLAUDE
    row["identity"].update(hostScope=host, nativeId=identifier)
    row["nativeIds"].update(
        sessionId="22222222-2222-4222-8222-222222222222" if provider == "codex" else identifier,
        threadId=identifier if provider == "codex" else None,
        jobId=None if provider == "codex" else "abcdef01",
    )
    row["kind"] = "user" if provider == "codex" else "unknown"
    row["phase"].update(value=phase)
    row["blockedReason"] = "approval" if phase == "blocked" else "unknown"
    row.update(title="Synthetic provider title", cwd="/work/synthetic")
    if provider == "claude":
        row["job"]["id"] = "abcdef01"
    return row


def document(host="alpha", *, sessions=None, unavailable=(), providers=("codex", "claude")):
    raw = fixture()
    raw["host"]["authority"] = host
    raw["sources"] = [source for source in raw["sources"] if source["provider"] in providers]
    raw["sessions"] = (
        sessions
        if sessions is not None
        else [native_row(host, provider) for provider in providers if provider not in unavailable]
    )
    for source in raw["sources"]:
        if source["provider"] in unavailable:
            source.update(runtime=None, sourceHealth="unavailable", errors=["source_unavailable"])
            for coverage in source["coverage"].values():
                coverage.update(status="unavailable", reason="source_failed")
    raw["sourceHealth"] = "partial" if unavailable else "current"
    return raw


def plan_for(request):
    plan = fixture("write-plan.json")
    plan["request"] = copy.deepcopy(request)
    plan["route"] = {
        ("codex", "new"): "codex_new",
        ("codex", "resume"): "codex_resume",
        ("claude", "new"): "claude_new",
        ("claude", "resume"): "claude_attach",
    }[(request["provider"], request["operation"])]
    return plan


def result_for(request):
    return {
        "schemaVersion": 1,
        "operation": request["operation"],
        "status": "prepared",
        "reason": "native_tty_entry",
        "effect": "none",
        "requestedIdentity": copy.deepcopy(request["reference"]),
        "resultingIdentity": copy.deepcopy(request["reference"]),
        "identityPending": request["reference"] is None,
        "handoff": plan_for(request),
    }
