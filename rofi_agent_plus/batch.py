"""Finite, fixed-target batch operations for the Agent Plus picker.

This module owns no terminal, SSH, tmux, or compositor behavior. It builds a
preview from current Agent observations and calls only the public Tmux Plus
viewer wrapper. Private fixed-name records hand read-only preparation to a
finite helper and the confirmed target list to one short-lived worker process.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import contract_viewers, engine, viewer_state
from .cache import CacheStore, PresentationContext, cache_root
from .config import PickerConfig, load_config
from .contract_backend import StaleMeshError
from .contract_lifecycle import (
    _PROVIDER_OPTIONS,
    LifecycleError,
    StableReference,
    _backend_identity,
    _reference,
)

ACTION_CLOSE = "close"
ACTION_RESUME = "resume"
_ACTIONS = frozenset({ACTION_CLOSE, ACTION_RESUME})
_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z", re.ASCII)
_PROVIDERS = frozenset({"codex", "claude", "opencode"})
_VIEWS = frozenset({"active", "open", "all", "local", "host"})
_DESKTOP = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_STATE_BYTES = 16 * 1024 * 1024
_MAX_TARGETS = 12_000
_MAX_RESULTS = _MAX_TARGETS
_MAX_TEXT = 1024
_PREVIEW_SECONDS = 60 * 60
_QUEUED_SECONDS = 120
_PREPARATION_SECONDS = 120
_INSPECTION_WORKERS = 4
_JOB_DIR = "batch-actions"
_PREVIEW_NAME = "preview.json"
_PREPARATION_NAME = "preparation.json"
_JOB_NAME = "job.json"
_STATE_LOCK_NAME = "state.lock"
_WORKER_LOCK_NAME = "worker.lock"


class BatchError(engine.PickerError):
    """A bounded batch-state or preparation error."""


class BatchBusy(BatchError):
    """An earlier confirmed batch still owns the endpoint."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__("A batch is already running")


@dataclass(frozen=True)
class Scope:
    view: str
    host_id: str | None = None
    viewer_desktop: str | None = None


def _valid_scope(scope: Scope) -> bool:
    return (
        isinstance(scope.view, str)
        and scope.view in _VIEWS
        and (
            isinstance(scope.host_id, str) and bool(_HOST_ID.fullmatch(scope.host_id))
            if scope.view == "host"
            else scope.host_id is None
        )
        and (
            scope.viewer_desktop is None
            or scope.view == "open"
            and isinstance(scope.viewer_desktop, str)
            and bool(_DESKTOP.fullmatch(scope.viewer_desktop))
        )
    )


def _valid_open_context(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"endpointHostId", "desktop"}
        and isinstance(value.get("endpointHostId"), str)
        and bool(_HOST_ID.fullmatch(value["endpointHostId"]))
        and isinstance(value.get("desktop"), str)
        and bool(_DESKTOP.fullmatch(value["desktop"]))
    )


def _open_endpoint(catalog: Sequence[Mapping[str, object]]) -> str | None:
    return next((str(host["hostId"]) for host in catalog if host.get("local") is True), None)


def operation_targets(record: Mapping[str, object] | None) -> list[Mapping[str, object]]:
    """Keep only work needed when the fixed preview was prepared."""
    if record is None:
        return []
    action = record.get("action")
    mode = "open" if action == ACTION_RESUME else "close" if action == ACTION_CLOSE else None
    targets = record.get("targets")
    if mode is None or not isinstance(targets, list):
        return []
    return [
        target for target in targets if isinstance(target, Mapping) and target.get("mode") == mode
    ]


def _short(value: object, limit: int = _MAX_TEXT) -> str:
    text = str(value) if value is not None else ""
    text = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in text)
    return text.strip()[:limit]


def _backend_for(context: PresentationContext) -> object:
    if context.error is not None or context.selected is None:
        raise BatchError(_short(context.error or "Current Tmux authority is unavailable"))
    _backend_identity(context.backend)
    if not (
        isinstance(getattr(context.selected, "tmux_command", None), str)
        and callable(getattr(context.selected, "_run", None))
    ):
        raise BatchError("Current Tmux viewer authority is unavailable")
    return context.selected


def _valid_catalog(snapshot: object) -> list[dict[str, object]]:
    raw = snapshot.get("hostCatalog") if isinstance(snapshot, Mapping) else None
    if not isinstance(raw, list) or len(raw) > 128:
        return []
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            return []
        host_id, display, local = item.get("hostId"), item.get("display"), item.get("local")
        if (
            not isinstance(host_id, str)
            or not _HOST_ID.fullmatch(host_id)
            or host_id.casefold() in seen
            or not isinstance(display, str)
            or not display
            or len(display) > _MAX_TEXT
            or not isinstance(local, bool)
        ):
            return []
        seen.add(host_id.casefold())
        result.append({"hostId": host_id, "display": display, "local": local})
    if result and (
        result[0]["local"] is not True or sum(item["local"] is True for item in result) != 1
    ):
        return []
    return result


def _hosts_for_scope(
    catalog: Sequence[Mapping[str, object]], scope: Scope
) -> tuple[str, ...] | None:
    if scope.view in {"active", "open", "all"}:
        return None
    if scope.view == "local":
        local = next((item for item in catalog if item.get("local") is True), None)
        return (str(local["hostId"]),) if local is not None else ()
    if scope.view == "host" and isinstance(scope.host_id, str):
        match = next(
            (
                item
                for item in catalog
                if str(item.get("hostId", "")).casefold() == scope.host_id.casefold()
            ),
            None,
        )
        return (str(match["hostId"]),) if match is not None else ()
    return None


def scope_label(catalog: Sequence[Mapping[str, object]], scope: Scope) -> str:
    if scope.view == "open":
        return "Open · local viewers across authoritative session-owner hosts"
    if scope.view == "active":
        return "Active · all authoritative session-owner hosts"
    if scope.view == "all":
        return "All · all authoritative session-owner hosts; may include active rows beyond the ordinary list cap"
    if scope.view == "local":
        local = next((item for item in catalog if item.get("local") is True), None)
        return f"Local · owner {local.get('display', local.get('hostId')) if local else 'host unavailable'}"
    if scope.view == "host":
        match = next(
            (
                item
                for item in catalog
                if str(item.get("hostId", "")).casefold() == (scope.host_id or "").casefold()
            ),
            None,
        )
        owner = match.get("display", match.get("hostId")) if match else scope.host_id or "host"
        return f"{_short(owner)} · session owner"
    return "All authoritative session-owner hosts"


def _host_record(snapshot: Mapping[str, object], host_id: str) -> Mapping[str, object] | None:
    hosts = snapshot.get("hosts")
    if not isinstance(hosts, Mapping):
        return None
    record = hosts.get(host_id)
    if isinstance(record, Mapping):
        return record
    for key, value in hosts.items():
        if (
            isinstance(key, str)
            and key.casefold() == host_id.casefold()
            and isinstance(value, Mapping)
        ):
            return value
    return None


def _observation_ok(record: Mapping[str, object] | None, stage: str) -> bool:
    observations = record.get("observations") if isinstance(record, Mapping) else None
    observation = observations.get(stage) if isinstance(observations, Mapping) else None
    return isinstance(observation, Mapping) and observation.get("outcome") == "ok"


def _provider_option(row: Mapping[str, object]) -> tuple[str, str] | None:
    if row.get("providerOptionVerified") is not True:
        return None
    kind, identifier = row.get("kind"), row.get("id")
    if (
        not isinstance(kind, str)
        or kind not in _PROVIDER_OPTIONS
        or not isinstance(identifier, str)
    ):
        return None
    return str(_PROVIDER_OPTIONS[kind][0]), identifier


