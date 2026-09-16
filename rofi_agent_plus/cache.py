"""Stale-while-revalidate cache for discovered agent sessions."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import engine
from .config import PickerConfig

# v4 adds private refresh-observation provenance.  A Host Mesh revision (or
# the explicit local-only ``null`` identity) remains part of cache validity.
# v3 snapshots are upgraded in memory on read and rewritten as v4 only by a
# later legitimate cache mutation.
CACHE_VERSION = 4
_PREVIOUS_CACHE_VERSION = 3
DEFAULT_CACHE_DIR = Path("rofi-agent-plus")
SNAPSHOT_NAME = "snapshot.json"
LOCK_NAME = "refresh.lock"
BACKGROUND_MARKER_NAME = "refresh.background"
LOCK_WAIT_SECONDS = 30.0
_PROVIDER_FOR_STAGE = {"threads": "codex", "claude": "claude", "opencode": "opencode"}
_OBSERVATION_STAGES = ("codex", "claude", "opencode", "activity", "tmux")
_OBSERVATION_STAGE_FOR_ERROR = {
    "threads": "codex",
    "claude": "claude",
    "opencode": "opencode",
    "active": "activity",
    "tmux": "tmux",
}
_OBSERVATION_OUTCOMES = {"ok", "failed", "unknown"}
_SOURCE_OBSERVATIONS = {"current", "retained", "activity-only"}
_ROFI_CALLBACK_ENVIRONMENT = (
    "ROFI_DATA",
    "ROFI_INFO",
    "ROFI_INPUT",
    "ROFI_OUTSIDE",
    "ROFI_RETV",
)
_BACKGROUND_OWNER_ENV = "ROFI_AGENT_PLUS_REFRESH_OWNER"
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z", re.ASCII)


@dataclass(frozen=True)
class PresentationContext:
    """One capability/revision observation reused by a Rofi callback."""

    fingerprint: str
    backend: dict[str, object]
    error: str | None = None
    selected: object | None = None


def _valid_host_catalog(value: object) -> bool:
    """Validate the private ordered Host Mesh catalog in a cache snapshot."""

    if not isinstance(value, list):
        return False
    seen: set[str] = set()
    local_count = 0
    for item in value:
        if not isinstance(item, Mapping):
            return False
        host_id = item.get("hostId")
        display = item.get("display")
        if (
            not isinstance(host_id, str)
            or not host_id
            or len(host_id) > 256
            or not _HOST_ID.fullmatch(host_id)
            or host_id.casefold() in seen
            or not isinstance(display, str)
            or not display
            or len(display) > 16 * 1024
            or any(unicodedata.category(char).startswith("C") for char in display)
            or not isinstance(item.get("local"), bool)
        ):
            return False
        if item["local"]:
            local_count += 1
        seen.add(host_id.casefold())
    # A non-empty Host Mesh always has exactly one local host and the Mesh
    # contract requires it to be first.  Empty catalogs are reserved for the
    # contract-error/no-authority snapshot.
    return not value or (local_count == 1 and value[0].get("local") is True)


def _host_catalog(value: object) -> list[dict[str, object]] | None:
    """Copy a valid internal catalog while preserving Host Mesh order."""

    if not _valid_host_catalog(value):
        return None
    assert isinstance(value, list)
    return [
        {
            "hostId": str(item["hostId"]).casefold(),
            "display": item["display"],
            "local": item["local"],
        }
        for item in value
        if isinstance(item, Mapping)
    ]


def cache_root() -> Path:
    value = os.environ.get("XDG_CACHE_HOME")
    root = Path(value) if value else Path.home() / ".cache"
    return root / DEFAULT_CACHE_DIR


def _safe_mode(path: Path, mode: int) -> None:
    try:
        current = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if current != mode:
        try:
            path.chmod(mode)
        except OSError:
            pass


def _as_session_key(session: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(session.get("hostId") or session.get("windowHost") or session.get("host") or "local"),
        str(session.get("kind") or ""),
        str(session.get("id") or ""),
    )


def _provider_for_session(session: Mapping[str, Any]) -> str:
    kind = str(session.get("kind") or "")
    return kind if kind in {"codex", "claude", "opencode"} else ""


def _timestamp(value: object, default: int = 0) -> int:
    """Return a non-negative integer timestamp without accepting booleans."""

    if isinstance(value, bool):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _stage_observation(value: object) -> dict[str, object]:
    """Copy one private stage observation, treating malformed history as unknown."""

    if not isinstance(value, Mapping):
        return {"lastAttemptAt": 0, "lastSuccessAt": 0, "outcome": "unknown"}
    outcome = value.get("outcome")
    if outcome not in _OBSERVATION_OUTCOMES:
        outcome = "unknown"
    return {
        "lastAttemptAt": _timestamp(value.get("lastAttemptAt")),
        "lastSuccessAt": _timestamp(value.get("lastSuccessAt")),
        "outcome": outcome,
    }


def _host_observations(value: object) -> dict[str, dict[str, object]]:
    """Copy every canonical stage while leaving absent legacy history unknown."""

    raw = value if isinstance(value, Mapping) else {}
    return {stage: _stage_observation(raw.get(stage)) for stage in _OBSERVATION_STAGES}


def _error_stages(errors: object) -> set[str]:
    """Return canonical stages whose host-wide observations failed."""

    if not isinstance(errors, list):
        return set()
    return {
        canonical
        for error in errors
        if isinstance(error, Mapping)
        and isinstance(error.get("stage"), str)
        and (canonical := _OBSERVATION_STAGE_FOR_ERROR.get(error["stage"])) is not None
    }


def _observation_outcome(errors: object) -> str:
    """Classify a completed all-host transaction without counting diagnostics."""

    if isinstance(errors, list) and any(
        isinstance(error, Mapping) and error.get("stage") in {"contract", "refresh"}
        for error in errors
    ):
        # These errors mean no complete authority-scoped transaction reached
        # the cache.  A retained generatedAt belongs to an older success and
        # must not make a v3 migration look complete.
        return "failed"
    if _error_stages(errors):
        return "partial"
    if isinstance(errors, list) and any(
        isinstance(error, Mapping) and error.get("stage") == "tmux-correlation" for error in errors
    ):
        # Correlation uncertainty is row-specific, but the all-host attempt is
        # still not fully evidenced.  It must not downgrade the host-wide
        # ``tmux`` observation itself.
        return "partial"
    return "complete"


def _observations_for_attempt(
    previous: Mapping[str, Any] | None,
    errors: object,
    observed_at: int,
) -> dict[str, dict[str, object]]:
    """Record one completed host attempt while retaining prior success times."""

    prior = _host_observations(previous.get("observations") if previous else None)
    failed = _error_stages(errors)
    observations: dict[str, dict[str, object]] = {}
    for stage in _OBSERVATION_STAGES:
        if stage in failed:
            observations[stage] = {
                "lastAttemptAt": observed_at,
                "lastSuccessAt": prior[stage]["lastSuccessAt"],
                "outcome": "failed",
            }
        else:
            # ``tmux-missing`` is a reached authoritative result, and
            # ``tmux-correlation`` is a row-specific uncertainty.  Neither
            # makes the host-wide inventory stage unavailable.
            observations[stage] = {
                "lastAttemptAt": observed_at,
                "lastSuccessAt": observed_at,
                "outcome": "ok",
            }
    return observations


def _source_from_current_event(value: object) -> str:
    """Accept only the backend's synthetic-row marker from current events."""

    # ``retained`` is cache-owned history, never an event-stream claim.  This
    # prevents a backend or malformed test event from blessing old provider
    # data as retained without the merge preserving its observation history.
    return "activity-only" if value == "activity-only" else "current"


