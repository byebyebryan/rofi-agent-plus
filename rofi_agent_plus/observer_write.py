"""Pure public write-client request/result boundary; no provider operations."""

from __future__ import annotations

import json
from collections.abc import Mapping

from agent_observer.bounded_json import WireError, decode_document
from agent_observer.contract import ContractError, store_namespace
from agent_observer.write_contract import validate_plan, validate_request, validate_result

MAX_WRITE_BYTES = 32 * 1024


class WriteBoundaryError(ValueError):
    """A bounded machine-contract failure without payload diagnostics."""


def encoded(value: object) -> bytes:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(data) > MAX_WRITE_BYTES:
        raise WriteBoundaryError("Observer write document exceeds its bound")
    return data


def request_for(row: Mapping[str, object], operation: str) -> dict[str, object]:
    source = row.get("observerSource")
    if (
        not isinstance(source, Mapping)
        or source.get("schemaVersion") != 2
        or source.get("sourceHealth") not in {"current", "partial"}
        or source.get("provider") != row.get("kind")
        or source.get("namespace") != row.get("namespace")
        or row.get("sourceObservation") != "current"
    ):
        raise WriteBoundaryError("Observer v2 action context is unavailable")
    reference = row.get("observerIdentity") if operation == "resume" else None
    if operation == "resume" and (
        not isinstance(reference, Mapping)
        or any(
            reference.get(public) != row.get(internal)
            for public, internal in (
                ("hostScope", "hostId"),
                ("provider", "kind"),
                ("namespace", "namespace"),
                ("nativeIdKind", "nativeIdKind"),
                ("nativeId", "id"),
            )
        )
    ):
        raise WriteBoundaryError("Observer selected reference is inconsistent")
    request = {
        "schemaVersion": 1,
        "operation": operation,
        "hostScope": row.get("hostId"),
        "provider": row.get("kind"),
        "configHome": source.get("configHome"),
        "configHomeKind": source.get("configHomeKind"),
        "cwd": row.get("cwd"),
        "reference": reference,
    }
    try:
        validate_request(request)
    except ContractError as error:
        raise WriteBoundaryError("Observer write context is invalid") from error
    return request


def prepared(raw: bytes, request: dict[str, object]) -> dict[str, object]:
    try:
        plan = validate_plan(decode_document(raw, max_bytes=MAX_WRITE_BYTES))
    except (ContractError, WireError) as error:
        raise WriteBoundaryError("Observer write plan is invalid") from error
    routes = {
        ("new", "codex"): {"codex_new"},
        ("new", "claude"): {"claude_new"},
        ("resume", "codex"): {"codex_resume"},
        ("resume", "claude"): {"claude_attach", "claude_saved_resume"},
    }
    if (
        plan["request"] != request
        or plan["route"] not in routes[(request["operation"], request["provider"])]
    ):
        raise WriteBoundaryError("Observer write plan changed the requested context")
    return plan


def completed(raw: bytes, plan: dict[str, object]) -> dict[str, object]:
    try:
        result = validate_result(decode_document(raw, max_bytes=MAX_WRITE_BYTES))
    except (ContractError, WireError) as error:
        raise WriteBoundaryError("Observer write result is invalid") from error
    request = plan["request"]
    if (
        result["operation"] != request["operation"]
        or result["requestedIdentity"] != request["reference"]
    ):
        raise WriteBoundaryError("Observer write result changed the requested identity")
    actual = result["resultingIdentity"]
    namespace = store_namespace(
        request["provider"], request["configHome"], request["configHomeKind"], plan["guard"]["uid"]
    )
    if actual is not None and (
        actual["hostScope"] != request["hostScope"]
        or actual["provider"] != request["provider"]
        or actual["namespace"] != namespace
        or actual["nativeIdKind"] != {"codex": "thread", "claude": "session"}[request["provider"]]
    ):
        raise WriteBoundaryError("Observer write result belongs to another scope")
    handoff = result["handoff"]
    if handoff is not None:
        target = handoff["request"]
        if any(
            target[field] != request[field]
            for field in ("hostScope", "provider", "configHome", "configHomeKind", "cwd")
        ):
            raise WriteBoundaryError("Observer handoff changed the requested context")
        if result["status"] == "prepared" and handoff != plan:
            # Revalidation may change only the preparation timestamp.
            if {key: value for key, value in handoff.items() if key != "createdAt"} != {
                key: value for key, value in plan.items() if key != "createdAt"
            }:
                raise WriteBoundaryError("Observer prepared handoff changed its guard")
        if result["status"] == "ready" and (
            target["operation"] != "resume" or target["reference"] != actual
        ):
            raise WriteBoundaryError("Observer confirmed handoff lost its actual identity")
    return result


def rejection_code(raw: bytes) -> str | None:
    """Parse the public finite stderr error, discarding arbitrary diagnostics."""
    import re

    try:
        value = decode_document(raw, max_bytes=MAX_WRITE_BYTES)
    except WireError:
        return None
    if (
        isinstance(value, dict)
        and set(value) == {"schemaVersion", "error"}
        and type(value["schemaVersion"]) is int
        and value["schemaVersion"] == 1
        and isinstance(value["error"], str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value["error"], re.ASCII)
    ):
        return value["error"]
    return None