def _valid_provider_identifier(kind: object, identifier: object) -> bool:
    if not isinstance(kind, str) or kind not in _PROVIDERS or not isinstance(identifier, str):
        return False
    pattern = engine.OPENCODE_ID_PATTERN if kind == "opencode" else engine.UUID_PATTERN
    return pattern.fullmatch(identifier) is not None


def _ref_payload(reference: StableReference) -> dict[str, object]:
    payload = reference.payload()
    payload["hostId"] = reference.host_id
    return payload


def _ref_key(value: Mapping[str, object]) -> tuple[object, ...]:
    return (
        value.get("hostId"),
        value.get("meshRevision"),
        value.get("serverGeneration"),
        value.get("sessionId"),
        value.get("createdAt"),
    )


def _text_target(row: Mapping[str, object], host_display: str, reason: str) -> dict[str, str]:
    return {
        "name": _short(row.get("name") or row.get("id") or "Agent"),
        "provider": _short(row.get("kind") or "Agent"),
        "host": _short(host_display),
        "reason": _short(reason),
    }


def _candidate_rows(
    snapshot: Mapping[str, object],
    catalog: Sequence[Mapping[str, object]],
    host_ids: tuple[str, ...] | None,
    identity: Mapping[str, object],
    *,
    open_refs: set[tuple[str, str, str, int]] | None = None,
) -> tuple[list[tuple[dict[str, object], str]], list[dict[str, str]]]:
    wanted_hosts = {host.casefold() for host in host_ids} if host_ids is not None else None
    candidates: list[tuple[dict[str, object], str]] = []
    exclusions: list[dict[str, str]] = []
    for host in catalog:
        host_id = str(host["hostId"])
        display = str(host["display"])
        if wanted_hosts is not None and host_id.casefold() not in wanted_hosts:
            continue
        record = _host_record(snapshot, host_id)
        raw_rows = record.get("sessions") if isinstance(record, Mapping) else None
        rows = raw_rows if isinstance(raw_rows, list) else []
        identity_counts: dict[tuple[str, str, str], int] = {}
        candidates_for_host: list[tuple[Mapping[str, object], tuple[str, str, str]]] = []
        for raw in rows:
            if not isinstance(raw, Mapping) or not _valid_provider_identifier(
                raw.get("kind"), raw.get("id")
            ):
                continue
            kind, identifier = str(raw["kind"]), str(raw["id"])
            logical = (host_id.casefold(), kind, identifier)
            identity_counts[logical] = identity_counts.get(logical, 0) + 1
            candidates_for_host.append((raw, logical))
        reported_duplicates: set[tuple[str, str, str]] = set()
        for raw, logical in candidates_for_host:
            kind, identifier = str(raw["kind"]), str(raw["id"])
            active = raw.get("active") is True or raw.get("activityState") == "waiting"
            if not active:
                continue
            row = dict(raw)
            if open_refs is not None:
                raw_ref = row.get("tmux")
                if (
                    not isinstance(raw_ref, Mapping)
                    or raw_ref.get("hostId", row.get("hostId")) != row.get("hostId")
                    or raw_ref.get("meshRevision") != identity.get("meshRevision")
                    or viewer_state.reference_key({**raw_ref, "hostId": row.get("hostId")})
                    not in open_refs
                ):
                    continue
            if identity_counts[logical] > 1:
                if logical not in reported_duplicates:
                    exclusions.append(
                        _text_target(row, display, "duplicate provider rows are ambiguous")
                    )
                    reported_duplicates.add(logical)
                continue
            if (
                row.get("contractMode") is not True
                or row.get("backend") != dict(identity)
                or row.get("hostId") != host_id
                or row.get("sourceObservation") != "current"
                or not _observation_ok(record, "activity")
                or not _observation_ok(record, str(kind))
                or not _observation_ok(record, "tmux")
            ):
                exclusions.append(_text_target(row, display, "stale observation"))
                continue
            if row.get("tmuxAmbiguous") is True:
                exclusions.append(_text_target(row, display, "ambiguous tmux association"))
                continue
            if row.get("tmuxStale") is True:
                exclusions.append(_text_target(row, display, "stale tmux association"))
                continue
            if not isinstance(row.get("tmux"), Mapping):
                exclusions.append(_text_target(row, display, "no tmux association"))
                continue
            try:
                revision = identity.get("meshRevision")
                reference = _reference(
                    row.get("tmux"), host_id, revision if isinstance(revision, str) else None
                )
            except LifecycleError:
                exclusions.append(_text_target(row, display, "missing or invalid tmux association"))
                continue
            option = _provider_option(row)
            if row.get("providerOptionVerified") is True and option is None:
                exclusions.append(_text_target(row, display, "invalid provider option evidence"))
                continue
            candidate: dict[str, object] = {
                "hostId": host_id,
                "kind": str(kind),
                "id": identifier,
                "name": _short(row.get("name") or identifier),
                "host": display,
                "reference": _ref_payload(reference),
                "requiredOption": list(option) if option is not None else None,
            }
            candidates.append((candidate, display))
        if not rows and record is not None:
            errors = record.get("errors")
            host_errors = (
                [item for item in errors if isinstance(item, Mapping)]
                if isinstance(errors, list)
                else []
            )
            if host_errors:
                detail = _short(
                    host_errors[0].get("message")
                    or host_errors[0].get("stage")
                    or "refresh unavailable"
                )
                reason = (
                    "unreachable host"
                    if any(
                        word in detail.casefold()
                        for word in ("unreachable", "timeout", "offline", "could not")
                    )
                    else "host refresh unavailable"
                )
                exclusions.append(
                    {"name": "Host observations", "provider": "", "host": display, "reason": reason}
                )
    return candidates, exclusions


def _exclusion_reason(inspection: contract_viewers.ViewerInspection) -> str:
    if inspection.reason:
        return _short(inspection.reason)
    return {
        "unverified": "viewer identity is unverified; close manually if needed",
        "ambiguous": "viewer association is ambiguous",
        "unsupported": "viewer environment is unsupported",
        "none": "no verified viewer",
        "verified": "viewer inspection failed",
    }.get(inspection.status, "viewer inspection unavailable")