def _merge_host_snapshot(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge failed provider stages into the prior host snapshot.

    A successful provider result replaces its old rows.  Rows belonging to a
    provider that failed remain available until a later successful refresh.
    An activity failure preserves the last known activity fields while using
    current metadata for the row.
    """

    current_sessions = []
    for item in current.get("sessions", []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["sourceObservation"] = _source_from_current_event(row.get("sourceObservation"))
        current_sessions.append(row)
    current_errors = [dict(item) for item in current.get("errors", []) if isinstance(item, dict)]
    failed_stages = {str(error.get("stage")) for error in current_errors if error.get("stage")}
    current_by_key = {_as_session_key(item): item for item in current_sessions}
    tmux_failed = "tmux" in failed_stages

    def retain_tmux(
        old: Mapping[str, Any],
        fresh: dict[str, Any],
        *,
        current_had_tmux: bool,
    ) -> None:
        """Keep old tmux evidence only when the current tmux stage failed.

        An authoritative ``ok``/empty or ``tmux_missing`` result has already
        disproved a fresh old association.  A failed/unreachable/error stage
        may retain it, but only with an explicit stale marker.
        """

        if tmux_failed:
            if isinstance(old.get("tmux"), dict) and "tmux" not in fresh:
                fresh["tmux"] = dict(old["tmux"])
                fresh["tmuxSession"] = old.get("tmuxSession")
            if "tmux" in fresh:
                if fresh.get("providerOptionVerified") is not True:
                    fresh.pop("providerOptionVerified", None)
                fresh["tmuxStale"] = True
            else:
                fresh.pop("providerOptionVerified", None)
            return
        if not current_had_tmux:
            fresh.pop("tmux", None)
            fresh.pop("tmuxSession", None)
            fresh.pop("tmuxStale", None)
            fresh.pop("providerOptionVerified", None)
        elif fresh.get("providerOptionVerified") is not True:
            # The marker is inseparable from an option-backed association.
            # In particular, a provider-stage retention merge can overlay a
            # fresh process-only association on an older option-backed row.
            fresh.pop("providerOptionVerified", None)

    if previous and isinstance(previous.get("sessions"), list):
        for old in previous["sessions"]:
            if not isinstance(old, dict):
                continue
            key = _as_session_key(old)
            provider = _provider_for_session(old)
            # A synthetic activity-only row has no provider-native metadata to
            # retain.  Keeping it through a later provider failure would make
            # an old process probe look like a current session listing.
            preserve = old.get("sourceObservation") != "activity-only" and any(
                _PROVIDER_FOR_STAGE.get(stage) == provider for stage in failed_stages
            )
            fresh = current_by_key.get(key)
            current_had_tmux = fresh is not None and "tmux" in fresh
            if preserve and fresh is not None:
                # The provider's list/details stage failed, but activity is a
                # fresh independent observation.  Start from old provider
                # metadata and overlay only current identity/activity and
                # current tmux evidence; do not replace a useful name/cwd
                # with an active-probe placeholder.
                retained = dict(old)
                for field in (
                    "kind",
                    "id",
                    "host",
                    "hostId",
                    "windowHost",
                    "connectHost",
                    "route",
                    "contractMode",
                    "backend",
                    "active",
                    "activityState",
                ):
                    if field in fresh:
                        retained[field] = fresh[field]
                if "tmux" in fresh:
                    retained["tmux"] = fresh["tmux"]
                    retained["tmuxSession"] = fresh.get("tmuxSession")
                    if fresh.get("providerOptionVerified") is True:
                        retained["providerOptionVerified"] = True
                    else:
                        retained.pop("providerOptionVerified", None)
                fresh.clear()
                fresh.update(retained)
                fresh["sourceObservation"] = "retained"
            elif preserve and fresh is None:
                fresh = dict(old)
                fresh["sourceObservation"] = "retained"
                current_sessions.append(fresh)
                current_by_key[key] = fresh
            if "active" in failed_stages and key in current_by_key:
                fresh = current_by_key[key]
                # Activity is independent process evidence.  It may retain
                # only its own state; an old tmux display name without the
                # matching current nested reference would be false authority.
                activity_fields = ("active", "activityState")
                # Contract rows own tmux authority separately. Older cache
                # records may still carry this display-only field, so do not
                # reinterpret it as a current contract reference.
                if old.get("contractMode") is not True and fresh.get("contractMode") is not True:
                    activity_fields += ("tmuxSession",)
                for field in activity_fields:
                    if field in old:
                        fresh[field] = old[field]
            if key in current_by_key and (
                old.get("contractMode") is True or current_by_key[key].get("contractMode") is True
            ):
                retain_tmux(old, current_by_key[key], current_had_tmux=current_had_tmux)

    current_sessions.sort(
        key=lambda item: (int(item.get("recencyAt") or 0), str(item.get("id") or "")),
        reverse=True,
    )
    result = {
        "generatedAt": int(current.get("generatedAt") or time.time()),
        "sessions": current_sessions,
        "errors": current_errors,
        "observations": _observations_for_attempt(
            previous,
            current_errors,
            _timestamp(current.get("generatedAt"), int(time.time())),
        ),
    }
    return result


def _backend_identity(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    kind = value.get("kind")
    capability = value.get("capability")
    revision = value.get("meshRevision")
    expected_capability = {
        "contract": "host-mesh-v1+tmux-session-v1",
        "contract-error": "host-mesh-v1+tmux-session-v1",
    }.get(kind)
    if expected_capability is None or capability != expected_capability:
        return None
    if revision is not None and (
        not isinstance(revision, str)
        or not revision
        or revision.strip() != revision
        or any(char.isspace() or ord(char) < 32 for char in revision)
    ):
        return None
    if kind == "contract-error" and revision is not None:
        return None
    return {"kind": kind, "capability": capability, "meshRevision": revision}


def _last_refresh(
    attempted_at: int,
    *,
    completed_at: int | None,
    outcome: str,
) -> dict[str, object]:
    return {
        "attemptedAt": attempted_at,
        "completedAt": completed_at,
        "outcome": outcome,
    }


def _valid_stage_observation(value: object) -> bool:
    if not isinstance(value, Mapping) or value.get("outcome") not in _OBSERVATION_OUTCOMES:
        return False
    return all(
        isinstance(value.get(key), int) and not isinstance(value.get(key), bool) and value[key] >= 0
        for key in ("lastAttemptAt", "lastSuccessAt")
    )


def _valid_observations(value: object) -> bool:
    return isinstance(value, Mapping) and all(
        _valid_stage_observation(value.get(stage)) for stage in _OBSERVATION_STAGES
    )


def _valid_last_refresh(value: object) -> bool:
    if not isinstance(value, Mapping) or value.get("outcome") not in {
        "complete",
        "partial",
        "failed",
    }:
        return False
    attempted = value.get("attemptedAt")
    completed = value.get("completedAt")
    if not isinstance(attempted, int) or isinstance(attempted, bool) or attempted < 0:
        return False
    if value["outcome"] == "failed":
        # Failed attempts have no successful transaction completion.  This
        # also keeps the empty, no-authority snapshot (0/None/failed) valid.
        return completed is None
    return isinstance(completed, int) and not isinstance(completed, bool) and completed >= attempted


def _v3_row_source(row: Mapping[str, Any], errors: object) -> str:
    """Recover the only trustworthy v3 row distinction from host errors."""

    provider = _provider_for_session(row)
    failed = _error_stages(errors)
    return "retained" if provider in failed else "current"


def _migrate_v3_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create an in-memory v4 view of a valid v3 private snapshot.

    v3 had no exact per-stage success history.  We recover current provider
    failures from its host errors and leave their prior success timestamp at
    zero instead of inventing a time.  The first normal refresh replaces that
    legacy uncertainty with v4 observations.
    """

    # Snapshots enter this function from JSON or CacheStore.write, so this is
    # also a convenient deep copy that guarantees a read never mutates disk
    # data or caller-owned objects.
    migrated = json.loads(json.dumps(payload, ensure_ascii=False))
    migrated["version"] = CACHE_VERSION
    generated_at = _timestamp(migrated.get("generatedAt"))
    backend = _backend_identity(migrated.get("backend"))
    if backend is None or backend["kind"] != "contract" or generated_at == 0:
        migrated["lastRefresh"] = _last_refresh(0, completed_at=None, outcome="failed")
    else:
        outcome = _observation_outcome(migrated.get("errors"))
        migrated["lastRefresh"] = (
            _last_refresh(0, completed_at=None, outcome="failed")
            if outcome == "failed"
            else _last_refresh(generated_at, completed_at=generated_at, outcome=outcome)
        )
    hosts = migrated.get("hosts")
    if not isinstance(hosts, dict):
        return migrated
    errors_by_host: dict[str, list[object]] = {}
    row_sources: dict[tuple[str, str, str], str] = {}
    for key, host in hosts.items():
        if not isinstance(key, str) or not isinstance(host, dict):
            continue
        host_errors = host.get("errors") if isinstance(host.get("errors"), list) else []
        errors_by_host[key] = host_errors
        observed_at = _timestamp(host.get("generatedAt"), generated_at)
        failed = _error_stages(host_errors)
        host["observations"] = {
            stage: {
                "lastAttemptAt": observed_at,
                "lastSuccessAt": 0 if stage in failed else observed_at,
                "outcome": "failed" if stage in failed else "ok",
            }
            for stage in _OBSERVATION_STAGES
        }
        rows = host.get("sessions")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    source = _v3_row_source(row, host_errors)
                    row["sourceObservation"] = source
                    row_sources[_as_session_key(row)] = source
    rows = migrated.get("sessions")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            host_key = str(row.get("hostId") or row.get("windowHost") or row.get("host") or "local")
            row["sourceObservation"] = row_sources.get(
                _as_session_key(row), _v3_row_source(row, errors_by_host.get(host_key, []))
            )
    return migrated


def _prior_last_refresh(previous: Mapping[str, Any] | None) -> dict[str, object]:
    """Read v4 metadata or safely synthesize it for direct legacy callers."""

    if previous is not None and _valid_last_refresh(previous.get("lastRefresh")):
        value = previous["lastRefresh"]
        assert isinstance(value, Mapping)
        return dict(value)
    generated_at = _timestamp(previous.get("generatedAt") if previous else None)
    backend = _backend_identity(previous.get("backend")) if previous else None
    if generated_at and backend is not None and backend["kind"] == "contract":
        outcome = _observation_outcome(previous.get("errors") if previous else None)
        if outcome != "failed":
            return _last_refresh(generated_at, completed_at=generated_at, outcome=outcome)
    return _last_refresh(0, completed_at=None, outcome="failed")


def _failed_contract_snapshot(
    config: PickerConfig,
    previous: Mapping[str, Any] | None,
    backend: Mapping[str, object],
    errors: list[dict[str, str]],
    *,
    attempted_at: int | None = None,
    retain_unselected_hosts: bool = False,
) -> dict[str, Any]:
    """Retain only compatible cache data without advancing freshness."""

    prior = (
        previous
        if previous is not None and _backend_identity(previous.get("backend")) == dict(backend)
        else None
    )
    hosts = dict(prior.get("hosts", {})) if isinstance(prior, Mapping) else {}
    catalog = _host_catalog(prior.get("hostCatalog")) if isinstance(prior, Mapping) else None
    prior_refresh = _prior_last_refresh(prior)
    if retain_unselected_hosts:
        # A lifecycle revalidation is deliberately narrower than the picker
        # transaction.  Its failure must not claim an all-host attempt.
        last_refresh = prior_refresh
    else:
        last_refresh = _last_refresh(
            _timestamp(attempted_at, int(time.time())),
            completed_at=None,
            outcome="failed",
        )
    snapshot = {
        "version": CACHE_VERSION,
        "fingerprint": config.fingerprint,
        # Never bless a partial/stale contract result as freshly generated.
        "generatedAt": int(prior.get("generatedAt", 0)) if isinstance(prior, Mapping) else 0,
        "backend": dict(backend),
        "hostCatalog": catalog or [],
        "hosts": hosts,
        "sessions": _flatten_hosts(hosts, config.max_sessions),
        "errors": _flatten_errors(hosts) + errors,
        "lastRefresh": last_refresh,
    }
    return snapshot


def _flatten_hosts(hosts: Mapping[str, Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for host in hosts.values():
        for item in host.get("sessions", []):
            if not isinstance(item, dict):
                continue
            key = _as_session_key(item)
            if key in seen:
                continue
            seen.add(key)
            sessions.append(dict(item))
    sessions.sort(
        key=lambda item: (int(item.get("recencyAt") or 0), str(item.get("id") or "")),
        reverse=True,
    )
    return sessions[:limit]


def _flatten_errors(hosts: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for key, host in hosts.items():
        for error in host.get("errors", []):
            if not isinstance(error, dict):
                continue
            errors.append(
                {
                    "host": str(error.get("host") or key),
                    "stage": str(error.get("stage") or "refresh"),
                    "message": str(error.get("message") or "unknown error"),
                }
            )
    return errors


def _scoped_generated_at(
    previous: Mapping[str, Any] | None,
    backend: Mapping[str, object],
) -> int:
    """Keep a partial lifecycle check from refreshing peer-host TTLs."""

    if previous is None or _backend_identity(previous.get("backend")) != dict(backend):
        return 0
    value = previous.get("generatedAt")
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _require_complete_contract_transaction(snapshot: Mapping[str, Any]) -> None:
    """Reject an aborted transaction before a lifecycle action uses old rows.

    Provider and inventory failures are classified per host and remain useful
    to ``ContractLifecycle._provider_row``. In contrast, refresh/contract
    failures mean the selected host was never authoritatively revalidated, so
    retaining its old row must not permit an open or create action.
    """

    backend = _backend_identity(snapshot.get("backend"))
    if backend is None or backend["kind"] != "contract":
        raise engine.PickerError("Agent Plus lifecycle revalidation has no current contract")
    errors = snapshot.get("errors")
    if not isinstance(errors, list):
        raise engine.PickerError("Agent Plus lifecycle revalidation returned invalid errors")
    for error in errors:
        if not isinstance(error, Mapping):
            raise engine.PickerError("Agent Plus lifecycle revalidation returned invalid errors")
        stage = error.get("stage")
        if stage in {"contract", "refresh"}:
            message = str(error.get("message") or "transaction did not complete")
            raise engine.PickerError(f"Agent Plus lifecycle revalidation failed: {message}")


def build_snapshot(
    config: PickerConfig,
    events: Iterator[dict[str, Any]],
    previous: Mapping[str, Any] | None = None,
    now: int | None = None,
    *,
    retain_unselected_hosts: bool = False,
) -> dict[str, Any]:
    """Build a versioned snapshot from the engine's per-host event stream."""

    attempted_at = int(now if now is not None else time.time())
    hosts: dict[str, dict[str, Any]] = {}
    refresh_errors: list[dict[str, str]] = []
    backend: dict[str, object] = {
        "kind": "contract",
        "capability": "host-mesh-v1+tmux-session-v1",
        "meshRevision": None,
    }
    expected_hosts: set[str] | None = None
    completed_hosts: set[str] = set()
    finished = False
    contract_aborted = False
    host_catalog: list[dict[str, object]] = []

    def preserve_previous(expected: set[str] | None = None) -> None:
        if not previous or not isinstance(previous.get("hosts"), dict):
            return
        for key, value in previous["hosts"].items():
            if (
                isinstance(key, str)
                and isinstance(value, dict)
                and (expected is None or key in expected)
            ):
                hosts[key] = dict(value)

    try:
        for event in events:
            if not isinstance(event, dict):
                refresh_errors.append(
                    {"host": "local", "stage": "refresh", "message": "malformed refresh event"}
                )
                contract_aborted = backend["kind"] == "contract"
                continue
            kind = event.get("event")
            if kind == "refresh-started":
                declared = _backend_identity(event.get("backend"))
                if event.get("backend") is not None and declared is None:
                    refresh_errors.append(
                        {"host": "local", "stage": "refresh", "message": "invalid backend identity"}
                    )
                    contract_aborted = True
                    continue
                backend = declared or backend
                names = event.get("hosts")
                if (
                    not isinstance(names, list)
                    or any(not isinstance(name, str) or not name for name in names)
                    or len(set(names)) != len(names)
                ):
                    refresh_errors.append(
                        {"host": "local", "stage": "refresh", "message": "invalid refresh host set"}
                    )
                    contract_aborted = backend["kind"] == "contract"
                    continue
                expected_hosts = set(names)
                raw_catalog = event.get("hostCatalog")
                parsed_catalog = _host_catalog(raw_catalog)
                catalog_ids = (
                    {str(item["hostId"]).casefold() for item in parsed_catalog}
                    if parsed_catalog is not None
                    else set()
                )
                expected_catalog_ids = {name.casefold() for name in expected_hosts}
                if (
                    parsed_catalog is None
                    or not parsed_catalog
                    or not expected_catalog_ids.issubset(catalog_ids)
                    or (not retain_unselected_hosts and catalog_ids != expected_catalog_ids)
                ):
                    refresh_errors.append(
                        {
                            "host": "local",
                            "stage": "refresh",
                            "message": "invalid refresh host catalog",
                        }
                    )
                    contract_aborted = backend["kind"] == "contract"
                elif (
                    retain_unselected_hosts
                    and previous is not None
                    and _backend_identity(previous.get("backend")) == backend
                ):
                    # A selected-host lifecycle refresh may update one host,
                    # but the presentation ring must retain the full catalog
                    # from the compatible prior authority.
                    prior_catalog = _host_catalog(previous.get("hostCatalog"))
                    host_catalog = prior_catalog if prior_catalog is not None else parsed_catalog
                else:
                    host_catalog = parsed_catalog
                # Contract Mesh is authoritative for host membership.  Start
                # from compatible old rows only for currently declared hosts,
                # which prunes removed hosts before any cache merge.
                if backend["kind"] == "contract" and (
                    previous is not None and _backend_identity(previous.get("backend")) == backend
                ):
                    preserve_previous(None if retain_unselected_hosts else expected_hosts)
            elif kind == "host-complete":
                key = str(event.get("host") or "local")
                if expected_hosts is not None and key not in expected_hosts:
                    refresh_errors.append(
                        {"host": key, "stage": "refresh", "message": "unexpected refresh host"}
                    )
                    contract_aborted = backend["kind"] == "contract"
                    continue
                current = {
                    "generatedAt": int(
                        event.get("generatedAt") or (now if now is not None else time.time())
                    ),
                    "sessions": event.get("sessions", []),
                    "errors": event.get("errors", []),
                }
                hosts[key] = _merge_host_snapshot(hosts.get(key), current)
                completed_hosts.add(key)
            elif kind == "refresh-finished":
                declared = _backend_identity(event.get("backend"))
                if event.get("backend") is not None and declared != backend:
                    refresh_errors.append(
                        {
                            "host": "local",
                            "stage": "refresh",
                            "message": "backend changed during refresh",
                        }
                    )
                    contract_aborted = backend["kind"] == "contract"
                finished = True
            else:
                refresh_errors.append(
                    {"host": "local", "stage": "refresh", "message": "unknown refresh event"}
                )
                contract_aborted = backend["kind"] == "contract"
    except Exception as exc:  # preserve prior hosts if a refresh aborts unexpectedly
        refresh_errors.append({"host": "local", "stage": "refresh", "message": str(exc)})
        contract_aborted = backend["kind"] == "contract"

    if backend["kind"] == "contract" and (
        contract_aborted
        or not finished
        or expected_hosts is None
        or completed_hosts != expected_hosts
    ):
        if expected_hosts is not None and completed_hosts != expected_hosts:
            refresh_errors.append(
                {
                    "host": "local",
                    "stage": "refresh",
                    "message": "incomplete contract host coverage",
                }
            )
        return _failed_contract_snapshot(
            config,
            previous,
            backend,
            refresh_errors,
            attempted_at=attempted_at,
            retain_unselected_hosts=retain_unselected_hosts,
        )

    if (
        not host_catalog
        and previous is not None
        and retain_unselected_hosts
        and _backend_identity(previous.get("backend")) == backend
    ):
        prior_catalog = _host_catalog(previous.get("hostCatalog"))
        if prior_catalog is not None:
            host_catalog = prior_catalog

    # A lifecycle revalidation only refreshes one host.  Its selected-host
    # snapshot is current, but advancing the top-level timestamp would bless
    # every retained peer as globally fresh and suppress the required next
    # all-host discovery.  An absent/incompatible prior authority falls back
    # to zero so this partial result is never treated as a full refresh.
    completed_at = int(now if now is not None else time.time())
    generated_at = (
        _scoped_generated_at(previous, backend) if retain_unselected_hosts else completed_at
    )
    errors = _flatten_errors(hosts)
    errors.extend(refresh_errors)
    last_refresh = (
        _prior_last_refresh(previous)
        if retain_unselected_hosts
        else _last_refresh(
            attempted_at,
            completed_at=completed_at,
            outcome=_observation_outcome(errors),
        )
    )
    return {
        "version": CACHE_VERSION,
        "fingerprint": config.fingerprint,
        "generatedAt": generated_at,
        "backend": backend,
        "hostCatalog": host_catalog,
        "hosts": hosts,
        "sessions": _flatten_hosts(hosts, config.max_sessions),
        "errors": errors,
        "lastRefresh": last_refresh,
    }


class CacheStore:
    """Own cache files and serialized refresh operations."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        backend_selector: Callable[[], object] | None = None,
    ) -> None:
        self.root = root or cache_root()
        self.snapshot_path = self.root / SNAPSHOT_NAME
        self.lock_path = self.root / LOCK_NAME
        self.background_path = self.root / BACKGROUND_MARKER_NAME
        self._backend_selector = backend_selector
        self._last_refresh_scope: dict[str, object] | None = None
        self._background_owner = os.environ.get(_BACKGROUND_OWNER_ENV)

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _safe_mode(self.root, 0o700)

    def load(
        self,
        fingerprint: str | None = None,
        backend: Mapping[str, object] | None = None,
    ) -> dict[str, Any] | None:
        try:
            with self.snapshot_path.open(encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") not in {
            _PREVIOUS_CACHE_VERSION,
            CACHE_VERSION,
        }:
            return None
        if fingerprint is not None and payload.get("fingerprint") != fingerprint:
            return None
        stored_backend = _backend_identity(payload.get("backend"))
        if stored_backend is None:
            return None
        if backend is not None and stored_backend != _backend_identity(backend):
            return None
        catalog_valid = _valid_host_catalog(payload.get("hostCatalog"))
        if (
            not isinstance(payload.get("sessions"), list)
            or not isinstance(payload.get("errors", []), list)
            or not isinstance(payload.get("hosts"), dict)
            or not catalog_valid
            or (stored_backend["kind"] == "contract" and not payload["hostCatalog"])
        ):
            return None
        for key, host in payload["hosts"].items():
            if not isinstance(key, str) or not isinstance(host, dict):
                return None
            if not isinstance(host.get("sessions", []), list) or not isinstance(
                host.get("errors", []), list
            ):
                return None
        if payload.get("version") == _PREVIOUS_CACHE_VERSION:
            # Deliberately return the upgraded copy only.  Opening Rofi must
            # not rewrite a valid old cache or turn migration into a cache
            # miss; a later refresh/reconciliation write persists v4.
            payload = _migrate_v3_snapshot(payload)
        if not _valid_last_refresh(payload.get("lastRefresh")):
            return None
        for host in payload["hosts"].values():
            assert isinstance(host, dict)
            if not _valid_observations(host.get("observations")):
                return None
            rows = host.get("sessions", [])
            if any(
                not isinstance(row, dict)
                or row.get("sourceObservation") not in _SOURCE_OBSERVATIONS
                for row in rows
            ):
                return None
        rows = payload.get("sessions", [])
        if any(
            not isinstance(row, dict) or row.get("sourceObservation") not in _SOURCE_OBSERVATIONS
            for row in rows
        ):
            return None
        _safe_mode(self.snapshot_path, 0o600)
        return payload

    def _select_backend(self, *, deadline: float | None = None) -> tuple[object, dict[str, object]]:
        if self._backend_selector is None:
            from .contract_backend import select_backend

            selected = select_backend()
        else:
            selected = self._backend_selector()
        # Keep injected test seams compatible while ensuring a lifecycle
        # authority re-check cannot create a second independent Mesh budget.
        if deadline is not None:
            from .contract_backend import ContractBackend

            if isinstance(selected, ContractBackend):
                selected.prepare(deadline=deadline)
            else:
                selected.prepare()  # type: ignore[union-attr]
        else:
            selected.prepare()  # type: ignore[union-attr]
        identity = selected.identity  # type: ignore[union-attr]
        if not isinstance(identity, Mapping) or _backend_identity(identity) is None:
            raise engine.PickerError("Agent Plus backend returned an invalid identity")
        return selected, dict(identity)

    def presentation_context(self, config: PickerConfig) -> PresentationContext:
        """Resolve exactly one presentation authority for a callback."""

        try:
            selected, identity = self._select_backend()
        except Exception as error:
            return PresentationContext(
                config.fingerprint,
                {
                    "kind": "contract-error",
                    "capability": "host-mesh-v1+tmux-session-v1",
                    "meshRevision": None,
                },
                str(error)[:1024],
            )
        return PresentationContext(config.fingerprint, identity, selected=selected)

    def load_current(
        self,
        config: PickerConfig,
        context: PresentationContext | None = None,
    ) -> dict[str, Any] | None:
        """Load only data from the currently selected capability/revision."""

        context = context or self.presentation_context(config)
        return self.load(config.fingerprint, context.backend)

    def cache_scope(
        self,
        config: PickerConfig,
        context: PresentationContext | None = None,
    ) -> dict[str, object]:
        """Return the marker scope for the current capability observation."""

        context = context or self.presentation_context(config)
        return {"fingerprint": config.fingerprint, "backend": context.backend}

    def age(self, snapshot: Mapping[str, Any] | None, now: float | None = None) -> float:
        if not snapshot:
            return float("inf")
        try:
            generated = float(snapshot.get("generatedAt", 0))
        except (TypeError, ValueError):
            return float("inf")
        return max(0.0, (now if now is not None else time.time()) - generated)

    def is_fresh(
        self,
        snapshot: Mapping[str, Any] | None,
        refresh_seconds: int,
        now: float | None = None,
    ) -> bool:
        return self.age(snapshot, now) <= refresh_seconds

    def write(self, snapshot: Mapping[str, Any]) -> None:
        self.ensure_root()
        persisted: Mapping[str, Any] = snapshot
        if snapshot.get("version") == _PREVIOUS_CACHE_VERSION:
            persisted = _migrate_v3_snapshot(snapshot)
        encoded = json.dumps(persisted, ensure_ascii=False, separators=(",", ":"))
        descriptor, temporary = tempfile.mkstemp(prefix=".snapshot.", suffix=".tmp", dir=self.root)
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.snapshot_path)
            _safe_mode(self.snapshot_path, 0o600)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def reconcile_contract_reference(
        self,
        config: PickerConfig,
        context: PresentationContext,
        *,
        host_id: str,
        kind: str,
        identifier: str,
        reference: Mapping[str, object],
        wait_seconds: float = 1.0,
        deadline: float | None = None,
    ) -> bool:
        """Atomically retain one successful public lifecycle descriptor.

        This is deliberately a narrow, non-authoritative cache update.  It
        neither manufactures provider data nor advances ``generatedAt``.  The
        cache is re-read under the lock and must still have the same capability
        and Mesh revision, so a result from an older authority cannot overwrite
        a newer discovery snapshot.
        """

        if context.error is not None or _backend_identity(context.backend) is None:
            return False
        wanted_backend = _backend_identity(context.backend)
        assert wanted_backend is not None
        wanted = (host_id, kind, identifier)
        wait_deadline = time.monotonic() + max(0.0, wait_seconds)
        if deadline is not None:
            wait_deadline = min(wait_deadline, deadline)
        while True:
            if time.monotonic() >= wait_deadline:
                return False
            with self.lock(blocking=False) as acquired:
                if acquired:
                    snapshot = self.load(config.fingerprint, wanted_backend)
                    if snapshot is None:
                        return False
                    # JSON round-tripping gives a deep copy without carrying
                    # references from callers into the atomic persisted value.
                    candidate = json.loads(json.dumps(snapshot, ensure_ascii=False))
                    rows = candidate.get("sessions")
                    hosts = candidate.get("hosts")
                    if not isinstance(rows, list) or not isinstance(hosts, dict):
                        return False

                    def matches(row: object) -> bool:
                        return (
                            isinstance(row, dict)
                            and row.get("contractMode") is True
                            and row.get("backend") == wanted_backend
                            and _as_session_key(row) == wanted
                        )

                    flattened = [row for row in rows if matches(row)]
                    host = hosts.get(host_id)
                    host_rows = host.get("sessions") if isinstance(host, dict) else None
                    retained = (
                        [row for row in host_rows if matches(row)]
                        if isinstance(host_rows, list)
                        else []
                    )
                    if len(flattened) != 1 or len(retained) != 1:
                        return False
                    for row in (*flattened, *retained):
                        assert isinstance(row, dict)
                        row["tmux"] = dict(reference)
                        row["tmuxSession"] = reference.get("observedName")
                        row.pop("tmuxStale", None)
                        row.pop("tmuxAmbiguous", None)
                    self.write(candidate)
                    return True
            if time.monotonic() >= wait_deadline:
                return False
            time.sleep(0.05)

    @contextmanager
    def lock(self, blocking: bool = True) -> Iterator[bool]:
        self.ensure_root()
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(descriptor, flags)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def refresh(
        self,
        config: PickerConfig,
        discover: Callable[[PickerConfig, Mapping[str, Any] | None], Iterator[dict[str, Any]]]
        | None = None,
        *,
        force: bool = False,
        require_fresh: bool = False,
        wait_seconds: float = LOCK_WAIT_SECONDS,
        context: PresentationContext | None = None,
        deadline: float | None = None,
        host_ids: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Synchronously refresh, with bounded lock waiting and safe fallback."""

        uses_selected_backend = discover is None
        selected_backend: object | None = None
        backend_identity: Mapping[str, object] | None = None
        if discover is None:
            if context is not None and context.error is not None:
                failure_backend = {
                    "kind": "contract-error",
                    "capability": "host-mesh-v1+tmux-session-v1",
                    "meshRevision": None,
                }
                snapshot = _failed_contract_snapshot(
                    config,
                    None,
                    failure_backend,
                    [{"host": "local", "stage": "contract", "message": context.error[:1024]}],
                )
                self.write(snapshot)
                self._last_refresh_scope = {
                    "fingerprint": config.fingerprint,
                    "backend": failure_backend,
                }
                if require_fresh:
                    _require_complete_contract_transaction(snapshot)
                return snapshot
            try:
                if context is not None:
                    selected_backend = context.selected
                    identity = context.backend
                    if selected_backend is None:
                        raise engine.PickerError("Agent Plus backend context is unavailable")
                else:
                    selected_backend, identity = self._select_backend()
            except Exception as error:  # never quietly fall back from a selected pair
                failure_backend = {
                    "kind": "contract-error",
                    "capability": "host-mesh-v1+tmux-session-v1",
                    "meshRevision": None,
                }
                snapshot = _failed_contract_snapshot(
                    config,
                    None,
                    failure_backend,
                    [{"host": "local", "stage": "contract", "message": str(error)[:1024]}],
                )
                self.write(snapshot)
                self._last_refresh_scope = {
                    "fingerprint": config.fingerprint,
                    "backend": failure_backend,
                }
                if require_fresh:
                    _require_complete_contract_transaction(snapshot)
                return snapshot
            backend_identity = dict(identity)

            def backend_discover(
                discovery_config: PickerConfig,
                _previous: Mapping[str, Any] | None,
            ) -> Iterator[dict[str, Any]]:
                assert selected_backend is not None
                kwargs: dict[str, object] = {}
                if deadline is not None:
                    kwargs["deadline"] = deadline
                if host_ids is not None:
                    kwargs["host_ids"] = host_ids
                return iter(selected_backend.stream(discovery_config, **kwargs))  # type: ignore[union-attr]

            discover = backend_discover
        previous = self.load(config.fingerprint, backend_identity)
        lock_deadline = time.monotonic() + wait_seconds
        if deadline is not None:
            lock_deadline = min(lock_deadline, deadline)
        while True:
            with self.lock(blocking=False) as acquired:
                if acquired:
                    # Re-read after acquiring: another process may have
                    # completed the refresh while we were waiting.
                    previous = self.load(config.fingerprint, backend_identity)
                    if not force and self.is_fresh(previous, config.refresh_seconds):
                        cached = previous or _empty_snapshot(config)
                        if require_fresh:
                            _require_complete_contract_transaction(cached)
                        return cached
                    snapshot = build_snapshot(
                        config,
                        discover(config, previous),
                        previous,
                        retain_unselected_hosts=host_ids is not None,
                    )
                    if uses_selected_backend:
                        # An old detached owner may finish after Mesh/capability
                        # changed.  Re-observe while still holding the mutation
                        # lock and never let its stale discovery overwrite the
                        # current authority's snapshot.
                        try:
                            _current_backend, current_identity = self._select_backend(
                                deadline=deadline
                            )
                        except Exception:
                            current_identity = {
                                "kind": "contract-error",
                                "capability": "host-mesh-v1+tmux-session-v1",
                                "meshRevision": None,
                            }
                        if _backend_identity(snapshot.get("backend")) != current_identity:
                            current = self.load(config.fingerprint, current_identity)
                            authority_changed = current or {
                                "version": CACHE_VERSION,
                                "fingerprint": config.fingerprint,
                                "generatedAt": 0,
                                "backend": current_identity,
                                "hostCatalog": [],
                                "hosts": {},
                                "sessions": [],
                                "errors": [
                                    {
                                        "host": "local",
                                        "stage": "contract",
                                        "message": "discovery authority changed; refresh again",
                                    }
                                ],
                                "lastRefresh": _last_refresh(
                                    int(time.time()), completed_at=None, outcome="failed"
                                ),
                            }
                            if require_fresh:
                                _require_complete_contract_transaction(authority_changed)
                            return authority_changed
                    self.write(snapshot)
                    backend = _backend_identity(snapshot.get("backend"))
                    self._last_refresh_scope = (
                        {"fingerprint": config.fingerprint, "backend": backend}
                        if backend is not None
                        else None
                    )
                    if require_fresh:
                        _require_complete_contract_transaction(snapshot)
                    return snapshot
            current = self.load(config.fingerprint, backend_identity)
            # A lifecycle action must not reinterpret any lock-contended cache
            # snapshot as its selected-host revalidation. Ordinary picker
            # callbacks may consume a just-completed same-authority refresh;
            # older data remains ordinary picker fallback only.
            if (
                current is not None
                and not require_fresh
                and (not force or self.age(current) <= 1.0)
            ):
                return current
            if time.monotonic() >= lock_deadline:
                if require_fresh:
                    raise engine.PickerError(
                        "Agent Plus lifecycle revalidation is already in progress"
                    )
                if current is not None:
                    return current
                if previous is not None:
                    return previous
                raise engine.PickerError("Agent Plus refresh is already in progress")
            time.sleep(0.05)

    @staticmethod
    def _marker_matches(
        payload: object,
        scope: Mapping[str, object] | None,
        owner: str | None = None,
    ) -> bool:
        if scope is None and owner is None:
            # Preserve this unscoped test seam; production Rofi passes a typed
            # capability scope and owned cleanup always passes an owner.
            return True
        if not isinstance(payload, Mapping):
            return False
        if scope is not None and payload.get("scope") != dict(scope):
            return False
        return owner is None or payload.get("owner") == owner

    def _read_marker(self) -> object | None:
        try:
            with self.background_path.open(encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _write_marker(self, payload: Mapping[str, object]) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".refresh.", suffix=".tmp", dir=self.root)
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.background_path)
            _safe_mode(self.background_path, 0o600)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def _clear_marker_if_owned(self, owner: str, scope: Mapping[str, object] | None) -> None:
        payload = self._read_marker()
        if not self._marker_matches(payload, scope, owner):
            return
        try:
            self.background_path.unlink()
        except FileNotFoundError:
            pass

    def spawn_background(
        self,
        command: list[str],
        *,
        scope: Mapping[str, object] | None = None,
    ) -> bool:
        """Start at most one detached refresh process using a marker claim."""

        self.ensure_root()
        with self.lock(blocking=False) as acquired:
            if not acquired:
                return False
            payload = self._read_marker()
            try:
                stale = (
                    self.background_path.exists()
                    and time.time() - self.background_path.stat().st_mtime > LOCK_WAIT_SECONDS * 4
                )
            except OSError:
                stale = False
            if payload is not None and self._marker_matches(payload, scope) and not stale:
                return False
            # Replace atomically while holding the refresh lock.  There is no
            # unlink/create gap in which another callback can claim the same
            # scope.  A prior worker cannot clear this new marker because its
            # owner token differs.
            owner = secrets.token_hex(16)
            self._write_marker({"pid": os.getpid(), "scope": dict(scope or {}), "owner": owner})
        try:
            environment = os.environ.copy()
            for key in _ROFI_CALLBACK_ENVIRONMENT:
                environment.pop(key, None)
            environment[_BACKGROUND_OWNER_ENV] = owner
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=environment,
            )
        except OSError:
            self._clear_marker_if_owned(owner, scope)
            return False
        return True

    def background_age(self, now: float | None = None) -> float | None:
        """Return the age of the detached-refresh marker, if it is valid."""

        try:
            metadata = self.background_path.stat()
        except OSError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
            return None
        return max(0.0, (now if now is not None else time.time()) - metadata.st_mtime)

    def background_active(
        self,
        max_age: float = LOCK_WAIT_SECONDS * 4,
        now: float | None = None,
        scope: Mapping[str, object] | None = None,
    ) -> bool:
        """Report whether a detached refresh marker is recent enough to poll."""

        if not self._marker_matches(self._read_marker(), scope):
            return False
        age = self.background_age(now)
        return age is not None and age <= max_age

    def clear_background_marker(
        self,
        *,
        scope: Mapping[str, object] | None = None,
        owner: str | None = None,
    ) -> None:
        if scope is None:
            scope = self._last_refresh_scope
        owner = owner or self._background_owner
        payload = self._read_marker()
        if owner is not None:
            if not self._marker_matches(payload, scope, owner):
                return
        elif isinstance(payload, Mapping) and payload.get("pid") != os.getpid():
            return
        try:
            self.background_path.unlink()
        except FileNotFoundError:
            pass

    def clear_owned_background_marker(self) -> None:
        """Clear only a marker whose authority this refresh observed."""

        if self._background_owner is not None:
            self.clear_background_marker(owner=self._background_owner)
        elif self._last_refresh_scope is not None:
            self.clear_background_marker(scope=self._last_refresh_scope)


def _empty_snapshot(config: PickerConfig) -> dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "fingerprint": config.fingerprint,
        "generatedAt": int(time.time()),
        "backend": {
            "kind": "contract-error",
            "capability": "host-mesh-v1+tmux-session-v1",
            "meshRevision": None,
        },
        "hostCatalog": [],
        "hosts": {},
        "sessions": [],
        "errors": [],
        "lastRefresh": _last_refresh(0, completed_at=None, outcome="failed"),
    }
