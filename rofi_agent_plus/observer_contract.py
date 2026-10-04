"""Bounded consumer validation for the provisional Agent Observer command.

The adapter accepts one allowlisted host snapshot document. It does not start
providers, infer a terminal association, or treat observer metadata as action
authority.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .wire import WireError, decode_document, validate_string_bounds

_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z", re.ASCII)
_SHA256 = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_JOB_ID = re.compile(r"[a-f0-9]{8}\Z", re.ASCII)
_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z", re.ASCII)
_PROVIDERS = frozenset({"codex", "claude"})
_HEALTH = frozenset({"current", "stale", "unavailable", "unsupported", "ambiguous"})
_REASONS = frozenset(
    {
        "unobserved",
        "native_snapshot",
        "native_event",
        "source_unavailable",
        "observation_gap",
        "unsupported",
        "identity_ambiguous",
        "synthetic",
    }
)
_EVIDENCE_SOURCES = frozenset(
    {"codex_rpc", "claude_registry", "claude_roster", "claude_job_store", "synthetic"}
)
_WORK_VALUES = frozenset({"working", "needs_input", "settled", "interrupted", "error", "unknown"})
_PRESENCE_VALUES = frozenset({"present", "absent", "unknown"})
_ATTACHMENT_VALUES = frozenset({"attached", "detached", "unknown"})
_WAIT_REASONS = frozenset(
    {"approval", "user_input", "worker_request", "sandbox_request", "multiple", "unknown"}
)
_MAX_BYTES = 512 * 1024
_MAX_ROWS = 8192
_MAX_NODES = 100_000
_MAX_TIME_MS = 4_000_000_000_000_000
_MAX_CODEX_HISTORY_TIME_MS = 4_000_000_000_000
_PROVIDER_STAGE = {"codex": "threads", "claude": "claude"}
_CLAUDE_HISTORY_ERRORS = frozenset(
    {
        "history_sdk_unavailable",
        "history_source_failed",
        "history_timeout",
        "history_limit",
        "history_unsafe_entry",
        "history_ambiguous",
    }
)
_COVERAGE_KEYS = frozenset(
    {
        "saved",
        "loaded",
        "work",
        "workerPresence",
        "clientBinding",
        "sessionRegistry",
        "jobStore",
        "attachment",
    }
)
_COVERAGE_STATUSES = frozenset({"complete", "partial", "unavailable", "unsupported"})
_COVERAGE_REASONS = frozenset(
    {
        "not_observed",
        "native_snapshot",
        "history_limit",
        "metadata_scan",
        "live_limit",
        "source_failed",
        "native_proof",
        "native_transition_proof_pending",
        "source_not_established",
    }
)
_COVERAGE_VALUE_LISTS = {
    "supportedValues": _WORK_VALUES,
    "pendingValues": _WORK_VALUES,
    "supportedWaitFlags": frozenset({"waitingOnApproval", "waitingOnUserInput"}),
    "pendingWaitFlags": frozenset({"waitingOnApproval", "waitingOnUserInput"}),
    "supportedWaitReasons": _WAIT_REASONS,
    "pendingWaitReasons": _WAIT_REASONS,
}


class ObserverContractError(ValueError):
    """A finite, content-free Observer envelope or metadata failure."""


@dataclass(frozen=True)
class ObserverSnapshot:
    """Validated rows and independent provider-stage failures."""

    sessions: tuple[dict[str, Any], ...]
    errors: tuple[dict[str, str], ...]
    source_health: str


def _fail() -> None:
    raise ObserverContractError("Agent Observer snapshot returned invalid data")


def _text(value: object, *, limit: int = 4096, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not empty and not value)
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        _fail()
    return value


def _integer(value: object, *, maximum: int = _MAX_TIME_MS, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _fail()
    return value


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail()
    return value


def _keys(value: Mapping[str, object], required: set[str], allowed: set[str] | None = None) -> None:
    if not required.issubset(value) or (allowed is not None and not set(value).issubset(allowed)):
        _fail()


def _tree(value: object, *, depth: int = 0, budget: list[int] | None = None) -> None:
    """Bound the entire decoded document, including discarded extensions."""

    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > _MAX_NODES or depth > 32:
        _fail()
    if isinstance(value, str):
        _text(value, empty=True)
    elif isinstance(value, Mapping):
        if len(value) > 1024:
            _fail()
        for key, child in value.items():
            _text(key, empty=True)
            _tree(child, depth=depth + 1, budget=budget)
    elif isinstance(value, list):
        if len(value) > _MAX_ROWS:
            _fail()
        for child in value:
            _tree(child, depth=depth + 1, budget=budget)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int):
        if not -(2**63) <= value <= 2**63 - 1:
            _fail()
    elif isinstance(value, float):
        if not math.isfinite(value) or not 0 <= value <= 1_000_000:
            _fail()
    else:
        _fail()


def _code(value: object) -> str:
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        _fail()
    return value


def _enum(value: object, values: frozenset[str] | set[str]) -> bool:
    return isinstance(value, str) and value in values


def _errors(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 128:
        _fail()
    result: list[str] = []
    for item in value:
        error = _object(item)
        _keys(error, {"code"}, {"code", "jobId"})
        result.append(_code(error["code"]))
        if "jobId" in error and error["jobId"] is not None:
            job_id = _text(error["jobId"], limit=8)
            if not _JOB_ID.fullmatch(job_id):
                _fail()
    return result


def _coverage(value: object) -> dict[str, object]:
    """Keep only source-supported capability codes and booleans."""

    coverage = _object(value)
    if len(coverage) > 16 or not set(coverage).issubset(_COVERAGE_KEYS):
        _fail()
    result: dict[str, object] = {}
    for key, item in coverage.items():
        if isinstance(item, str):
            if item not in _COVERAGE_STATUSES:
                _fail()
            result[key] = item
            continue
        if isinstance(item, bool):
            result[key] = item
            continue
        metadata = _object(item)
        allowed = {
            "complete",
            "supported",
            "reason",
            "supportedValues",
            "supportedWaitFlags",
            "supportedWaitReasons",
            "pendingValues",
            "pendingWaitFlags",
            "pendingWaitReasons",
        }
        if not set(metadata).issubset(allowed):
            _fail()
        clean: dict[str, object] = {}
        for field, field_value in metadata.items():
            if field in {"complete", "supported"}:
                if not isinstance(field_value, bool):
                    _fail()
                clean[field] = field_value
            elif field == "reason":
                if not isinstance(field_value, str) or field_value not in _COVERAGE_REASONS:
                    _fail()
                clean[field] = field_value
            else:
                allowed_values = _COVERAGE_VALUE_LISTS.get(field)
                if (
                    allowed_values is None
                    or not isinstance(field_value, list)
                    or len(field_value) > 16
                    or any(not _enum(code, allowed_values) for code in field_value)
                    or len(set(field_value)) != len(field_value)
                ):
                    _fail()
                clean[field] = list(field_value)
        result[key] = clean
    return result


def _provider_source(value: object) -> tuple[str, dict[str, Any] | None, str]:
    source = _object(value)
    required = {
        "schemaVersion",
        "collectionId",
        "collectedAt",
        "provider",
        "configHome",
        "coverage",
        "errors",
        "limitations",
        "sourceHealth",
        "durationMs",
    }
    _keys(
        source, required, required | {"namespace", "runtime", "ignoredMessages", "configHomeKind"}
    )
    if type(source["schemaVersion"]) is not int or source["schemaVersion"] != 1:
        _fail()
    provider = source["provider"]
    if not isinstance(provider, str) or provider not in _PROVIDERS:
        _fail()
    config_home_kind = source.get("configHomeKind", "unknown")
    if "configHomeKind" in source and (
        provider != "claude" or not _enum(config_home_kind, {"default", "explicit"})
    ):
        _fail()
    collection_id = _text(source["collectionId"], limit=36)
    if not _UUID.fullmatch(collection_id):
        _fail()
    collected_at = _integer(source["collectedAt"])
    assert collected_at is not None
    config_home = _text(source["configHome"])
    if not config_home.startswith("/") or config_home.startswith("//"):
        _fail()
    coverage = _coverage(source["coverage"])
    limitations = source["limitations"]
    if not isinstance(limitations, list) or len(limitations) > 64:
        _fail()
    for limitation in limitations:
        _code(limitation)
    source_health = source["sourceHealth"]
    if not _enum(source_health, {"current", "stale", "unavailable"}):
        _fail()
    duration = source["durationMs"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        _fail()
    if not math.isfinite(float(duration)) or not 0 <= duration <= 1_000_000:
        _fail()
    errors = _errors(source["errors"])
    if provider == "claude" and any(
        code.startswith("history_") and code not in _CLAUDE_HISTORY_ERRORS for code in errors
    ):
        _fail()
    if provider == "claude":
        saved_coverage = coverage.get("saved")
        if (
            isinstance(saved_coverage, Mapping)
            and saved_coverage.get("reason") == "metadata_scan"
            and saved_coverage.get("complete") is not False
        ):
            _fail()
    if "ignoredMessages" in source:
        _integer(source["ignoredMessages"], maximum=1_000_000)

    runtime_allowlists = {
        "codex": {
            "version",
            "versionEvidence",
            "binarySha256",
            "pid",
            "startTicks",
            "bootId",
            "topology",
            "endpoint",
        },
        "claude": {"version", "versionEvidence", "binarySha256", "bootId", "topology"},
    }
    has_namespace = "namespace" in source
    has_runtime = "runtime" in source
    if has_namespace != has_runtime:
        _fail()
    observer_source: dict[str, Any] | None = None
    if has_runtime:
        namespace = _text(source["namespace"])
        runtime = _object(source["runtime"])
        _keys(runtime, runtime_allowlists[provider], runtime_allowlists[provider])
        for key in ("version", "versionEvidence", "topology"):
            _text(runtime[key], limit=256)
        if runtime["versionEvidence"] not in {
            "verified_executable_sha256",
            "verified_installed_executable_sha256",
        }:
            _fail()
        digest = _text(runtime["binarySha256"], limit=64)
        if not _SHA256.fullmatch(digest):
            _fail()
        boot_id = _text(runtime["bootId"], limit=36)
        if not _UUID.fullmatch(boot_id):
            _fail()
        topology = {
            "codex": "native_managed_endpoint",
            "claude": "private_session_registry_and_job_store",
        }[provider]
        if runtime["topology"] != topology:
            _fail()
        if provider == "codex":
            _integer(runtime["pid"], maximum=2**31 - 1)
            _integer(runtime["startTicks"])
            _text(runtime["endpoint"])
        elif set(runtime) != runtime_allowlists["claude"]:
            _fail()
        # Expose only action-relevant runtime identity. Worker PIDs, endpoint
        # paths, and boot details are not consumer authority.
        observer_source = {
            "schemaVersion": 1,
            "provider": provider,
            "namespace": namespace,
            "configHome": config_home,
            "runtime": {
                "version": runtime["version"],
                "binarySha256": digest,
                "topology": topology,
            },
            "coverage": coverage,
            "collectionId": collection_id,
            "collectedAt": collected_at,
            "sourceHealth": source_health,
        }
        if provider == "claude":
            observer_source["configHomeKind"] = config_home_kind
    elif source_health == "current":
        _fail()
    return provider, observer_source, source_health


def _evidence(value: object, dimension: str) -> dict[str, object]:
    evidence = _object(value)
    _keys(
        evidence,
        {"value", "observedAt", "source", "health", "reason"},
        {
            "value",
            "observedAt",
            "source",
            "health",
            "reason",
            "lastKnownValue",
        },
    )
    values = {
        "work": _WORK_VALUES,
        "presence": _PRESENCE_VALUES,
        "attachment": _ATTACHMENT_VALUES,
    }[dimension]
    current = evidence["value"]
    health = evidence["health"]
    reason = evidence["reason"]
    source = evidence["source"]
    if not _enum(current, values) or not _enum(health, _HEALTH) or not _enum(reason, _REASONS):
        _fail()
    if source is not None and not _enum(source, _EVIDENCE_SOURCES):
        _fail()
    observed_at = _integer(evidence["observedAt"], nullable=True)
    if current != "unknown" and (health != "current" or source is None or observed_at is None):
        _fail()
    if "lastKnownValue" in evidence:
        last_known = evidence["lastKnownValue"]
        if current != "unknown" or health == "current" or not _enum(last_known, values):
            _fail()
    return dict(evidence)


def _identity(value: object, expected_host: str, provider: str, namespace: str) -> dict[str, str]:
    identity = _object(value)
    required = {"hostScope", "provider", "namespace", "nativeIdKind", "nativeId"}
    if set(identity) != required:
        _fail()
    host_scope = _text(identity["hostScope"], limit=256)
    row_provider = identity["provider"]
    row_namespace = _text(identity["namespace"])
    id_kind = identity["nativeIdKind"]
    native_id = _text(identity["nativeId"], limit=256)
    expected_id_kind = "thread" if provider == "codex" else "session"
    if (
        host_scope != expected_host
        or row_provider != provider
        or row_namespace != namespace
        or id_kind != expected_id_kind
    ):
        _fail()
    return {
        "hostScope": host_scope,
        "provider": provider,
        "namespace": row_namespace,
        "nativeIdKind": expected_id_kind,
        "nativeId": native_id,
    }


def _native_ids(value: object, provider: str, native_id: str) -> dict[str, object]:
    identifiers = _object(value)
    if provider == "codex":
        if set(identifiers) != {"threadId", "sessionId"}:
            _fail()
        thread_id = _text(identifiers["threadId"], limit=36)
        session_id = _text(identifiers["sessionId"], limit=36)
        if (
            not _UUID.fullmatch(thread_id)
            or not _UUID.fullmatch(session_id)
            or thread_id != native_id
        ):
            _fail()
        return {"threadId": thread_id, "sessionId": session_id}
    if set(identifiers) != {"sessionId", "jobId"}:
        _fail()
    session_id = _text(identifiers["sessionId"], limit=36)
    job_id = identifiers["jobId"]
    if not _UUID.fullmatch(session_id) or session_id != native_id:
        _fail()
    if job_id is not None:
        job_id = _text(job_id, limit=8)
        if not _JOB_ID.fullmatch(job_id):
            _fail()
    return {"sessionId": session_id, "jobId": job_id}


def _row_optional_fields(value: Mapping[str, object], provider: str) -> dict[str, object]:
    result: dict[str, object] = {}
    inventory_values = (
        {"live", "saved"}
        if provider == "codex"
        else {
            "live",
            "retained_job",
            "registry",
            "saved",
        }
    )
    if "inventory" in value:
        if not _enum(value["inventory"], inventory_values):
            _fail()
        result["inventory"] = value["inventory"]
    if "presenceKind" in value:
        presence_kind = value["presenceKind"]
        allowed_presence = {"server_thread_loaded"} if provider == "codex" else {"os_worker"}
        if not _enum(presence_kind, allowed_presence):
            _fail()
        result["presenceKind"] = presence_kind
    if "waitReason" in value:
        wait_reason = value["waitReason"]
        if not _enum(wait_reason, _WAIT_REASONS):
            _fail()
        result["waitReason"] = wait_reason
    issues = value.get("metadataIssues", [])
    if not isinstance(issues, list) or len(issues) > 64:
        _fail()
    result["metadataIssues"] = [_code(issue) for issue in issues]

    if provider == "codex":
        if "nativeHistoryTimes" not in value:
            created_at_ms = updated_at_ms = None
        else:
            history = _object(value["nativeHistoryTimes"])
            if set(history) != {"createdAt", "updatedAt"}:
                _fail()
            created_at_ms = _integer(
                history["createdAt"], maximum=_MAX_CODEX_HISTORY_TIME_MS, nullable=True
            )
            updated_at_ms = _integer(
                history["updatedAt"], maximum=_MAX_CODEX_HISTORY_TIME_MS, nullable=True
            )
            if any(
                timestamp is not None and timestamp % 1000
                for timestamp in (created_at_ms, updated_at_ms)
            ):
                _fail()
            unavailable = "native_history_time_unavailable" in result["metadataIssues"]
            if (created_at_ms is None or updated_at_ms is None) != unavailable:
                _fail()
        result["createdAt"] = created_at_ms // 1000 if created_at_ms is not None else None
        result["updatedAt"] = updated_at_ms // 1000 if updated_at_ms is not None else None
        # A missing update clock remains unknown; creation time is available
        # separately but is not a guess at the row's current recency.
        result["recencyAt"] = result["updatedAt"]
        if "runtimeVersion" in value:
            result["runtimeVersion"] = _text(value["runtimeVersion"], limit=128)
        source_kind = None
        if "sourceKind" in value:
            source_kind = value["sourceKind"]
            if not _enum(
                source_kind, {"cli", "vscode", "exec", "appServer", "subagent", "unknown"}
            ):
                _fail()
            result["sourceKind"] = source_kind
        thread_kind = None
        if "threadKind" in value:
            thread_kind = value["threadKind"]
            if not _enum(thread_kind, {"user", "child", "unknown"}):
                _fail()
            result["threadKind"] = thread_kind
        if (thread_kind == "child" and source_kind is None) or (
            source_kind == "subagent" and thread_kind not in {"child", "unknown"}
        ):
            _fail()
        if "nativeState" in value:
            native_state = _object(value["nativeState"])
            if set(native_state) != {"type", "activeFlags"}:
                _fail()
            state_type = native_state["type"]
            flags = native_state["activeFlags"]
            if not _enum(state_type, {"notLoaded", "idle", "active", "systemError"}):
                _fail()
            if (
                not isinstance(flags, list)
                or len(flags) > 8
                or any(
                    not _enum(flag, {"waitingOnApproval", "waitingOnUserInput"}) for flag in flags
                )
                or len(set(flags)) != len(flags)
            ):
                _fail()
            result["nativeState"] = {"type": state_type, "activeFlags": list(flags)}
    else:
        if "sessionKind" in value:
            session_kind = value["sessionKind"]
            if not _enum(
                session_kind,
                {"interactive", "bg", "daemon", "daemon-worker", "unknown"},
            ):
                _fail()
            result["sessionKind"] = session_kind
        if "nativeStatus" in value:
            status = _object(value["nativeStatus"])
            if set(status) != {"value", "observedAt"}:
                _fail()
            status_value = status["value"]
            if not _enum(status_value, {"busy", "shell", "idle", "waiting", "unknown"}):
                _fail()
            result["nativeStatus"] = {
                "value": status_value,
                "observedAt": _integer(status["observedAt"], nullable=True),
            }
        if "cwdSource" in value:
            cwd_source = value["cwdSource"]
            if cwd_source is not None and not _enum(
                cwd_source, {"claude_registry", "claude_job_store", "claude_history"}
            ):
                _fail()
            result["cwdSource"] = cwd_source
        if "history" in value:
            history = _object(value["history"])
            if set(history) != {"source", "sdkVersion", "createdAt", "fileModifiedAt"}:
                _fail()
            if _text(history["source"], limit=32) != "claude_sdk":
                _fail()
            if _text(history["sdkVersion"], limit=32) != "0.2.163":
                _fail()
            created_at = _integer(
                history["createdAt"], maximum=_MAX_CODEX_HISTORY_TIME_MS, nullable=True
            )
            file_modified_at = _integer(
                history["fileModifiedAt"], maximum=_MAX_CODEX_HISTORY_TIME_MS, nullable=True
            )
            result["history"] = {
                "source": "claude_sdk",
                "sdkVersion": "0.2.163",
                "createdAt": created_at,
                "fileModifiedAt": file_modified_at,
            }
        if "job" in value:
            if value["job"] is None:
                result["job"] = None
                return result
            job = _object(value["job"])
            required_job = {"id", "retained", "state", "tempo", "terminalObservedAt"}
            if set(job) != required_job:
                _fail()
            job_id = job["id"]
            if job_id is not None:
                job_id = _text(job_id, limit=8)
                if not _JOB_ID.fullmatch(job_id):
                    _fail()
            retained = job["retained"]
            if retained is not None and not isinstance(retained, bool):
                _fail()
            state = job["state"]
            tempo = job["tempo"]
            if not _enum(state, {"working", "done", "failed", "stopped", "unknown"}):
                _fail()
            if not _enum(tempo, {"active", "idle", "blocked", "unknown"}):
                _fail()
            terminal_at = _integer(job["terminalObservedAt"], nullable=True)
            result["job"] = {
                "id": job_id,
                "retained": retained,
                "state": state,
                "tempo": tempo,
                "terminalObservedAt": terminal_at,
            }
    return result


def _session(
    value: object,
    *,
    expected_host: str,
    provider: str,
    namespace: str,
    observer_source: Mapping[str, object],
) -> dict[str, Any]:
    row = _object(value)
    common = {
        "identity",
        "work",
        "presence",
        "attachment",
        "nativeIds",
        "title",
        "cwd",
        "inventory",
        "presenceKind",
        "waitReason",
        "metadataIssues",
    }
    provider_fields = (
        {"runtimeVersion", "sourceKind", "threadKind", "nativeState", "nativeHistoryTimes"}
        if provider == "codex"
        else {"sessionKind", "nativeStatus", "job", "cwdSource", "history"}
    )
    required_fields = {"identity", "work", "presence", "attachment", "nativeIds", "title", "cwd"}
    if not set(row).issubset(common | provider_fields) or not required_fields.issubset(row):
        _fail()
    identity = _identity(row["identity"], expected_host, provider, namespace)
    native_id = identity["nativeId"]
    native_ids = _native_ids(row["nativeIds"], provider, native_id)
    evidence = {key: _evidence(row[key], key) for key in ("work", "presence", "attachment")}
    client_binding = observer_source["coverage"].get("clientBinding")
    if (
        isinstance(client_binding, Mapping)
        and client_binding.get("supported") is False
        and (
            evidence["attachment"]["value"] != "unknown"
            or evidence["attachment"]["health"] != "unsupported"
        )
    ):
        _fail()
    title = _text(row["title"], limit=1024, empty=True)
    cwd = row["cwd"]
    if cwd is not None:
        cwd = _text(cwd)
        if not cwd.startswith("/") or cwd.startswith("//"):
            _fail()
    optional = _row_optional_fields(row, provider)
    if provider == "claude":
        if "job" in optional:
            job = optional["job"]
            if job is None:
                if native_ids["jobId"] is not None:
                    _fail()
            elif native_ids["jobId"] != job["id"]:
                _fail()
        if "history" in optional:
            created_at_ms = optional["history"]["createdAt"]
            optional["createdAt"] = created_at_ms // 1000 if created_at_ms is not None else None
        if optional.get("inventory") == "saved":
            if (
                "history" not in optional
                or optional.get("sessionKind") != "unknown"
                or "job" not in optional
                or optional["job"] is not None
                or native_ids["jobId"] is not None
                or optional.get("cwdSource") != ("claude_history" if cwd is not None else None)
                or ("waitReason" in optional and optional["waitReason"] != "unknown")
                or (
                    "nativeStatus" in optional
                    and (
                        optional["nativeStatus"]["value"] != "unknown"
                        or optional["nativeStatus"]["observedAt"] is not None
                    )
                )
            ):
                _fail()
            for dimension in ("work", "presence"):
                item = evidence[dimension]
                if (
                    item["value"] != "unknown"
                    or item["observedAt"] is not None
                    or item["source"] is not None
                    or item["health"] != "unavailable"
                    or item["reason"] != "unobserved"
                    or "lastKnownValue" in item
                ):
                    _fail()
            optional["recencyAt"] = optional["createdAt"]
        else:
            if "job" in optional and optional["job"] is None:
                _fail()
            work_time = evidence["work"]["observedAt"]
            optional["recencyAt"] = (
                work_time // 1000
                if evidence["work"]["health"] == "current"
                and evidence["work"]["value"] != "unknown"
                and evidence["work"]["source"] in {"claude_registry", "claude_job_store"}
                and work_time is not None
                else None
            )
    work = evidence["work"]
    presence = evidence["presence"]
    work_value = work["value"]
    activity_state = (
        {
            "working": "working",
            "needs_input": "waiting",
            "settled": "idle",
            "interrupted": "interrupted",
            "error": "error",
        }.get(str(work_value), "unknown")
        if work["health"] == "current"
        else "unknown"
    )
    active = None
    if presence["health"] == "current":
        active = presence["value"] == "present" if presence["value"] != "unknown" else None
    display_title = title or native_id
    result: dict[str, Any] = {
        "kind": provider,
        "id": native_id,
        "namespace": namespace,
        "nativeIdKind": identity["nativeIdKind"],
        "observerIdentity": identity,
        "nativeIds": native_ids,
        "observerSource": dict(observer_source),
        "sourceObservation": "current",
        "title": display_title,
        "name": display_title,
        "cwd": cwd,
        "active": active,
        "activityState": activity_state,
        **evidence,
        **optional,
    }
    return result


def parse_snapshot(
    raw: bytes,
    *,
    expected_host_scope: str,
    expected_providers: tuple[str, ...] = ("codex", "claude"),
    limit: int = _MAX_BYTES,
) -> ObserverSnapshot:
    """Decode and project one exact, bounded Observer host snapshot."""

    if (
        not isinstance(expected_host_scope, str)
        or not expected_host_scope
        or len(expected_host_scope) > 256
    ):
        raise ObserverContractError("Agent Observer host scope is invalid")
    if (
        not isinstance(expected_providers, tuple)
        or not 1 <= len(expected_providers) <= len(_PROVIDERS)
        or any(not _enum(provider, _PROVIDERS) for provider in expected_providers)
        or len(set(expected_providers)) != len(expected_providers)
    ):
        raise ObserverContractError("Agent Observer provider selection is invalid")
    requested_providers = frozenset(expected_providers)
    try:
        payload = decode_document(raw, limit=limit)
        _tree(payload)
        validate_string_bounds(payload, limit=4096)
    except WireError as error:
        raise ObserverContractError("Agent Observer snapshot wire data is invalid") from error
    envelope = _object(payload)
    envelope_fields = {
        "schemaVersion",
        "collectionId",
        "collectedAt",
        "host",
        "sources",
        "sessions",
        "errors",
        "limitations",
        "sourceHealth",
        "durationMs",
    }
    if set(envelope) != envelope_fields:
        _fail()
    if type(envelope["schemaVersion"]) is not int or envelope["schemaVersion"] != 1:
        raise ObserverContractError("Agent Observer snapshot schema is unsupported")
    collection_id = _text(envelope["collectionId"], limit=36)
    if not _UUID.fullmatch(collection_id):
        _fail()
    _integer(envelope["collectedAt"])
    host = _object(envelope["host"])
    if set(host) != {"authority", "authoritySource", "nativeHostname", "uid"}:
        _fail()
    if host.get("authority") != expected_host_scope or host.get("authoritySource") != "caller":
        raise ObserverContractError("Agent Observer host provenance does not match Host Mesh")
    _text(host["nativeHostname"], limit=256)
    _integer(host["uid"], maximum=2**31 - 1)
    source_health = envelope["sourceHealth"]
    if not _enum(source_health, {"current", "partial", "unavailable"}):
        _fail()
    duration = envelope["durationMs"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        _fail()
    if not math.isfinite(float(duration)) or not 0 <= duration <= 1_000_000:
        _fail()
    limitations = envelope["limitations"]
    if not isinstance(limitations, list) or len(limitations) > 64:
        _fail()
    for limitation in limitations:
        _code(limitation)
    host_errors = envelope["errors"]
    if not isinstance(host_errors, list) or host_errors:
        # The only current host-level error is duplicate identity. A duplicate
        # is rejected below as well, so any envelope-level error is unsafe.
        _fail()

    sources = envelope["sources"]
    sessions = envelope["sessions"]
    if not isinstance(sources, list) or len(sources) > len(_PROVIDERS):
        _fail()
    if not isinstance(sessions, list) or len(sessions) > _MAX_ROWS:
        _fail()
    source_info: dict[str, tuple[dict[str, Any] | None, str]] = {}
    provider_errors: dict[str, str] = {}
    for item in sources:
        provider, observer_source, provider_health = _provider_source(item)
        if provider not in requested_providers:
            raise ObserverContractError("Agent Observer snapshot contains an unrequested provider")
        if provider in source_info:
            raise ObserverContractError(
                "Agent Observer snapshot contains duplicate provider sources"
            )
        source_info[provider] = (observer_source, provider_health)
        provider_error_codes = _errors(_object(item)["errors"])
        if provider == "claude":
            history_codes = [
                code for code in provider_error_codes if code in _CLAUDE_HISTORY_ERRORS
            ]
            live_codes = [code for code in provider_error_codes if code not in history_codes]
            if history_codes:
                details = ", ".join(history_codes[:8])
                provider_errors["claude-history"] = f"Claude history metadata warning ({details})"
            if provider_health != "current" or live_codes:
                details = ", ".join(live_codes[:8])
                reason = f"Observer source {provider_health}"
                if details:
                    reason += f" ({details})"
                provider_errors[_PROVIDER_STAGE[provider]] = reason
        elif provider_health != "current" or provider_error_codes:
            details = ", ".join(provider_error_codes[:8])
            reason = f"Observer source {provider_health}"
            if details:
                reason += f" ({details})"
            provider_errors[_PROVIDER_STAGE[provider]] = reason
    for provider in requested_providers - source_info.keys():
        provider_errors[_PROVIDER_STAGE[provider]] = "Observer source unavailable"

    seen: set[tuple[str, str, str, str, str]] = set()
    projected: list[dict[str, Any]] = []
    for item in sessions:
        raw_identity = _object(_object(item).get("identity"))
        provider = raw_identity.get("provider")
        if not isinstance(provider, str) or provider not in _PROVIDERS:
            _fail()
        source_entry = source_info.get(provider)
        if source_entry is None:
            _fail()
        observer_source, _provider_health = source_entry
        if observer_source is None:
            _fail()
        namespace = str(observer_source["namespace"])
        row_identity = _identity(raw_identity, expected_host_scope, provider, namespace)
        key = (
            row_identity["hostScope"],
            row_identity["provider"],
            row_identity["namespace"],
            row_identity["nativeIdKind"],
            row_identity["nativeId"],
        )
        if key in seen:
            raise ObserverContractError(
                "Agent Observer snapshot contains duplicate native identities"
            )
        seen.add(key)
        projected.append(
            _session(
                item,
                expected_host=expected_host_scope,
                provider=provider,
                namespace=namespace,
                observer_source=observer_source,
            )
        )

    errors = tuple(
        {"host": expected_host_scope, "stage": stage, "message": message}
        for stage, message in sorted(provider_errors.items())
    )
    return ObserverSnapshot(tuple(projected), errors, source_health)