def build_single_close_preview(
    store: CacheStore,
    config: PickerConfig,
    selection: Mapping[str, object],
    *,
    context: PresentationContext | None = None,
) -> dict[str, object]:
    """Freeze verified local viewers for one selected conversation.

    This path deliberately does not refresh provider sessions. The selected
    row is narrowed by its typed host/provider/id and full tmux reference,
    then the public viewer inspection verifies that exact reference and its
    exact provider option against the current authority.
    """

    context = store.presentation_context(config) if context is None else context
    backend = _backend_for(context)
    identity = _backend_identity(context.backend)
    snapshot = store.load_current(config, context)
    catalog = _valid_catalog(snapshot)
    host_id = selection.get("hostId")
    kind = selection.get("kind")
    identifier = selection.get("id")
    selected_backend = selection.get("backend")
    display = "Selected host"
    reason = "selected owner host is not in the current authoritative catalog"
    owner = next(
        (item for item in catalog if isinstance(host_id, str) and item.get("hostId") == host_id),
        None,
    )
    if owner is not None:
        display = str(owner["display"])
    scope = f"Selected conversation · owner {display}"

    def excluded(reason_text: str, *, stop: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "action": ACTION_CLOSE,
            "backend": dict(identity),
            "scope": _short(scope),
            "targets": [],
            "exclusions": [
                {
                    "name": _short(selection.get("name") or identifier or "Selected session"),
                    "provider": _short(kind or ""),
                    "host": _short(display),
                    "reason": _short(reason_text),
                }
            ],
            "createdAt": int(time.time()),
        }
        if stop:
            result["stopReason"] = _short(reason_text)
        return result

    if owner is None:
        return excluded(reason)
    if (
        not isinstance(host_id, str)
        or not isinstance(kind, str)
        or not isinstance(identifier, str)
        or not _valid_provider_identifier(kind, identifier)
    ):
        return excluded("selected host/provider/session identity is invalid")
    try:
        if _backend_identity(selected_backend) != identity:
            return excluded("selected session belongs to an older Host Mesh authority")
    except LifecycleError:
        return excluded("selected session has no verifiable Host Mesh authority")
    if selection.get("tmuxAssociationCurrent") is not True:
        return excluded("selected tmux association is stale or ambiguous")
    if selection.get("providerOptionVerified") is not True:
        return excluded("provider option association is not currently verified")
    option = _provider_option(selection)
    if option is None:
        return excluded("selected provider option evidence is invalid")
    try:
        reference = _reference(
            selection.get("tmux"),
            host_id,
            identity.get("meshRevision") if isinstance(identity.get("meshRevision"), str) else None,
        )
    except LifecycleError:
        return excluded("selected tmux reference is missing or no longer current")

    try:
        inspection = contract_viewers.inspect_viewers(
            backend,
            reference,
            required_option=option,
        )
    except contract_viewers.ViewerError as error:
        return excluded(
            _short(error) or "viewer inspection failed",
            stop=error.stop_batch,
        )
    except (LifecycleError, engine.PickerError, OSError) as error:
        return excluded(_short(error) or "viewer inspection failed")
    if inspection.status != "verified":
        return excluded(_exclusion_reason(inspection))
    if not inspection.close_safe:
        return excluded("closing could destroy the tmux session")

    target: dict[str, object] = {
        "hostId": host_id,
        "kind": kind,
        "id": identifier,
        "name": _short(selection.get("name") or identifier),
        "host": display,
        "reference": _ref_payload(reference),
        "requiredOption": list(option),
        "mode": "close",
        "viewers": [
            {"viewerId": viewer.viewer_id, "windowId": viewer.window_id}
            for viewer in inspection.viewers
        ],
    }
    return {
        "action": ACTION_CLOSE,
        "backend": dict(identity),
        "scope": _short(scope),
        "targets": [target],
        "exclusions": [],
        "createdAt": int(time.time()),
    }


def _inspect_candidate(
    backend: object, candidate: Mapping[str, object], revision: object
) -> contract_viewers.ViewerInspection:
    raw_option = candidate.get("requiredOption")
    required_option = (
        tuple(str(value) for value in raw_option)
        if isinstance(raw_option, list) and len(raw_option) == 2
        else None
    )
    reference = _reference(
        candidate.get("reference"),
        str(candidate["hostId"]),
        revision if isinstance(revision, str) else None,
    )
    return contract_viewers.inspect_viewers(backend, reference, required_option=required_option)


def _inspection_futures(
    executor: ThreadPoolExecutor,
    backend: object,
    candidates: Sequence[tuple[dict[str, object], Mapping[str, object]]],
    revision: object,
    is_current: Callable[[], bool] | None,
) -> Iterator[
    tuple[dict[str, object], Mapping[str, object], Future[contract_viewers.ViewerInspection]]
]:
    # Submit one bounded chunk at a time. Results retain catalog order, and a
    # protocol failure prevents the next chunk from starting. Already-running
    # checks are read-only and finish under their public command timeout.
    for start in range(0, len(candidates), _INSPECTION_WORKERS):
        if is_current is not None and not is_current():
            raise BatchError("Preview preparation was cancelled or expired")
        pending = [
            (candidate, display, executor.submit(_inspect_candidate, backend, candidate, revision))
            for candidate, display in candidates[start : start + _INSPECTION_WORKERS]
        ]
        yield from pending


