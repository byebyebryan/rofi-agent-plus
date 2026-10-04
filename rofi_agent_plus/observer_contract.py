"""Pure UI projection of the pinned public Agent Observer v2 contract."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_observer.bounded_json import WireError, decode_document
from agent_observer.contract import MAX_BYTES, ContractError, validate_snapshot

_PROVIDERS = frozenset({"codex", "claude"})
_PROVIDER_STAGE = {"codex": "threads", "claude": "claude"}


class ObserverContractError(ValueError):
    """A bounded, content-free public contract failure."""


@dataclass(frozen=True)
class ObserverSnapshot:
    sessions: tuple[dict[str, Any], ...]
    errors: tuple[dict[str, str], ...]
    source_health: str
    incomplete_providers: tuple[str, ...] = ()


def session_sort_key(row: Mapping[str, Any]) -> tuple[object, ...]:
    """Attention first, then actual conversation activity, then full identity."""
    phase = row.get("phase")
    value = (
        phase.get("value", "unknown")
        if isinstance(phase, Mapping) and phase.get("health") == "current"
        else "unknown"
    )
    at = row.get("recencyAt")
    at = at if type(at) in {int, float} else None
    return (
        {"blocked": 0, "waiting": 1, "working": 2}.get(str(value), 3),
        at is None,
        -(at or 0),
        str(row.get("hostId") or "").casefold(),
        str(row.get("kind") or ""),
        str(row.get("namespace") or ""),
        str(row.get("nativeIdKind") or ""),
        str(row.get("id") or ""),
    )


def retain_row(row: dict[str, Any]) -> None:
    """Retain metadata through a missed inventory without renewing any clock."""
    row["sourceObservation"] = "retained"
    row["active"] = None
    row["activityState"] = "unknown"
    for dimension in ("phase", "runtime", "worker", "attachment", "outcome"):
        fact = row.get(dimension)
        if isinstance(fact, Mapping):
            stale = dict(fact)
            if stale.get("value") != "unknown":
                stale["lastKnownValue"] = stale["value"]
            stale.update(value="unknown", health="stale", reason="observation_gap")
            row[dimension] = stale
    activity = row.get("activity")
    if isinstance(activity, Mapping):
        row["activity"] = {**activity, "health": "stale", "reason": "observation_gap"}


def _project(row: dict[str, Any], source: dict[str, Any], collected_at: int) -> dict[str, Any]:
    identity = row["identity"]
    phase, runtime = row["phase"], row["runtime"]
    activity_at = row["activity"]["at"]
    active = (
        {"running": True, "parked": False}.get(runtime["value"])
        if runtime["health"] == "current"
        else None
    )
    result = copy.deepcopy(row)
    result.update(
        kind=identity["provider"],
        threadKind=row["kind"],
        id=identity["nativeId"],
        namespace=identity["namespace"],
        nativeIdKind=identity["nativeIdKind"],
        observerIdentity=copy.deepcopy(identity),
        observerSource={**copy.deepcopy(source), "schemaVersion": 2, "collectedAt": collected_at},
        sourceObservation="current"
        if source["sourceHealth"] in {"current", "partial"}
        else "retained",
        name=row["title"],
        active=active,
        activityState=phase["value"] if phase["health"] == "current" else "unknown",
        recencyAt=activity_at / 1000 if activity_at is not None else None,
    )
    if result["sourceObservation"] == "retained":
        retain_row(result)
    return result


def parse_snapshot(
    raw: bytes,
    *,
    expected_host_scope: str,
    expected_providers: tuple[str, ...] = ("codex", "claude"),
    limit: int = MAX_BYTES,
) -> ObserverSnapshot:
    """Validate public data, then apply only consumer presentation policy."""
    if (
        not isinstance(expected_host_scope, str)
        or not expected_host_scope
        or not isinstance(expected_providers, tuple)
        or not expected_providers
        or any(not isinstance(provider, str) for provider in expected_providers)
        or len(set(expected_providers)) != len(expected_providers)
        or not set(expected_providers) <= _PROVIDERS
    ):
        raise ObserverContractError("Agent Observer consumer scope is invalid")
    try:
        payload = validate_snapshot(decode_document(raw, max_bytes=min(limit, MAX_BYTES)))
    except (ContractError, WireError, TypeError, ValueError) as error:
        raise ObserverContractError(
            "Agent Observer public snapshot is invalid or unsupported"
        ) from error
    if payload["host"]["authority"] != expected_host_scope:
        raise ObserverContractError("Agent Observer host provenance does not match Host Mesh")
    sources: dict[str, dict[str, Any]] = {}
    for source in payload["sources"]:
        provider = source["provider"]
        if provider not in expected_providers or provider in sources:
            raise ObserverContractError("Agent Observer provider selection does not match request")
        sources[provider] = source
    errors: list[dict[str, str]] = []
    incomplete: list[str] = []
    for provider in expected_providers:
        source = sources.get(provider)
        if source is None:
            errors.append(
                {
                    "host": expected_host_scope,
                    "stage": _PROVIDER_STAGE[provider],
                    "message": "Observer source unavailable",
                }
            )
            incomplete.append(provider)
            continue
        codes = source["errors"]
        history = [code for code in codes if code.startswith("history_")]
        live = [code for code in codes if code not in history]
        if history:
            errors.append(
                {
                    "host": expected_host_scope,
                    "stage": "claude-history",
                    "message": "Observer history metadata warning (" + ", ".join(history[:8]) + ")",
                }
            )
        if source["sourceHealth"] not in {"current", "partial"} or live:
            reason = "Observer source " + source["sourceHealth"]
            if live:
                reason += " (" + ", ".join(live[:8]) + ")"
            errors.append(
                {"host": expected_host_scope, "stage": _PROVIDER_STAGE[provider], "message": reason}
            )
        if source["sourceHealth"] not in {"current", "partial"} or any(
            coverage["status"] != "complete" for coverage in source["coverage"].values()
        ):
            incomplete.append(provider)
    if payload["errors"]:
        errors.append(
            {
                "host": expected_host_scope,
                "stage": "observer",
                "message": "Observer metadata warning (" + ", ".join(payload["errors"][:8]) + ")",
            }
        )
    rows = tuple(
        _project(row, sources[row["identity"]["provider"]], payload["collectedAt"])
        for row in payload["sessions"]
    )
    return ObserverSnapshot(rows, tuple(errors), payload["sourceHealth"], tuple(incomplete))