def build_preview(
    store: CacheStore,
    config: PickerConfig,
    scope: Scope,
    action: str,
    *,
    context: PresentationContext | None = None,
    is_current: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Refresh the selected page scope and freeze eligible viewer targets."""

    if action not in _ACTIONS or not _valid_scope(scope):
        raise BatchError("Unknown batch action or scope")
    context = store.presentation_context(config) if context is None else context
    backend = _backend_for(context)
    identity = _backend_identity(context.backend)
    catalog_before = _valid_catalog(store.load_current(config, context))
    open_context: dict[str, str] | None = None
    open_started_at = int(time.time() * 1000)
    if scope.view == "open":
        desktop = scope.viewer_desktop or viewer_state.desktop_context()
        endpoint = _open_endpoint(catalog_before)
        if endpoint is None or desktop != viewer_state.desktop_context():
            raise BatchError("Open page endpoint is unavailable or changed")
        open_context = {"endpointHostId": endpoint, "desktop": desktop}
        if action == ACTION_RESUME:
            # Open is already the visible subset. Never turn a display match
            # or a disappearing window into a launch or bulk-focus operation.
            return {
                "action": action,
                "backend": dict(identity),
                "scope": scope_label(catalog_before, scope),
                "openContext": open_context,
                "targets": [],
                "exclusions": [],
                "createdAt": int(time.time()),
            }
    host_ids = _hosts_for_scope(catalog_before, scope)
    if scope.view in {"local", "host"} and host_ids is None:
        # Without a current catalog this owner scope cannot be expanded from
        # a display label or silently widened to the whole Mesh.
        host_ids = ()
    if host_ids == ():
        refreshed: Mapping[str, object] = store.load_current(config, context) or {}
    else:
        refreshed = store.refresh(
            config,
            force=True,
            context=context,
            host_ids=host_ids,
            require_fresh=False,
        )
    if not isinstance(refreshed, Mapping):
        refreshed = {}
    current_identity = _backend_identity(refreshed.get("backend"))
    if current_identity != identity:
        return {
            "action": action,
            "backend": dict(identity),
            "scope": scope_label(catalog_before, scope),
            "targets": [],
            "exclusions": [
                {
                    "name": "Batch preview",
                    "provider": "",
                    "host": "",
                    "reason": "current authority changed during refresh",
                }
            ],
            "createdAt": int(time.time()),
            **({"openContext": open_context} if open_context is not None else {}),
        }
    catalog = _valid_catalog(refreshed)
    scope_text = scope_label(catalog, scope)
    open_refs: set[tuple[str, str, str, int]] | None = None
    if open_context is not None:
        if (
            _open_endpoint(catalog) != open_context["endpointHostId"]
            or viewer_state.desktop_context() != open_context["desktop"]
        ):
            raise BatchError("Open page endpoint changed during preparation")
        open_refs = set()
        inventory = getattr(backend, "viewer_inventory_result", None)
        inventory_started = getattr(backend, "viewer_inventory_started_at", None)
        try:
            if (
                not isinstance(inventory, Mapping)
                or type(inventory_started) is not int
                or inventory_started < open_started_at
                or getattr(backend, "viewer_inventory_desktop", None) != open_context["desktop"]
            ):
                raise viewer_state.ViewerStateError("Fresh local viewer observations unavailable")
            viewer_scope = viewer_state.make_scope(
                config.fingerprint, identity, open_context["endpointHostId"]
            )
            observed = viewer_state.observations_from_inventory(inventory, viewer_scope)
            elapsed = int(time.time() * 1000) - observed["observedAt"]
            if not 0 <= elapsed <= viewer_state.FRESH_SECONDS * 1000:
                raise viewer_state.ViewerStateError("Local viewer observations expired")
            for item in observed["rows"]:
                viewer = item["viewer"]
                if viewer.get("state") == "open" and viewer.get("confidence") in {
                    "confirmed",
                    "matched",
                }:
                    key = viewer_state.reference_key(item["sessionRef"])
                    if key is not None:
                        open_refs.add(key)
        except (viewer_state.ViewerStateError, ValueError, TypeError) as error:
            return {
                "action": action,
                "backend": dict(identity),
                "scope": scope_text,
                "openContext": open_context,
                "targets": [],
                "exclusions": [
                    {"name": "Open sessions", "provider": "", "host": "", "reason": _short(error)}
                ],
                "createdAt": int(time.time()),
                "stopReason": "Fresh local viewer observations unavailable",
            }
    candidates, exclusions = _candidate_rows(
        refreshed, catalog, host_ids, identity, open_refs=open_refs
    )
    if host_ids == ():
        exclusions.append(
            {
                "name": "Selected page scope",
                "provider": "",
                "host": "",
                "reason": "no authoritative owner host is available for this page",
            }
        )
    targets: list[dict[str, object]] = []
    seen_refs: set[tuple[object, ...]] = set()
    unique_candidates = []
    for candidate, display in candidates:
        reference_payload = candidate.get("reference")
        assert isinstance(reference_payload, Mapping)
        key = _ref_key(reference_payload)
        if key in seen_refs:
            exclusions.append(
                _text_target(candidate, display, "duplicate tmux reference already listed")
            )
            continue
        seen_refs.add(key)
        unique_candidates.append((candidate, display))
    inspection_stopped = False
    with ThreadPoolExecutor(max_workers=_INSPECTION_WORKERS) as executor:
        for candidate, display, future in _inspection_futures(
            executor, backend, unique_candidates, identity.get("meshRevision"), is_current
        ):
            try:
                inspection = future.result()
            except contract_viewers.ViewerError as error:
                exclusions.append(_text_target(candidate, display, str(error)))
                if error.stop_batch:
                    inspection_stopped = True
                    break
                continue
            except (LifecycleError, engine.PickerError, OSError) as error:
                exclusions.append(
                    _text_target(candidate, display, _short(error) or "viewer inspection failed")
                )
                continue
            mode: str
            if inspection.status not in {"none", "verified"}:
                exclusions.append(_text_target(candidate, display, _exclusion_reason(inspection)))
                continue
            if action == ACTION_CLOSE:
                if inspection.status == "none":
                    mode = "already_closed"
                elif not inspection.close_safe:
                    exclusions.append(
                        _text_target(candidate, display, "closing could destroy the tmux session")
                    )
                    continue
                else:
                    mode = "close"
            elif inspection.status == "verified":
                mode = "already_open"
            else:
                mode = "open"
            frozen = dict(candidate)
            frozen["mode"] = mode
            frozen["viewers"] = [
                {"viewerId": viewer.viewer_id, "windowId": viewer.window_id}
                for viewer in inspection.viewers
            ]
            targets.append(frozen)
            if len(targets) >= _MAX_TARGETS:
                exclusions.append(
                    {
                        "name": "Remaining active sessions",
                        "provider": "",
                        "host": "",
                        "reason": "preview target safety limit reached",
                    }
                )
                break
    if inspection_stopped:
        exclusions.append(
            {
                "name": "Remaining active sessions",
                "provider": "",
                "host": "",
                "reason": "viewer protocol or authority failure stopped preview inspection",
            }
        )
    result: dict[str, object] = {
        "action": action,
        "backend": dict(identity),
        "scope": scope_text,
        "targets": targets,
        "exclusions": exclusions[:_MAX_TARGETS],
        "createdAt": int(time.time()),
        **({"openContext": open_context} if open_context is not None else {}),
    }
    if open_context is not None and viewer_state.desktop_context() != open_context["desktop"]:
        raise BatchError("Open page endpoint changed during preparation")
    if inspection_stopped:
        result["stopReason"] = "viewer protocol or authority failure stopped preview inspection"
    return result


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_state(raw: bytes) -> object:
    if len(raw) > _MAX_STATE_BYTES:
        raise ValueError("state too large")
    return json.loads(
        raw.decode("utf-8", "strict"),
        object_pairs_hook=_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid constant")),
    )


class BatchStateStore:
    """Private fixed-name records with strict owner, type, and size checks."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or cache_root()) / _JOB_DIR
        self.preview_path = self.root / _PREVIEW_NAME
        self.preparation_path = self.root / _PREPARATION_NAME
        self.job_path = self.root / _JOB_NAME
        self.state_lock_path = self.root / _STATE_LOCK_NAME
        self.worker_lock_path = self.root / _WORKER_LOCK_NAME

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        metadata = self.root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise BatchError("Batch state directory is not private")

    @contextmanager
    def locked(self, *, blocking: bool = True) -> Iterator[bool]:
        self.ensure_root()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.state_lock_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise BatchError("Batch state lock is unsafe")
            os.fchmod(descriptor, 0o600)
            operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @contextmanager
    def worker_lock(self, *, blocking: bool = True) -> Iterator[bool]:
        self.ensure_root()
        descriptor = os.open(
            self.worker_lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise BatchError("Batch worker lock is unsafe")
            os.fchmod(descriptor, 0o600)
            operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _read(self, path: Path) -> object | None:
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_size > _MAX_STATE_BYTES
            ):
                return None
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_STATE_BYTES + 1)
            return _parse_state(raw)
        except (OSError, UnicodeError, ValueError, RecursionError):
            return None
        finally:
            os.close(descriptor)

    def _write(self, path: Path, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > _MAX_STATE_BYTES:
            raise BatchError("Batch state exceeds its safety limit")
        descriptor, temporary = tempfile.mkstemp(prefix=".batch-", suffix=".tmp", dir=self.root)
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            os.chmod(path, 0o600)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def _read_preview(self) -> Mapping[str, object] | None:
        value = self._read(self.preview_path)
        return value if isinstance(value, Mapping) else None

    def _read_job(self) -> Mapping[str, object] | None:
        value = self._read(self.job_path)
        return value if isinstance(value, Mapping) else None

    def _read_preparation_locked(self, request_id: str) -> Mapping[str, object] | None:
        value = self._read(self.preparation_path)
        fields = {
            "version",
            "requestId",
            "action",
            "view",
            "hostId",
            "createdAt",
            "status",
            "previewId",
            "error",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) - {"viewerDesktop"} != fields
            or type(value.get("version")) is not int
            or value["version"] != 1
            or not _ID.fullmatch(request_id)
            or value.get("requestId") != request_id
            or not isinstance(value.get("action"), str)
            or value.get("action") not in _ACTIONS
            or not isinstance(value.get("view"), str)
            or value.get("view") not in _VIEWS
            or (
                value["view"] == "open"
                and (
                    not isinstance(value.get("viewerDesktop"), str)
                    or not _DESKTOP.fullmatch(value["viewerDesktop"])
                )
            )
            or (value["view"] != "open" and "viewerDesktop" in value)
            or (
                value["view"] == "host"
                and (
                    not isinstance(value.get("hostId"), str)
                    or not _HOST_ID.fullmatch(value["hostId"])
                )
            )
            or (value["view"] != "host" and value.get("hostId") is not None)
            or not isinstance(value.get("status"), str)
            or value.get("status") not in {"pending", "ready", "failed"}
            or type(value.get("createdAt")) is not int
            or not 0
            <= int(time.time()) - value["createdAt"]
            <= (_PREVIEW_SECONDS if value["status"] == "ready" else _PREPARATION_SECONDS)
            or not isinstance(value.get("error"), str)
            or len(value["error"]) > _MAX_TEXT
            or (
                value["status"] == "ready"
                and (
                    not isinstance(value.get("previewId"), str)
                    or not _ID.fullmatch(value["previewId"])
                )
            )
            or (value["status"] != "ready" and value.get("previewId") is not None)
        ):
            return None
        return value

    def read_preparation(self, request_id: str) -> Mapping[str, object] | None:
        with self.locked():
            return self._read_preparation_locked(request_id)

    def start_preparation(self, action: str, scope: Scope, *, spawn: bool = True) -> str:
        """Start one finite read-only request; a new request supersedes old results."""
        if action not in _ACTIONS or not _valid_scope(scope):
            raise BatchError("Invalid preview preparation scope or action")
        desktop = (
            scope.viewer_desktop or viewer_state.desktop_context() if scope.view == "open" else None
        )
        if desktop is not None and desktop != viewer_state.desktop_context():
            raise BatchError("Open page endpoint changed")
        request_id = secrets.token_hex(16)
        with self.locked():
            active = self._active_job_locked()
            if active is not None:
                raise BatchBusy(str(active.get("jobId", "")))
            # Re-entering the same pending request does not spawn another helper.
            old = self._read(self.preparation_path)
            if isinstance(old, Mapping) and isinstance(old.get("requestId"), str):
                current = self._read_preparation_locked(old["requestId"])
                if (
                    current is not None
                    and current["status"] == "pending"
                    and (current["action"], current["view"], current["hostId"])
                    == (action, scope.view, scope.host_id)
                    and current.get("viewerDesktop") == desktop
                ):
                    return str(current["requestId"])
            self.preview_path.unlink(missing_ok=True)
            record = {
                "version": 1,
                "requestId": request_id,
                "action": action,
                "view": scope.view,
                "hostId": scope.host_id,
                "createdAt": int(time.time()),
                "status": "pending",
                "previewId": None,
                "error": "",
                **({"viewerDesktop": desktop} if desktop is not None else {}),
            }
            self._write(self.preparation_path, record)
            if spawn:
                try:
                    _spawn_helper("_batch-prepare", request_id)
                except OSError as error:
                    record.update(status="failed", error="Could not start preview preparation")
                    self._write(self.preparation_path, record)
                    raise BatchError("Could not start preview preparation") from error
        return request_id

    def finish_preparation(self, request_id: str, payload: Mapping[str, object]) -> bool:
        """Publish only if this exact unexpired request still owns the preview."""
        with self.locked():
            current = self._read_preparation_locked(request_id)
            if current is None or current["status"] != "pending":
                return False
            if self._active_job_locked() is not None:
                return False
            preview_id = secrets.token_hex(16)
            preview = dict(payload)
            preview.update(version=1, previewId=preview_id, createdAt=int(time.time()))
            if current["view"] == "open":
                open_context = preview.get("openContext")
                if (
                    not _valid_open_context(open_context)
                    or open_context["desktop"] != current["viewerDesktop"]
                    or open_context["desktop"] != viewer_state.desktop_context()
                ):
                    raise BatchError("Open page endpoint changed during preparation")
            elif "openContext" in preview:
                raise BatchError("Batch preview scope changed during preparation")
            if preview.get("action") != current["action"] or not _valid_preview_record(
                preview, preview_id
            ):
                raise BatchError("Batch preview could not be saved safely")
            self._write(self.preview_path, preview)
            record = dict(current)
            record.update(status="ready", previewId=preview_id)
            self._write(self.preparation_path, record)
            return True

    def fail_preparation(self, request_id: str, error: object) -> None:
        with self.locked():
            current = self._read_preparation_locked(request_id)
            if current is None or current["status"] != "pending":
                return
            record = dict(current)
            record.update(status="failed", error=_short(error) or "Preview preparation failed")
            self._write(self.preparation_path, record)

    def discard_preparation(self, request_id: str) -> None:
        with self.locked():
            raw = self._read(self.preparation_path)
            if not isinstance(raw, Mapping) or raw.get("requestId") != request_id:
                return
            preview = self._read_preview()
            if preview is not None and preview.get("previewId") == raw.get("previewId"):
                self.preview_path.unlink(missing_ok=True)
            self.preparation_path.unlink(missing_ok=True)

    def write_preview(self, payload: Mapping[str, object]) -> str:
        targets = payload.get("targets")
        exclusions = payload.get("exclusions")
        if (
            not isinstance(targets, list)
            or len(targets) > _MAX_TARGETS
            or not isinstance(exclusions, list)
        ):
            raise BatchError("Batch preview is too large")
        preview_id = secrets.token_hex(16)
        record = dict(payload)
        record.update({"version": 1, "previewId": preview_id, "createdAt": int(time.time())})
        with self.locked():
            current = self._active_job_locked()
            if current is not None:
                raise BatchBusy(str(current.get("jobId", "")))
            self.preparation_path.unlink(missing_ok=True)
            self._write(self.preview_path, record)
        return preview_id

    def read_preview(self, preview_id: str) -> Mapping[str, object] | None:
        if not _ID.fullmatch(preview_id):
            return None
        with self.locked():
            record = self._read_preview()
            if (
                record is None
                or record.get("version") != 1
                or record.get("previewId") != preview_id
                or isinstance(record.get("createdAt"), bool)
                or not isinstance(record.get("createdAt"), int)
                or int(time.time()) - int(record["createdAt"]) > _PREVIEW_SECONDS
                or record.get("action") not in _ACTIONS
                or not isinstance(record.get("targets"), list)
                or len(record["targets"]) > _MAX_TARGETS
                or not isinstance(record.get("exclusions"), list)
                or not _valid_preview_record(record, preview_id)
            ):
                return None
            return record

    def discard_preview(self, preview_id: str) -> bool:
        """Remove only the current unconfirmed preview with this identity."""
        if not _ID.fullmatch(preview_id):
            return False
        with self.locked():
            record = self._read_preview()
            if record is None or record.get("previewId") != preview_id:
                return False
            try:
                self.preview_path.unlink()
            except FileNotFoundError:
                return False
            return True

    def _worker_active_locked(self) -> bool:
        with self.worker_lock(blocking=False) as acquired:
            return not acquired

    def _mark_abandoned_locked(self, job: dict[str, object], reason: str) -> None:
        job["status"] = "failed"
        job["updatedAt"] = int(time.time())
        job["stopReason"] = reason
        self._write(self.job_path, job)

    def _active_job_locked(self) -> Mapping[str, object] | None:
        raw = self._read_job()
        if raw is None or raw.get("status") not in {"queued", "running"}:
            return None
        job = dict(raw)
        if self._worker_active_locked():
            return job
        updated = job.get("updatedAt")
        if isinstance(updated, int) and int(time.time()) - updated <= _QUEUED_SECONDS:
            return job
        reason = (
            "Batch worker stopped before starting"
            if job.get("status") == "queued"
            else "Batch worker stopped; the last operation may have an ambiguous outcome"
        )
        self._mark_abandoned_locked(job, reason)
        return None

    def current_job(self, job_id: str | None = None) -> Mapping[str, object] | None:
        if job_id is not None and not _ID.fullmatch(job_id):
            return None
        with self.locked():
            job = self._read_job()
            if job is None:
                return None
            if job_id is not None and job.get("jobId") != job_id:
                return None
            return job

    def active_job(self) -> Mapping[str, object] | None:
        with self.locked():
            active = self._active_job_locked()
            return dict(active) if active is not None else None

    def consume_and_submit(
        self,
        preview_id: str,
        *,
        spawn: bool = True,
    ) -> tuple[str, Mapping[str, object]]:
        if not _ID.fullmatch(preview_id):
            raise BatchError("Batch preview has expired")
        with self.locked():
            active = self._active_job_locked()
            if active is not None:
                raise BatchBusy(str(active.get("jobId", "")))
            preview = self._read_preview()
            if (
                preview is None
                or preview.get("version") != 1
                or preview.get("previewId") != preview_id
                or preview.get("action") not in _ACTIONS
                or not isinstance(preview.get("backend"), Mapping)
                or not isinstance(preview.get("targets"), list)
                or not preview["targets"]
                or len(preview["targets"]) > _MAX_TARGETS
                or bool(preview.get("stopReason"))
                or type(preview.get("createdAt")) is not int
                or not 0 <= int(time.time()) - preview["createdAt"] <= _PREVIEW_SECONDS
                or not _valid_preview_record(preview, preview_id)
            ):
                raise BatchError("Batch preview has expired or is not confirmable")
            targets = operation_targets(preview)
            if not targets:
                raise BatchError("Batch preview has no windows to open or close")
            open_context = preview.get("openContext")
            if (
                open_context is not None
                and open_context["desktop"] != viewer_state.desktop_context()
            ):
                raise BatchError("Open page endpoint changed; prepare a new preview")
            job_id = secrets.token_hex(16)
            now = int(time.time())
            job: dict[str, object] = {
                "version": 1,
                "jobId": job_id,
                "previewId": preview_id,
                "status": "queued",
                "action": preview["action"],
                "backend": dict(preview["backend"]),
                "scope": _short(preview.get("scope")),
                "targets": targets,
                "results": [],
                "counts": {"done": 0, "already": 0, "skipped": 0, "failed": 0},
                "createdAt": now,
                "updatedAt": now,
                "stopReason": "",
                **({"openContext": dict(open_context)} if open_context is not None else {}),
            }
            self._write(self.job_path, job)
            self.preparation_path.unlink(missing_ok=True)
            try:
                self.preview_path.unlink()
            except FileNotFoundError:
                pass
            if spawn:
                try:
                    _spawn_worker(job_id)
                except OSError as error:
                    job["status"] = "failed"
                    job["updatedAt"] = int(time.time())
                    job["stopReason"] = "Could not start the batch worker"
                    self._write(self.job_path, job)
                    raise BatchError("Could not start the batch worker") from error
            return job_id, job

    def worker_job(self, job_id: str) -> Mapping[str, object] | None:
        if not _ID.fullmatch(job_id):
            return None
        with self.locked():
            job = self._read_job()
            if job is None or job.get("jobId") != job_id or job.get("status") != "queued":
                return None
            return job

    def set_running(self, job_id: str) -> Mapping[str, object] | None:
        with self.locked():
            job = self._read_job()
            if job is None or job.get("jobId") != job_id or job.get("status") != "queued":
                return None
            result = dict(job)
            result["status"] = "running"
            result["updatedAt"] = int(time.time())
            self._write(self.job_path, result)
            return result

    def update_job(self, job: Mapping[str, object]) -> None:
        with self.locked():
            current = self._read_job()
            if current is None or current.get("jobId") != job.get("jobId"):
                raise BatchError("Batch job state changed")
            self._write(self.job_path, job)


def _spawn_worker(job_id: str) -> None:
    _spawn_helper("_batch-worker", job_id)


def _spawn_helper(branch: str, record_id: str) -> None:
    if branch not in {"_batch-worker", "_batch-prepare"} or not _ID.fullmatch(record_id):
        raise BatchError("Invalid batch job identifier")
    entrypoint = Path(__file__).resolve().parents[1] / "bin" / "rofi-agent-plus"
    command = (
        [sys.executable, str(entrypoint), branch, record_id]
        if entrypoint.is_file()
        else [sys.executable, "-m", "rofi_agent_plus", branch, record_id]
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("ROFI_")}
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=environment,
    )


def preparation_main(
    request_id: str,
    *,
    store: BatchStateStore | None = None,
    cache_store: CacheStore | None = None,
) -> int:
    """Finite read-only preparation; only explicit Confirm can submit a job."""
    if not _ID.fullmatch(request_id):
        return 2
    store = store or BatchStateStore()
    try:
        request = store.read_preparation(request_id)
        if request is None or request["status"] != "pending":
            return 0

        def is_current() -> bool:
            current = store.read_preparation(request_id)
            return current is not None and current["status"] == "pending"

        preview = build_preview(
            cache_store or CacheStore(),
            load_config(),
            Scope(str(request["view"]), request["hostId"], request.get("viewerDesktop")),
            str(request["action"]),
            is_current=is_current,
        )
        store.finish_preparation(request_id, preview)
        return 0
    except Exception as error:  # noqa: BLE001 - finite preparation boundary
        store.fail_preparation(request_id, error)
        return 1


def _valid_target(
    target: object,
    action: str,
    backend_identity: Mapping[str, object],
) -> bool:
    fields = {
        "hostId",
        "kind",
        "id",
        "name",
        "host",
        "reference",
        "requiredOption",
        "mode",
        "viewers",
    }
    if not isinstance(target, Mapping) or set(target) != fields:
        return False
    identity = _target_identity(target)
    if identity is None:
        return False
    if not _valid_provider_identifier(identity[1], identity[2]):
        return False
    if any(
        not isinstance(target.get(field), str)
        or not target[field]
        or len(target[field]) > _MAX_TEXT
        for field in ("name", "host")
    ):
        return False
    revision = backend_identity.get("meshRevision")
    try:
        reference = _reference(
            target.get("reference"),
            identity[0],
            revision if isinstance(revision, str) else None,
        )
    except LifecycleError:
        return False
    if (
        reference.host_id != identity[0]
        or not isinstance(target.get("reference"), Mapping)
        or set(target["reference"])
        != {"meshRevision", "serverGeneration", "sessionId", "createdAt", "observedName", "hostId"}
    ):
        return False
    raw_option = target.get("requiredOption")
    expected_option = (str(_PROVIDER_OPTIONS[identity[1]][0]), identity[2])
    if raw_option is None:
        if target.get("mode") not in (
            {"already_closed", "close"} if action == ACTION_CLOSE else {"already_open", "open"}
        ):
            return False
    elif (
        not isinstance(raw_option, list)
        or len(raw_option) != 2
        or not all(isinstance(value, str) for value in raw_option)
        or tuple(raw_option) != expected_option
    ):
        return False
    mode = target.get("mode")
    valid_modes = (
        {"already_closed", "close"} if action == ACTION_CLOSE else {"already_open", "open"}
    )
    if mode not in valid_modes:
        return False
    viewers = target.get("viewers")
    if not isinstance(viewers, list) or len(viewers) > contract_viewers.MAX_VIEWERS:
        return False
    seen: set[str] = set()
    seen_windows: set[int] = set()
    for viewer in viewers:
        if not isinstance(viewer, Mapping) or set(viewer) != {"viewerId", "windowId"}:
            return False
        viewer_id, window_id = viewer.get("viewerId"), viewer.get("windowId")
        if (
            not isinstance(viewer_id, str)
            or not viewer_id
            or len(viewer_id) > contract_viewers.MAX_FIELD
            or any(ord(char) < 32 or ord(char) == 127 for char in viewer_id)
            or any(unicodedata.category(char).startswith("C") for char in viewer_id)
            or viewer_id in seen
            or type(window_id) is not int
            or not 0 <= window_id <= 2**63 - 1
            or window_id in seen_windows
        ):
            return False
        seen.add(viewer_id)
        seen_windows.add(window_id)
    if mode == "close" and not viewers:
        return False
    if mode == "already_closed" and viewers:
        return False
    if mode == "already_open" and not viewers:
        return False
    if mode == "open" and viewers:
        return False
    return True


def _valid_job_record(value: object, job_id: str) -> bool:
    fields = {
        "version",
        "jobId",
        "previewId",
        "status",
        "action",
        "backend",
        "scope",
        "targets",
        "results",
        "counts",
        "createdAt",
        "updatedAt",
        "stopReason",
    }
    if not isinstance(value, Mapping) or set(value) - {"openContext"} != fields:
        return False
    if "openContext" in value and (
        not _valid_open_context(value["openContext"]) or value.get("action") != ACTION_CLOSE
    ):
        return False
    backend = value.get("backend")
    if (
        type(value.get("version")) is not int
        or value.get("version") != 1
        or value.get("jobId") != job_id
        or not isinstance(value.get("previewId"), str)
        or not _ID.fullmatch(value["previewId"])
        or value.get("status") != "queued"
        or value.get("action") not in _ACTIONS
        or not isinstance(backend, Mapping)
        or not isinstance(value.get("scope"), str)
        or len(value["scope"]) > _MAX_TEXT
        or not isinstance(value.get("targets"), list)
        or len(value["targets"]) > _MAX_TARGETS
        or not isinstance(value.get("results"), list)
        or value["results"]
        or not isinstance(value.get("counts"), Mapping)
        or set(value["counts"]) != {"done", "already", "skipped", "failed"}
        or any(type(count) is not int or count != 0 for count in value["counts"].values())
        or type(value.get("createdAt")) is not int
        or type(value.get("updatedAt")) is not int
        or not isinstance(value.get("stopReason"), str)
        or value.get("stopReason")
    ):
        return False
    try:
        identity = _backend_identity(backend)
    except LifecycleError:
        return False
    return all(_valid_target(target, str(value["action"]), identity) for target in value["targets"])


def _valid_preview_record(value: object, preview_id: str) -> bool:
    fields = {
        "action",
        "backend",
        "scope",
        "targets",
        "exclusions",
        "createdAt",
        "version",
        "previewId",
    }
    optional = {"stopReason", "openContext"}
    if (
        not isinstance(value, Mapping)
        or not fields.issubset(value)
        or set(value) - fields - optional
        or type(value.get("version")) is not int
        or value.get("version") != 1
        or value.get("previewId") != preview_id
        or value.get("action") not in _ACTIONS
        or not isinstance(value.get("backend"), Mapping)
        or not isinstance(value.get("scope"), str)
        or len(value["scope"]) > _MAX_TEXT
        or type(value.get("createdAt")) is not int
        or not isinstance(value.get("targets"), list)
        or len(value["targets"]) > _MAX_TARGETS
        or not isinstance(value.get("exclusions"), list)
        or len(value["exclusions"]) > _MAX_TARGETS
        or ("stopReason" in value and not isinstance(value.get("stopReason"), str))
        or ("stopReason" in value and len(value["stopReason"]) > _MAX_TEXT)
    ):
        return False
    try:
        identity = _backend_identity(value["backend"])
    except LifecycleError:
        return False
    if "openContext" in value and (
        not _valid_open_context(value["openContext"])
        or value["action"] == ACTION_RESUME
        and value["targets"]
    ):
        return False
    if not all(
        _valid_target(target, str(value["action"]), identity) for target in value["targets"]
    ):
        return False
    for exclusion in value["exclusions"]:
        if (
            not isinstance(exclusion, Mapping)
            or set(exclusion) != {"name", "provider", "host", "reason"}
            or any(
                not isinstance(exclusion.get(key), str) or len(exclusion[key]) > _MAX_TEXT
                for key in ("name", "provider", "host", "reason")
            )
        ):
            return False
    return True


def _target_identity(target: Mapping[str, object]) -> tuple[str, str, str] | None:
    host_id, kind, identifier = target.get("hostId"), target.get("kind"), target.get("id")
    if (
        not isinstance(host_id, str)
        or not _HOST_ID.fullmatch(host_id)
        or not isinstance(kind, str)
        or kind not in _PROVIDERS
        or not isinstance(identifier, str)
        or not identifier
        or len(identifier) > _MAX_TEXT
        or not _valid_provider_identifier(kind, identifier)
    ):
        return None
    return host_id, kind, identifier


def _current_active_row(
    snapshot: Mapping[str, object],
    target: Mapping[str, object],
    expected_identity: Mapping[str, object],
) -> tuple[Mapping[str, object] | None, str]:
    identity = _target_identity(target)
    if identity is None or _backend_identity(snapshot.get("backend")) != dict(expected_identity):
        return None, "current authority changed"
    host_id, kind, identifier = identity
    record = _host_record(snapshot, host_id)
    if record is None:
        return None, "host observations are unavailable"
    rows = record.get("sessions")
    if not isinstance(rows, list):
        return None, "host observations are unavailable"
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("hostId") == host_id
        and row.get("kind") == kind
        and row.get("id") == identifier
        and row.get("contractMode") is True
        and row.get("backend") == dict(expected_identity)
    ]
    if len(matches) != 1:
        return None, "active provider session ended or became ambiguous"
    row = matches[0]
    if (
        row.get("sourceObservation") != "current"
        or row.get("tmuxStale") is True
        or row.get("tmuxAmbiguous") is True
        or not (row.get("active") is True or row.get("activityState") == "waiting")
        or not _observation_ok(record, "activity")
        or not _observation_ok(record, str(kind))
        or not _observation_ok(record, "tmux")
    ):
        return None, "current active evidence is unavailable"
    try:
        revision = expected_identity.get("meshRevision")
        reference = _reference(
            row.get("tmux"), host_id, revision if isinstance(revision, str) else None
        )
    except LifecycleError:
        return None, "current tmux association is unavailable"
    expected_ref = target.get("reference")
    if not isinstance(expected_ref, Mapping) or _ref_key(_ref_payload(reference)) != _ref_key(
        expected_ref
    ):
        return None, "tmux session ended or was replaced"
    if target.get("requiredOption") is not None and _provider_option(row) != tuple(
        target["requiredOption"]
    ):
        return None, "provider option changed or is no longer verified"
    return row, ""


def _append_result(
    job: dict[str, object],
    target: Mapping[str, object],
    status: str,
    reason: str = "",
) -> None:
    counts = job.get("counts")
    if not isinstance(counts, dict):
        counts = {"done": 0, "already": 0, "skipped": 0, "failed": 0}
        job["counts"] = counts
    if status not in counts:
        status = "failed"
    counts[status] = int(counts.get(status, 0)) + 1
    results = job.get("results")
    if not isinstance(results, list):
        results = []
        job["results"] = results
    if len(results) < _MAX_RESULTS:
        results.append(
            {
                "name": _short(target.get("name") or target.get("id") or "Agent"),
                "provider": _short(target.get("kind") or "Agent"),
                "host": _short(target.get("host") or target.get("hostId") or "host"),
                "status": status,
                "reason": _short(reason),
            }
        )


def _job_should_stop(error: BaseException) -> bool:
    return isinstance(error, contract_viewers.ViewerError) and error.stop_batch


def _record_stop(job: dict[str, object], reason: str) -> None:
    job["stopReason"] = _short(reason)
    targets = job.get("targets")
    results = job.get("results")
    completed = len(results) if isinstance(results, list) else 0
    if isinstance(targets, list):
        for target in targets[completed:]:
            if isinstance(target, Mapping):
                _append_result(
                    job, target, "skipped", "Not run after authority or protocol failure"
                )
    job["status"] = "failed"
    job["updatedAt"] = int(time.time())


def _run_job_target(
    store: CacheStore,
    config: PickerConfig,
    job: dict[str, object],
    target: Mapping[str, object],
) -> tuple[str, str, bool]:
    action = job.get("action")
    expected_identity = job.get("backend")
    if action not in _ACTIONS or not isinstance(expected_identity, Mapping):
        return "failed", "batch job has an invalid action or authority", True
    open_context = job.get("openContext")
    if open_context is not None and (
        not _valid_open_context(open_context)
        or action != ACTION_CLOSE
        or open_context["desktop"] != viewer_state.desktop_context()
    ):
        return "failed", "Open page endpoint or action changed", True
    mode = target.get("mode")
    host_id = target.get("hostId")
    reference_payload = target.get("reference")
    if not isinstance(host_id, str) or not isinstance(reference_payload, Mapping):
        return "failed", "batch target is invalid", True
    revision = expected_identity.get("meshRevision")
    try:
        reference = _reference(
            reference_payload,
            host_id,
            revision if isinstance(revision, str) else None,
        )
    except LifecycleError as error:
        return "failed", str(error), True
    raw_option = target.get("requiredOption")
    required_option = (
        (str(raw_option[0]), str(raw_option[1]))
        if isinstance(raw_option, list) and len(raw_option) == 2
        else None
    )
    if action == ACTION_CLOSE:
        if mode == "already_closed":
            return "already", "no verified viewer was open in the preview", False
        if mode != "close":
            return "failed", "close target is invalid", True
        viewers = target.get("viewers")
        if not isinstance(viewers, list) or not viewers:
            return "failed", "frozen viewer list is invalid", True
        closed = 0
        already = 0
        failure = ""
        for viewer in viewers:
            if not isinstance(viewer, Mapping) or not isinstance(viewer.get("viewerId"), str):
                return "failed", "frozen viewer identity is invalid", True
            # Resolve a fresh authority for every public operation. The Tmux
            # Plus command independently revalidates the complete session and
            # frozen viewer handle before requesting closure.
            context = store.presentation_context(config)
            try:
                backend = _backend_for(context)
                if _backend_identity(context.backend) != dict(expected_identity):
                    return "skipped", "current authority changed", True
                if open_context is not None:
                    mesh = getattr(backend, "mesh", None)
                    local = getattr(mesh, "local", None)
                    if (
                        getattr(local, "host_id", None) != open_context["endpointHostId"]
                        or viewer_state.desktop_context() != open_context["desktop"]
                    ):
                        return "skipped", "Open page endpoint changed", True
                newly_closed = contract_viewers.close_viewer(
                    backend,
                    reference,
                    str(viewer["viewerId"]),
                    required_option=required_option,
                )
                if newly_closed:
                    closed += 1
                else:
                    already += 1
            except contract_viewers.ViewerError as error:
                if error.stop_batch:
                    return "failed", str(error), True
                failure = _short(error)
                break
            except StaleMeshError as error:
                return "failed", _short(error), True
            except BatchError as error:
                return "failed", _short(error), True
            except (OSError, engine.PickerError) as error:
                failure = _short(error) or "viewer close failed"
                break
        if failure:
            return "failed", failure, False
        if closed:
            return "done", f"closed {closed}; {already} already closed", False
        return "already", "viewer was already closed", False
    if action == ACTION_RESUME:
        if mode not in {"already_open", "open"}:
            return "failed", "resume target is invalid", True
        # A fresh prepared authority prevents a long batch from reusing the
        # expired deadline held by a previous ContractBackend instance.
        context = store.presentation_context(config)
        try:
            backend = _backend_for(context)
        except BatchError as error:
            return "failed", str(error), True
        if _backend_identity(context.backend) != dict(expected_identity):
            return "skipped", "current authority changed", True
        try:
            snapshot = store.refresh(
                config,
                force=True,
                require_fresh=True,
                context=context,
                host_ids=(host_id,),
            )
        except StaleMeshError as error:
            return "failed", _short(error), True
        except engine.PickerError as error:
            # A selected host that cannot be refreshed is a target skip. A
            # later host can still succeed under the same frozen Mesh.
            return "skipped", _short(error) or "selected host is unreachable", False
        if _backend_identity(snapshot.get("backend")) != dict(expected_identity):
            return "skipped", "current authority changed", True
        row, reason = _current_active_row(snapshot, target, expected_identity)
        if row is None:
            return "skipped", reason, False
        if required_option is not None and _provider_option(row) != required_option:
            return "skipped", "provider option changed or is no longer verified", False
        try:
            launched = contract_viewers.open_existing_viewer(
                backend,
                reference,
                required_option=required_option,
            )
        except contract_viewers.ViewerError as error:
            if error.stop_batch:
                return "failed", str(error), True
            return "failed", str(error), False
        if launched:
            return "done", "viewer launched for the existing tmux session", False
        return "already", "viewer is already open", False
    return "failed", "batch action is invalid", True


def worker_main(
    job_id: str,
    *,
    store: BatchStateStore | None = None,
    cache_store: CacheStore | None = None,
) -> int:
    """Run one explicitly dispatched finite worker job."""

    if not _ID.fullmatch(job_id):
        return 2
    state_store = store or BatchStateStore()
    presentation_store = cache_store or CacheStore()
    with state_store.worker_lock(blocking=False) as acquired:
        if not acquired:
            return 0
        queued = state_store.worker_job(job_id)
        if queued is None:
            return 0
        if not _valid_job_record(queued, job_id):
            invalid = dict(queued)
            invalid["status"] = "failed"
            invalid["updatedAt"] = int(time.time())
            invalid["stopReason"] = "Batch job state is malformed; no operation was run"
            try:
                state_store.update_job(invalid)
            except BatchError:
                pass
            return 1
        job_record = state_store.set_running(job_id)
        if not isinstance(job_record, Mapping):
            return 0
        job = dict(job_record)
        try:
            config = PickerConfig()
            from .config import load_config

            config = load_config()
            targets = job.get("targets")
            if not isinstance(targets, list) or len(targets) > _MAX_TARGETS:
                raise BatchError("Batch target list is invalid")
            for target in targets:
                if not isinstance(target, Mapping):
                    _record_stop(job, "Batch target list is invalid")
                    break
                result, reason, stop = _run_job_target(presentation_store, config, job, target)
                _append_result(job, target, result, reason)
                job["updatedAt"] = int(time.time())
                state_store.update_job(job)
                if stop:
                    _record_stop(job, reason or "Batch stopped after an authority failure")
                    state_store.update_job(job)
                    return 0
            if job.get("status") == "running":
                job["status"] = "complete"
                job["updatedAt"] = int(time.time())
                state_store.update_job(job)
            return 0
        except Exception as error:  # a finite worker must persist an explicit terminal result
            _record_stop(job, _short(error) or "Batch worker failed")
            try:
                state_store.update_job(job)
            except BatchError:
                pass
            return 1


def job_summary(job: Mapping[str, object]) -> str:
    counts = job.get("counts")
    counts = counts if isinstance(counts, Mapping) else {}
    return (
        f"Done {counts.get('done', 0)} · already open/closed {counts.get('already', 0)} · "
        f"skipped {counts.get('skipped', 0)} · failed {counts.get('failed', 0)}"
    )


def result_rows(job: Mapping[str, object]) -> list[dict[str, str]]:
    raw = job.get("results")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw[:_MAX_RESULTS]:
        if not isinstance(item, Mapping):
            continue
        result.append(
            {
                "name": _short(item.get("name") or "Agent"),
                "provider": _short(item.get("provider") or ""),
                "host": _short(item.get("host") or "host"),
                "status": _short(item.get("status") or "failed"),
                "reason": _short(item.get("reason") or ""),
            }
        )
    return result
