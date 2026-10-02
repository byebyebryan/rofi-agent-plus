"""Rofi script-mode adapter for Agent Plus."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from . import batch, engine
from .cache import CacheStore, PresentationContext
from .config import PickerConfig, load_config
from .contract_lifecycle import ContractLifecycle, LifecycleError, fast_open_selection
from .view_preferences import (
    ViewPreference,
    ViewPreferenceStore,
    encode_last_used,
    parse_last_used,
)
from .viewer_state import FRESH_SECONDS

ROFI_RETV_SELECTED = 1
ROFI_RETV_CUSTOM_1 = 10
ROFI_RETV_CUSTOM_2 = 11
ROFI_RETV_CUSTOM_3 = 12
ROFI_RETV_CUSTOM_4 = 13
# 15 is retained as a migration guard for an older managed invocation that
# still routed Escape through a script callback.  It closes immediately in
# ``run_rofi`` and must never render a replacement list.
ROFI_RETV_CUSTOM_6 = 15
ROFI_RETV_CUSTOM_7 = 16
ROFI_RETV_CUSTOM_8 = 17
ROFI_RETV_CUSTOM_19 = 28
MAX_MESSAGE_LENGTH = 360
AUTO_REFRESH_POLL_SECONDS = 1
AUTO_REFRESH_MAX_SECONDS = 30
AUTO_REFRESH_DATA_PREFIX = "background-refresh:"
AUTO_REFRESH_IDLE_DATA = "idle"
ERROR_NOTICE_SECONDS = 3
FAST_OPEN_FALLBACK_CODES = frozenset(
    {"stale_session", "session_not_found", "stale_mesh", "invalid_input"}
)
ERROR_NOTICE_DATA_PREFIX = "error-notice:"
CHECK_NOTICE_SECONDS = 2
CHECK_NOTICE_DATA_PREFIX = "check-notice:"
NAVIGATION_DATA_PREFIX = "navigation:"
NAVIGATION_DATA_VERSION = 2
ACTION_DATA_PREFIX = "action:"
ACTION_DATA_VERSION = 1
ACTION_RESUME = "resume"
ACTION_CLOSE = batch.ACTION_CLOSE
ACTION_NEW = "new-session-here"
ACTION_ORDER = (ACTION_RESUME, ACTION_CLOSE, ACTION_NEW)
BATCH_UI_DATA_PREFIX = "batch-ui:"
_BATCH_SCREENS = frozenset({"preparing", "preview", "job"})
_BATCH_ROW_TYPES = frozenset({"batch", "batch-confirm", "batch-target", "batch-job"})
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DISPLAY_CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")

PROVIDER_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "opencode": "OpenCode",
}
PROVIDER_SEARCH_TERMS = {
    "codex": "codex",
    "claude": "claude claude-code claude code",
    "opencode": "opencode open-code open code",
}
PROVIDER_ICON_PATHS = {
    kind: Path(__file__).resolve().parent / "assets" / "providers" / f"{kind}.svg"
    for kind in PROVIDER_LABELS
}
FALLBACK_ICON_PATH = Path(__file__).resolve().parent / "assets" / "providers" / "generic.svg"
ROW_SEPARATOR = "\n"
ROFI_RECORD_SEPARATOR = "\t"
ROFI_DELIMITER_VALUE = r"\t"
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z", re.ASCII)
VIEW_ALL = "all"
VIEW_LOCAL = "local"
VIEW_HOST = "host"
VIEW_ACTIVE = "active"
_VIEWS = frozenset({VIEW_ACTIVE, VIEW_ALL, VIEW_LOCAL, VIEW_HOST})
SessionIdentity = tuple[str, str, str]


def sanitize(value: object) -> str:
    """Remove control characters that could corrupt Rofi's script protocol."""

    text = str(value) if value is not None else ""
    return _CONTROL_CHARS.sub(" ", text).strip()


def _contract_text(value: object, *, required: bool = True) -> bool:
    return (
        isinstance(value, str)
        and (bool(value) or not required)
        and len(value) <= 16 * 1024
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


@dataclass(frozen=True)
class NavigationState:
    """One flat scope in the immutable Host Mesh presentation catalog.

    ``active``, ``all``, and ``local`` are typed scopes.  ``host`` carries an
    authoritative Host Mesh id rather than a display label.  Continuation
    data is untrusted, so malformed values normalize to the safe mixed scope.
    """

    view: str = VIEW_ALL
    host_id: str | None = None

    def __post_init__(self) -> None:
        view = self.view if isinstance(self.view, str) and self.view in _VIEWS else VIEW_ALL
        host_id = self.host_id
        if view != VIEW_HOST:
            host_id = None
        elif not isinstance(host_id, str) or not _HOST_ID.fullmatch(host_id) or len(host_id) > 256:
            view = VIEW_ALL
            host_id = None
        object.__setattr__(self, "view", view)
        object.__setattr__(self, "host_id", host_id)

    @property
    def nested(self) -> bool:
        """Compatibility spelling; P8 has no nested browsing state."""

        return False

    @property
    def is_default(self) -> bool:
        return self.view == VIEW_ALL

    def root(self) -> NavigationState:
        """Return the safe root used by stale P7 continuation data."""

        return NavigationState()


@dataclass(frozen=True)
class ContinuationState:
    """Rofi continuation data shared by navigation and refresh callbacks.

    Deadlines are kept in their wire-format form until :meth:`active` is
    called.  The timeout callback needs to see an expired deadline in order to
    clear it, while navigation callbacks only need the still-live portions.
    Keeping this small state object in one place also prevents a navigation
    callback from accidentally dropping a background refresh or notice.
    """

    navigation: NavigationState = NavigationState()
    refresh_deadline: float | None = None
    error_deadline: float | None = None
    error_message: str = ""
    check_deadline: float | None = None
    action: str = ACTION_RESUME
    action_valid: bool = True
    last_used: SessionIdentity | None = None
    batch_state: BatchUIState | None = None

    @property
    def has_lifecycle(self) -> bool:
        return (
            self.refresh_deadline is not None
            or self.error_deadline is not None
            or self.check_deadline is not None
        )

    def active(self, now: float | None = None) -> ContinuationState:
        """Return only unexpired refresh/notice components."""

        current = time.time() if now is None else now

        def live(deadline: float | None) -> float | None:
            if deadline is None:
                return None
            try:
                return deadline if math.isfinite(deadline) and deadline > current else None
            except (TypeError, ValueError, OverflowError):
                return None

        error_deadline = live(self.error_deadline)
        return ContinuationState(
            navigation=self.navigation,
            refresh_deadline=live(self.refresh_deadline),
            error_deadline=error_deadline,
            error_message=self.error_message if error_deadline is not None else "",
            check_deadline=live(self.check_deadline),
            action=self.action,
            action_valid=self.action_valid,
            last_used=self.last_used,
            batch_state=self.batch_state,
        )


@dataclass(frozen=True)
class BatchUIState:
    """Small typed Rofi state; frozen target lists stay in private files."""

    screen: str
    source_identity: SessionIdentity | None = None
    # Preview operation; the shared Resume/Close/New selection lives in
    # ContinuationState.action and survives preview/job exits.
    action: str | None = None
    record_id: str | None = None


def _batch_ui_data(state: BatchUIState | None) -> str | None:
    if state is None:
        return None
    payload: dict[str, object] = {"version": 1, "screen": state.screen}
    if state.source_identity is not None:
        payload["sourceIdentity"] = list(state.source_identity)
    if state.action is not None:
        payload["action"] = state.action
    if state.record_id is not None:
        payload["recordId"] = state.record_id
    encoded = quote(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), safe="")
    return BATCH_UI_DATA_PREFIX + encoded


def _parse_batch_ui_state(value: object) -> BatchUIState | None:
    if not isinstance(value, str):
        return None
    components = [
        part[len(BATCH_UI_DATA_PREFIX) :]
        for part in value.split(";")
        if part.startswith(BATCH_UI_DATA_PREFIX)
    ]
    if len(components) != 1 or not components[0] or len(components[0]) > 4096:
        return None
    try:
        payload = json.loads(unquote(components[0]))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, Mapping)
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
    ):
        return None
    screen = payload.get("screen")
    if not isinstance(screen, str) or screen not in _BATCH_SCREENS:
        # A 0.9 group continuation is no longer an action context. Treat it
        # as ordinary root state so it cannot invoke a hidden operation.
        return None
    allowed = {
        "preparing": {"version", "screen", "sourceIdentity", "action", "recordId"},
        "preview": {"version", "screen", "sourceIdentity", "action", "recordId"},
        "job": {"version", "screen", "sourceIdentity", "recordId"},
    }[screen]
    if set(payload) - allowed or not {"version", "screen"}.issubset(payload):
        return None
    source_identity: SessionIdentity | None = None
    raw_identity = payload.get("sourceIdentity")
    if raw_identity is not None:
        if not isinstance(raw_identity, list) or len(raw_identity) != 3:
            return None
        source_identity = _session_identity(
            {"hostId": raw_identity[0], "kind": raw_identity[1], "id": raw_identity[2]}
        )
        if source_identity is None:
            return None
    action = payload.get("action")
    record_id = payload.get("recordId")
    if screen in {"preparing", "preview"}:
        if action not in {batch.ACTION_CLOSE, batch.ACTION_RESUME}:
            return None
    elif action is not None:
        return None
    if screen in {"preparing", "preview"}:
        if not isinstance(record_id, str) or not batch._ID.fullmatch(record_id):
            return None
    elif screen == "job":
        if not isinstance(record_id, str) or not batch._ID.fullmatch(record_id):
            return None
    return BatchUIState(screen, source_identity, action, record_id)


def _protocol(key: str, value: object) -> str:
    return "\0" + key + "\x1f" + sanitize(value)


def _row_options(options: Sequence[tuple[str, object]]) -> str:
    """Encode all options for one row after a single NUL separator."""

    fields: list[str] = []
    for key, value in options:
        encoded_value = (
            _DISPLAY_CONTROL_CHARS.sub(" ", str(value)).strip()
            if key == "display"
            else sanitize(value)
        )
        fields.extend((sanitize(key), encoded_value))
    return "\0" + "\x1f".join(fields) if fields else ""


def _pango_escape(value: object) -> str:
    """Escape dynamic text before embedding it in row Pango markup."""

    # U+2028 is our intentional display line separator.  Do not let an input
    # value create an additional visual line, and keep the protocol itself
    # one physical LF-delimited row.
    text = sanitize(value).replace("\u0085", " ").replace("\u2028", " ").replace("\u2029", " ")
    return escape(text, quote=False)


def _shorten_cwd(value: object, width: int = 42) -> str:
    cwd = sanitize(value)
    if not cwd:
        return "~"
    home = str(Path.home())
    if cwd == home:
        cwd = "~"
    elif cwd.startswith(home + "/"):
        cwd = "~" + cwd[len(home) :]
    if len(cwd) <= width:
        return cwd
    return "…" + cwd[-(width - 1) :]


def _age(timestamp: object, now: float | None = None) -> str:
    try:
        numeric_timestamp = float(timestamp)
        if numeric_timestamp <= 0:
            return "unknown"
        seconds = max(0, int((now if now is not None else time.time()) - numeric_timestamp))
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 30:
        return f"{days}d"
    months = days // 30
    if months < 12:
        return f"{months}mo"
    return f"{days // 365}y"


def _session_key(session: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(session.get("hostId") or session.get("host") or "local"),
        str(session.get("kind") or ""),
        str(session.get("id") or ""),
    )


def _session_identity(session: Mapping[str, Any]) -> SessionIdentity | None:
    """Return the stable logical identity of one trusted presentation row."""

    host_id = session.get("hostId")
    kind = session.get("kind")
    identifier = session.get("id")
    if (
        not isinstance(host_id, str)
        or not host_id
        or len(host_id) > 256
        or not _HOST_ID.fullmatch(host_id)
        or not isinstance(kind, str)
        or kind not in PROVIDER_LABELS
        or not isinstance(identifier, str)
        or not identifier
    ):
        return None
    pattern = engine.OPENCODE_ID_PATTERN if kind == "opencode" else engine.UUID_PATTERN
    if pattern.fullmatch(identifier) is None:
        return None
    return host_id.casefold(), kind, identifier


def _parse_selected_identity(raw: object) -> SessionIdentity | None:
    """Parse only the row identity from untrusted callback metadata.

    Automatic refresh callbacks must never treat ``ROFI_INFO`` as lifecycle or
    authority data.  Invalid metadata simply disables the identity override;
    Rofi's normal cursor fallback remains in charge.
    """

    if not isinstance(raw, str) or not raw or len(raw) > 64 * 1024:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return _session_identity(payload) if isinstance(payload, Mapping) else None


def _recency_timestamp(value: object) -> float | None:
    """Return a usable positive recency timestamp, if one is present."""

    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp) or timestamp <= 0:
        return None
    return timestamp


def _session_sort_key(session: Mapping[str, Any]) -> tuple[object, ...]:
    """Sort sessions newest-first with deterministic, human-friendly ties."""

    timestamp = _recency_timestamp(session.get("recencyAt"))
    name = sanitize(session.get("name") or session.get("id") or "Agent")
    host = sanitize(session.get("host") or session.get("hostId") or "local")
    kind = sanitize(session.get("kind") or "")
    identifier = sanitize(session.get("id") or "")
    return (
        0 if timestamp is not None else 1,
        -(timestamp or 0),
        name.casefold(),
        name,
        host.casefold(),
        host,
        kind.casefold(),
        kind,
        identifier.casefold(),
        identifier,
    )


def _valid_sessions(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Copy renderable session rows while rejecting malformed identities."""

    rows = snapshot.get("sessions", []) if isinstance(snapshot, Mapping) else []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("kind")
        identifier = item.get("id")
        if not isinstance(kind, str) or kind not in PROVIDER_LABELS:
            continue
        if (
            not isinstance(identifier, str)
            or not identifier
            or item.get("contractMode") is not True
        ):
            continue
        result.append(dict(item))
    return result


def _session_host(session: Mapping[str, Any]) -> str:
    """Return the stable logical host id for a session row."""

    return sanitize(session.get("hostId") or session.get("host") or "local") or "local"


def _host_catalog(snapshot: Mapping[str, Any] | None) -> list[dict[str, object]]:
    """Return the validated, ordered Host Mesh catalog from a snapshot."""

    raw = snapshot.get("hostCatalog", []) if isinstance(snapshot, Mapping) else []
    if not isinstance(raw, list):
        return []
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            return []
        host_id = item.get("hostId")
        display = item.get("display")
        if (
            not isinstance(host_id, str)
            or not _HOST_ID.fullmatch(host_id)
            or len(host_id) > 256
            or host_id.casefold() in seen
            or not isinstance(display, str)
            or not display
            or len(display) > 16 * 1024
            or any(unicodedata.category(char).startswith("C") for char in display)
            or not isinstance(item.get("local"), bool)
        ):
            return []
        seen.add(host_id.casefold())
        result.append({"hostId": host_id, "display": display, "local": item["local"]})
    if result and (
        sum(item["local"] is True for item in result) != 1 or result[0]["local"] is not True
    ):
        return []
    return result


def _host_record(snapshot: Mapping[str, Any] | None, host_id: str) -> Mapping[str, Any] | None:
    hosts = snapshot.get("hosts") if isinstance(snapshot, Mapping) else None
    if not isinstance(hosts, Mapping):
        return None
    wanted = host_id.casefold()
    for key, value in hosts.items():
        if isinstance(key, str) and key.casefold() == wanted and isinstance(value, Mapping):
            return value
    return None


def _scope_host_id(snapshot: Mapping[str, Any] | None, navigation: NavigationState) -> str | None:
    if navigation.view == VIEW_HOST:
        return navigation.host_id
    if navigation.view == VIEW_LOCAL:
        local = next((item for item in _host_catalog(snapshot) if item["local"]), None)
        return str(local["hostId"]) if local is not None else None
    return None


def _canonical_navigation(
    snapshot: Mapping[str, Any] | None, navigation: NavigationState
) -> NavigationState:
    """Normalize a continuation scope against its immutable catalog."""

    if navigation.view == VIEW_ACTIVE:
        return navigation
    catalog = _host_catalog(snapshot)
    if not catalog:
        return NavigationState()
    local = next((item for item in catalog if item["local"]), None)
    remotes = [item for item in catalog if not item["local"]]
    if not remotes:
        return NavigationState(VIEW_LOCAL) if local is not None else NavigationState()
    if navigation.view == VIEW_LOCAL:
        return navigation if local is not None else NavigationState()
    if navigation.view == VIEW_HOST:
        wanted = (navigation.host_id or "").casefold()
        match = next((item for item in catalog if str(item["hostId"]).casefold() == wanted), None)
        if match is not None:
            return (
                NavigationState(VIEW_LOCAL)
                if match["local"]
                else NavigationState(VIEW_HOST, str(match["hostId"]))
            )
    return NavigationState()


def _scope_ring(snapshot: Mapping[str, Any] | None) -> list[NavigationState]:
    """Build the stable Active/All/Local/remote ring from Host Mesh order."""

    catalog = _host_catalog(snapshot)
    if not catalog:
        return [NavigationState(VIEW_ACTIVE), NavigationState()]
    local = next((item for item in catalog if item["local"]), None)
    remotes = [item for item in catalog if not item["local"]]
    if not remotes:
        return (
            [NavigationState(VIEW_ACTIVE), NavigationState(VIEW_LOCAL)]
            if local is not None
            else [NavigationState(VIEW_ACTIVE), NavigationState()]
        )
    ring = (
        [NavigationState(VIEW_ACTIVE), NavigationState(), NavigationState(VIEW_LOCAL)]
        if local is not None
        else [NavigationState(VIEW_ACTIVE), NavigationState()]
    )
    ring.extend(NavigationState(VIEW_HOST, str(item["hostId"])) for item in remotes)
    return ring


def _active_sessions(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Collect observed-active rows from validated per-host cache records."""

    candidates: list[dict[str, Any]] = []
    seen: set[SessionIdentity] = set()
    for host in _host_catalog(snapshot):
        host_id = str(host["hostId"])
        record = _host_record(snapshot, host_id)
        raw_rows = record.get("sessions") if isinstance(record, Mapping) else None
        if not isinstance(raw_rows, list):
            continue
        for row in _valid_sessions({"sessions": raw_rows}):
            if _session_host(row).casefold() != host_id.casefold():
                continue
            identity = _session_identity(row)
            if identity is None or identity in seen:
                continue
            seen.add(identity)
            candidates.append(row)
    active = [row for row in candidates if _row_observation(row, snapshot, None)[2]]
    return sorted(active, key=_session_sort_key)


def _group_sessions(
    snapshot: Mapping[str, Any] | None,
    navigation: NavigationState,
) -> list[dict[str, Any]]:
    """Return deduplicated cached active/waiting rows in the batch owner scope.

    Per-host records are uncapped, unlike the flattened All page. Observation
    status still controls eligibility for the visible active set: retained or
    failed activity evidence must not receive the membership accent.
    """

    catalog = _host_catalog(snapshot)
    if navigation.view in {VIEW_ACTIVE, VIEW_ALL}:
        wanted_hosts = {str(item["hostId"]).casefold() for item in catalog}
    else:
        scoped_host = _scope_host_id(snapshot, navigation)
        wanted_hosts = {scoped_host.casefold()} if scoped_host is not None else set()
    rows: list[dict[str, Any]] = []
    seen: set[SessionIdentity] = set()
    for host in catalog:
        host_id = str(host["hostId"])
        if host_id.casefold() not in wanted_hosts:
            continue
        record = _host_record(snapshot, host_id)
        raw_rows = record.get("sessions") if isinstance(record, Mapping) else None
        if not isinstance(raw_rows, list):
            continue
        for row in _valid_sessions({"sessions": raw_rows}):
            if _session_host(row).casefold() != host_id.casefold():
                continue
            identity = _session_identity(row)
            if identity is None or identity in seen:
                continue
            _, urgent, active = _row_observation(row, snapshot, None)
            waiting = row.get("activityState") == "waiting"
            if not urgent and (active or waiting):
                seen.add(identity)
                rows.append(row)
    return sorted(rows, key=_session_sort_key)


def _group_rows_for_navigation(
    sessions: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any] | None,
    navigation: NavigationState,
) -> tuple[list[dict[str, Any]], set[SessionIdentity]]:
    """Merge the ordinary page with active rows beyond its flattened cap."""

    ordinary = _sessions_for_navigation(sessions, navigation, snapshot)
    active_rows = _group_sessions(snapshot, navigation)
    merged: list[dict[str, Any]] = []
    indices: dict[SessionIdentity, int] = {}
    for row in ordinary:
        identity = _session_identity(row)
        if identity is None or identity in indices:
            continue
        indices[identity] = len(merged)
        merged.append(dict(row))
    membership: set[SessionIdentity] = set()
    for row in active_rows:
        identity = _session_identity(row)
        if identity is None:
            continue
        membership.add(identity)
        if identity in indices:
            # The host-scoped active record is the fresher presentation for a
            # logical identity that also appears in the flattened page.
            merged[indices[identity]] = dict(row)
        else:
            indices[identity] = len(merged)
            merged.append(dict(row))
    return sorted(merged, key=_session_sort_key), membership


def _sessions_for_navigation(
    sessions: Sequence[Mapping[str, Any]],
    navigation: NavigationState,
    snapshot: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Select leaf rows by logical scope and sort them newest-first."""

    if navigation.view == VIEW_ACTIVE:
        return _active_sessions(snapshot)
    host_id = _scope_host_id(snapshot, navigation)
    if host_id is None:
        rows = sessions
    else:
        host = _host_record(snapshot, host_id)
        candidate = host.get("sessions") if host is not None else None
        rows = (
            (item for item in candidate if _session_host(item).casefold() == host_id.casefold())
            if isinstance(candidate, list)
            else ()
        )
    # Host records are independently persisted; reapply the same leaf-row
    # validation as the flattened snapshot before rendering a scoped list.
    validated = _valid_sessions({"sessions": list(rows)})
    return sorted(validated, key=_session_sort_key)


def _breadcrumb(navigation: NavigationState, snapshot: Mapping[str, Any] | None = None) -> str:
    if navigation.view == VIEW_ACTIVE:
        label = "Active"
    elif navigation.view == VIEW_ALL:
        label = "All"
    elif navigation.view == VIEW_LOCAL:
        label = "Local"
    else:
        label = next(
            (
                str(item["display"])
                for item in _host_catalog(snapshot)
                if str(item["hostId"]).casefold() == (navigation.host_id or "").casefold()
            ),
            navigation.host_id or "Host",
        )
    return "Agents › " + sanitize(label)


def _action_label(action: str) -> str:
    return {
        ACTION_RESUME: "Resume",
        ACTION_CLOSE: "Close",
        ACTION_NEW: "New",
    }[action]


def _action_prompt(navigation: NavigationState, snapshot: Mapping[str, Any] | None) -> str:
    return _breadcrumb(navigation, snapshot)


def _action_hint(action: str) -> str:
    labels = []
    for candidate in ACTION_ORDER:
        label = _action_label(candidate)
        if candidate == action:
            label = f'<span foreground="#42a5f5" weight="bold">[{label}]</span>'
        labels.append(label)
    return f"Enter: {' · '.join(labels)}  │  Tab: Cycle actions  │  Alt+A: Select All active"


def _action_message(action: str, notice: str) -> str:
    hint = _action_hint(action)
    return f"{hint}\u2028\u2028{_pango_escape(notice)}" if notice else hint


def _action_data(action: str) -> str:
    if action not in ACTION_ORDER:
        raise ValueError("unknown Agent Plus action")
    encoded = quote(
        json.dumps(
            {"version": ACTION_DATA_VERSION, "action": action},
            ensure_ascii=True,
            separators=(",", ":"),
        ),
        safe="",
    )
    return ACTION_DATA_PREFIX + encoded


def _parse_action_state(value: object) -> tuple[str, bool]:
    """Decode the named action without falling back after malformed input."""

    if not isinstance(value, str):
        return ACTION_RESUME, True
    components = [
        component[len(ACTION_DATA_PREFIX) :]
        for component in value.split(";")
        if component.startswith(ACTION_DATA_PREFIX)
    ]
    if not components:
        # Older continuation records predate the action cycle.  They carry no
        # choice, so retain the documented primary action.
        return ACTION_RESUME, True
    if len(components) != 1 or not components[0] or len(components[0]) > 4096:
        return ACTION_RESUME, False
    try:
        payload = json.loads(unquote(components[0]))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return ACTION_RESUME, False
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"version", "action"}
        or isinstance(payload.get("version"), bool)
        or payload.get("version") != ACTION_DATA_VERSION
        or not isinstance(payload.get("action"), str)
        or payload["action"] not in ACTION_ORDER
    ):
        return ACTION_RESUME, False
    return payload["action"], True


def _navigation_data(navigation: NavigationState) -> str:
    payload: dict[str, object] = {
        "version": NAVIGATION_DATA_VERSION,
        "view": navigation.view,
    }
    if navigation.view == VIEW_HOST:
        payload["hostId"] = navigation.host_id
    encoded = quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), safe="")
    return NAVIGATION_DATA_PREFIX + encoded


def _parse_navigation_state(value: object) -> NavigationState:
    """Decode P8 state and safely collapse stale P7 state to a flat scope."""

    if not isinstance(value, str):
        return NavigationState()
    for component in value.split(";"):
        if not component.startswith(NAVIGATION_DATA_PREFIX):
            continue
        encoded = component[len(NAVIGATION_DATA_PREFIX) :]
        if not encoded or len(encoded) > 4096:
            return NavigationState()
        try:
            payload = json.loads(unquote(encoded))
        except (UnicodeError, json.JSONDecodeError):
            return NavigationState()
        if not isinstance(payload, Mapping):
            return NavigationState()
        version = payload.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int):
            return NavigationState()
        view = payload.get("view")
        if version == 1:
            # P7's Recent root and Providers root have no P8 equivalent.  A
            # P7 host scope is retained only when its old displayed value is
            # a safe logical id; the catalog resolves it later.
            if view == "hosts" and payload.get("scopeType") == "host":
                old_value = payload.get("scopeValue")
                return NavigationState(VIEW_HOST, old_value if isinstance(old_value, str) else None)
            return NavigationState()
        if version != NAVIGATION_DATA_VERSION or not isinstance(view, str):
            return NavigationState()
        if view == VIEW_HOST:
            host_id = payload.get("hostId")
            return NavigationState(view, host_id if isinstance(host_id, str) else None)
        if view in {VIEW_ACTIVE, VIEW_ALL, VIEW_LOCAL} and "hostId" not in payload:
            return NavigationState(view)
        return NavigationState()
    return NavigationState()


# Keep a descriptive public spelling for focused callers and tests while the
# private spelling makes it clear this parses untrusted Rofi input.
parse_navigation_state = _parse_navigation_state


def selection_payload(session: Mapping[str, Any]) -> str:
    """Encode a row's trusted selection identity for ``ROFI_INFO``."""

    if session.get("contractMode") is not True:
        raise engine.PickerError("Rofi can only open contract-backed sessions")
    payload = {key: session[key] for key in ("kind", "id", "name", "cwd", "host") if key in session}
    payload.update(
        {
            "contractMode": True,
            "hostId": session.get("hostId"),
            "backend": session.get("backend"),
        }
    )
    verified = session.get("providerOptionVerified")
    if verified is not None:
        if not isinstance(verified, bool):
            raise engine.PickerError("Rofi session has an invalid tmux evidence marker")
        # Retained rows from a failed tmux stage are useful for display, but
        # are not current option-backed evidence.  Do not let their marker
        # re-enable the fast path on a later selection.
        payload["providerOptionVerified"] = bool(
            verified and not session.get("tmuxStale") and not session.get("tmuxAmbiguous")
        )
    payload["tmuxAssociationCurrent"] = bool(
        session.get("sourceObservation") == "current"
        and not session.get("tmuxStale")
        and not session.get("tmuxAmbiguous")
    )
    if "tmux" in session:
        payload["tmux"] = session["tmux"]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _row_text(
    session: Mapping[str, Any],
    now: float | None = None,
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> str:
    kind = str(session.get("kind") or "")
    provider = PROVIDER_LABELS.get(kind, kind.title() or "Agent")
    name = sanitize(session.get("name") or session.get("id") or "Agent")
    host = sanitize(session.get("host") or session.get("hostId") or "local")
    cwd = _shorten_cwd(session.get("cwd"))
    age = _age(session.get("recencyAt"), now)
    activity = _activity_label(session, snapshot=snapshot)
    return f"{name}  ·  {provider}  ·  {host}  ·  {cwd}  ·  {age}  ·  {activity}"


def _activity_label(
    session: Mapping[str, Any], *, snapshot: Mapping[str, Any] | None = None
) -> str:
    if (
        session.get("sourceObservation") == "retained"
        or session.get("activityState") == "unknown"
        or _observation_failed(_stage_observation(snapshot, session, "activity"))
        or _refresh_outcome(snapshot) == "failed"
    ):
        return "Activity unknown"
    viewer = session.get("localViewer")
    state = viewer.get("state") if isinstance(viewer, Mapping) else "unknown"
    confidence = viewer.get("confidence") if isinstance(viewer, Mapping) else None
    opened = (
        "Open"
        if state == "open" and confidence == "confirmed"
        else "Open?"
        if state == "open" and confidence == "matched"
        else None
    )
    known_none = state == "none"
    activity = session.get("activityState")
    if activity == "waiting":
        return "Waiting" + (" · " + opened if opened else "" if known_none else " · ?")
    if session.get("active"):
        return opened or ("Active" if known_none else "Active · ?")
    return "Inactive" + (" · " + opened if opened else "" if known_none else " · ?")


def _refresh_outcome(snapshot: Mapping[str, Any] | None) -> str | None:
    """Return a recognized all-host refresh outcome, if one is present."""

    last_refresh = snapshot.get("lastRefresh") if isinstance(snapshot, Mapping) else None
    outcome = last_refresh.get("outcome") if isinstance(last_refresh, Mapping) else None
    return outcome if outcome in {"complete", "partial", "failed"} else None


def _stage_observation(
    snapshot: Mapping[str, Any] | None,
    session: Mapping[str, Any],
    stage: str,
) -> Mapping[str, Any] | None:
    """Return one host/stage observation without trusting malformed metadata."""

    host = _host_record(snapshot, _session_host(session))
    observations = host.get("observations") if isinstance(host, Mapping) else None
    value = observations.get(stage) if isinstance(observations, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _observation_failed(value: Mapping[str, Any] | None) -> bool:
    return isinstance(value, Mapping) and value.get("outcome") == "failed"


def _observation_age(value: Mapping[str, Any] | None, now: float | None) -> str:
    """Format the last successful observation time, preserving unknown history."""

    if not isinstance(value, Mapping):
        return "unknown"
    return _age(value.get("lastSuccessAt"), now)


def _row_observation(
    session: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    now: float | None,
    *,
    refresh_active: bool = False,
) -> tuple[str, bool, bool]:
    """Return ``(status, urgent, active)`` for one presentation row.

    Observation metadata is private cache provenance, not selection or
    lifecycle authority.  Missing metadata deliberately preserves the legacy
    ordinary-row presentation.
    """

    source = session.get("sourceObservation")
    refresh_outcome = _refresh_outcome(snapshot)
    provider = str(session.get("kind") or "")
    provider_observation = _stage_observation(snapshot, session, provider)
    activity_observation = _stage_observation(snapshot, session, "activity")
    global_failed = refresh_outcome == "failed"
    retained = source == "retained" or global_failed
    activity_only = source == "activity-only"
    limited = (
        not retained
        and not activity_only
        and (
            _observation_failed(activity_observation)
            or session.get("tmuxStale") is True
            or session.get("tmuxAmbiguous") is True
        )
    )

    if retained:
        age = _observation_age(provider_observation, now)
        status = (
            f"◌ Rechecking · last seen {age}" if refresh_active else f"◷ Last known · seen {age}"
        )
    elif activity_only:
        status = "Activity seen · details unavailable"
        if refresh_active:
            status = "◌ Checking · " + status
    elif limited:
        status = "Details limited"
        if refresh_active:
            status = "◌ Checking · " + status
    elif refresh_active:
        status = "◌ Checking"
    else:
        status = ""

    # A current activity probe is the only basis for the active Rofi state.
    # A failed activity stage or failed all-host transaction cannot safely
    # carry an old active marker into a new observation cycle.  Legacy rows
    # without observation metadata retain their prior behavior.
    active = bool(session.get("active")) and not (
        _observation_failed(activity_observation) or global_failed
    )
    urgent = retained or activity_only or limited
    return status, urgent, active


def _row_display(
    session: Mapping[str, Any],
    now: float | None = None,
    *,
    snapshot: Mapping[str, Any] | None = None,
    refresh_active: bool = False,
    batch_target: bool = False,
) -> str:
    """Return the two-line Pango presentation for one session row."""

    name = sanitize(session.get("name") or session.get("id") or "Agent")
    host = sanitize(session.get("host") or session.get("hostId") or "local")
    cwd = _shorten_cwd(session.get("cwd"))
    age = _age(session.get("recencyAt"), now)
    activity = _activity_label(session, snapshot=snapshot)
    secondary_parts = [host, cwd, age, activity]
    status, _, _ = _row_observation(
        session,
        snapshot,
        now,
        refresh_active=refresh_active,
    )
    secondary_markup = [_pango_escape(part) for part in secondary_parts]
    if status:
        secondary_markup.append(_pango_escape(status))
    secondary = "  ·  ".join(secondary_markup)
    title = _pango_escape(name)
    if batch_target:
        title = f'<span foreground="#42a5f5">{title}</span>'
    return f'<b>{title}</b>{ROW_SEPARATOR}<span size="smaller" alpha="75%">{secondary}</span>'


def _batch_reference_key(value: Mapping[str, Any]) -> tuple[object, ...] | None:
    """Return the complete immutable tmux identity used by batch targets."""

    required = ("hostId", "meshRevision", "serverGeneration", "sessionId", "createdAt")
    if any(key not in value for key in required):
        return None
    host_id = value.get("hostId")
    revision = value.get("meshRevision")
    server_generation = value.get("serverGeneration")
    session_id = value.get("sessionId")
    created_at = value.get("createdAt")
    if (
        not isinstance(host_id, str)
        or not host_id
        or (revision is not None and not isinstance(revision, str))
        or not isinstance(server_generation, str)
        or not server_generation
        or not isinstance(session_id, str)
        or re.fullmatch(r"\$[0-9]+", session_id, re.ASCII) is None
        or type(created_at) is not int
        or created_at < 0
    ):
        return None
    return tuple(value.get(key) for key in required)


def _target_identity_key(target: Mapping[str, Any]) -> SessionIdentity | None:
    return _session_identity(target)


def _target_matches_session(
    target: Mapping[str, Any],
    session: Mapping[str, Any],
) -> bool:
    """Match a frozen target by logical provider identity and full tmux ref."""

    target_identity = _target_identity_key(target)
    session_identity = _session_identity(session)
    reference = target.get("reference")
    tmux = session.get("tmux")
    backend = session.get("backend")
    if (
        target_identity is None
        or target_identity != session_identity
        or not isinstance(reference, Mapping)
        or not isinstance(tmux, Mapping)
    ):
        return False
    current_reference = dict(tmux)
    current_reference.setdefault("hostId", session.get("hostId"))
    if isinstance(backend, Mapping):
        current_reference.setdefault("meshRevision", backend.get("meshRevision"))
    expected_key = _batch_reference_key(reference)
    current_key = _batch_reference_key(current_reference)
    return expected_key is not None and expected_key == current_key


def _batch_target_badge(
    target: Mapping[str, Any],
    action: str,
    result: Mapping[str, Any] | None = None,
) -> str:
    """Format exact target state without implying a new viewer operation."""

    if result is not None:
        status = str(result.get("status") or "running").replace("_", " ").title()
        reason = sanitize(result.get("reason") or "")
        return status + (f" · {reason}" if reason else "")
    mode = target.get("mode")
    if mode == "already_closed":
        return "Already closed"
    if mode == "already_open":
        return "Already open"
    if action == batch.ACTION_CLOSE:
        viewers = target.get("viewers")
        count = len(viewers) if isinstance(viewers, list) else 0
        return f"Will close {count} window{'s' if count != 1 else ''}"
    return "Will open existing session"


def _frozen_target_card(
    target: Mapping[str, Any], action: str, result: Mapping[str, Any] | None = None
) -> str:
    name = sanitize(target.get("name") or target.get("id") or "Agent")
    provider = PROVIDER_LABELS.get(
        str(target.get("kind") or ""), sanitize(target.get("kind") or "Agent")
    )
    host = sanitize(target.get("host") or target.get("hostId") or "host")
    reference = target.get("reference")
    tmux_id = (
        sanitize(reference.get("sessionId") or "unknown")
        if isinstance(reference, Mapping)
        else "unknown"
    )
    status = _batch_target_badge(target, action, result)
    return f"Frozen target · {name}  ·  {provider}  ·  {host}  ·  tmux {tmux_id}  ·  {status}"


def _provider_icon(kind: str) -> str:
    """Resolve a bundled provider icon, with a bundled generic fallback."""

    candidate = PROVIDER_ICON_PATHS.get(kind)
    if candidate is not None and candidate.is_file():
        return str(candidate)
    if FALLBACK_ICON_PATH.is_file():
        return str(FALLBACK_ICON_PATH)
    return ""


def summarize_errors(errors: Sequence[object]) -> str:
    valid: list[str] = []
    for item in errors:
        if not isinstance(item, Mapping):
            continue
        host = sanitize(item.get("host") or "host")
        stage = sanitize(item.get("stage") or "refresh")
        message = sanitize(item.get("message") or "failed")
        valid.append(f"{host}/{stage}: {message}")
    if not valid:
        return ""
    joined = "Refresh errors: " + "; ".join(valid)
    return joined if len(joined) <= MAX_MESSAGE_LENGTH else joined[: MAX_MESSAGE_LENGTH - 1] + "…"


def _timeout_theme(
    delay: int | float | bool | None = None,
    *,
    enabled: bool | None = None,
) -> str:
    """Build the per-dialog timeout configuration used by script mode."""

    if delay is None:
        delay = bool(enabled)
    if isinstance(delay, bool):
        delay = AUTO_REFRESH_POLL_SECONDS if delay else 0
    normalized_delay = max(0, int(delay))
    return f'configuration {{ timeout {{ delay: {normalized_delay}; action: "kb-custom-19"; }} }}'


def _refresh_data(
    refresh_deadline: float | None = None,
    error_deadline: float | None = None,
    error_message: str = "",
    *,
    deadline: float | None = None,
    check_deadline: float | None = None,
    navigation: NavigationState | None = None,
    action: str = ACTION_RESUME,
    last_used: SessionIdentity | None = None,
    batch_state: BatchUIState | None = None,
) -> str:
    """Encode refresh, notices, and optional navigation state for Rofi."""

    if refresh_deadline is None:
        refresh_deadline = deadline
    values: list[str] = []
    if refresh_deadline is not None:
        values.append(f"{AUTO_REFRESH_DATA_PREFIX}{max(0, int(refresh_deadline))}")
    if error_deadline is not None:
        encoded_message = quote(sanitize(error_message), safe="")
        values.append(
            f"{ERROR_NOTICE_DATA_PREFIX}{max(0, int(error_deadline))}"
            f"{':' + encoded_message if encoded_message else ''}"
        )
    if check_deadline is not None:
        values.append(f"{CHECK_NOTICE_DATA_PREFIX}{max(0, int(check_deadline))}")
    if navigation is not None and not navigation.is_default:
        values.append(_navigation_data(navigation))
    last_used_data = encode_last_used(last_used)
    if last_used_data is not None:
        values.append(last_used_data)
    if not values:
        values.append(AUTO_REFRESH_IDLE_DATA)
    values.append(_action_data(action))
    batch_state_value = _batch_ui_data(batch_state)
    if batch_state_value is not None:
        values.append(batch_state_value)
    return ";".join(values)


def _parse_deadline(value: object, prefix: str) -> float | None:
    if not isinstance(value, str):
        return None
    for component in value.split(";"):
        if not component.startswith(prefix):
            continue
        raw_deadline = component[len(prefix) :]
        try:
            deadline = float(raw_deadline)
        except (TypeError, ValueError, OverflowError):
            return None
        return deadline if math.isfinite(deadline) and deadline > 0 else None
    return None


def _parse_refresh_deadline(value: object) -> float | None:
    return _parse_deadline(value, AUTO_REFRESH_DATA_PREFIX)


def _parse_error_notice(value: object) -> tuple[float | None, str]:
    if not isinstance(value, str):
        return None, ""
    for component in value.split(";"):
        if not component.startswith(ERROR_NOTICE_DATA_PREFIX):
            continue
        payload = component[len(ERROR_NOTICE_DATA_PREFIX) :]
        raw_deadline, separator, encoded_message = payload.partition(":")
        try:
            deadline = float(raw_deadline)
        except (TypeError, ValueError, OverflowError):
            return None, ""
        if not math.isfinite(deadline) or deadline <= 0:
            return None, ""
        if not separator:
            return deadline, ""
        try:
            return deadline, sanitize(unquote(encoded_message))
        except (UnicodeError, ValueError):
            return deadline, ""
    return None, ""


def _parse_check_notice(value: object) -> float | None:
    return _parse_deadline(value, CHECK_NOTICE_DATA_PREFIX)


def _parse_continuation_state(value: object) -> ContinuationState:
    """Parse current and older Rofi continuation components together."""

    refresh_deadline = _parse_refresh_deadline(value)
    error_deadline, error_message = _parse_error_notice(value)
    check_deadline = _parse_check_notice(value)
    action, action_valid = _parse_action_state(value)
    return ContinuationState(
        navigation=_parse_navigation_state(value),
        refresh_deadline=refresh_deadline,
        error_deadline=error_deadline,
        error_message=error_message,
        check_deadline=check_deadline,
        action=action,
        action_valid=action_valid,
        last_used=parse_last_used(value),
        batch_state=_parse_batch_ui_state(value),
    )


# Keep a descriptive public spelling for focused callers and tests.
parse_continuation_state = _parse_continuation_state


def _render_continuation(
    snapshot: Mapping[str, Any] | None,
    state: ContinuationState,
    *,
    navigation: NavigationState | None = None,
    selected_identity: SessionIdentity | None = None,
    reset_selection: bool = False,
    preserve: bool = False,
    preserve_filter: bool = False,
    clear_message: bool = True,
    continuation: bool = True,
    action: str | None = None,
    last_used: SessionIdentity | None = None,
    batch_initial_control: bool = False,
) -> str:
    """Render navigation while retaining live refresh/notice state.

    ``ROFI_DATA`` is the only state that survives a script callback.  A
    navigation transition changes the prompt and rows, but must not silently
    stop a background worker or lose its bounded error notice.  Expired
    components are deliberately omitted and a timeout of zero clears Rofi's
    old timeout/theme when the last continuation has ended.
    """

    active = state.active()
    target = navigation or state.navigation
    action = action or active.action
    if active.error_deadline is not None:
        message = active.error_message
    elif active.refresh_deadline is not None:
        message = "Checking sessions…"
    elif active.check_deadline is not None:
        message = "Checked just now"
    else:
        message = ""
    if active.has_lifecycle:
        timeout: bool | None = True
    elif state.has_lifecycle:
        # The callback received a continuation, but all its deadlines have
        # elapsed.  Explicitly clear the old timeout and carry only the new
        # navigation scope forward.
        timeout = False
    else:
        timeout = None
    return render_snapshot(
        snapshot,
        message=message,
        selected_identity=selected_identity,
        reset_selection=reset_selection,
        preserve=preserve,
        keep_filter=True if preserve_filter else None,
        timeout=timeout,
        refresh_deadline=active.refresh_deadline,
        error_deadline=active.error_deadline,
        check_deadline=active.check_deadline,
        checking=active.refresh_deadline is not None,
        clear_message=clear_message,
        continuation=continuation,
        navigation=target,
        action=action,
        last_used=state.last_used if last_used is None else last_used,
        batch_state=state.batch_state,
        batch_initial_control=batch_initial_control,
    )


def render_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    message: str = "",
    selected: Mapping[str, Any] | None = None,
    selected_identity: SessionIdentity | None = None,
    preserve: bool = False,
    now: float | None = None,
    continuation: bool = False,
    timeout: bool | None = None,
    refresh_deadline: float | None = None,
    error_deadline: float | None = None,
    checking: bool = False,
    check_deadline: float | None = None,
    clear_message: bool = False,
    navigation: NavigationState | None = None,
    keep_filter: bool | None = None,
    keep_selection: bool | None = None,
    reset_selection: bool = False,
    action: str = ACTION_RESUME,
    last_used: SessionIdentity | None = None,
    batch_state: BatchUIState | None = None,
    batch_record: Mapping[str, object] | None = None,
    batch_job: Mapping[str, object] | None = None,
    batch_notice: str = "",
    batch_initial_control: bool = False,
    polling_batch: bool = False,
    initial_open: bool = False,
) -> str:
    """Render a snapshot as Rofi script headers and rows."""

    navigation_was_provided = navigation is not None
    navigation = _canonical_navigation(snapshot, navigation or NavigationState())
    if action not in ACTION_ORDER:
        action = ACTION_RESUME
    sessions = _valid_sessions(snapshot)
    headers = [
        _protocol("prompt", _action_prompt(navigation, snapshot)),
        # Alt+A must reach custom-4 even when native filtering leaves no row.
        # Custom input and deletion are still rejected by their read-only
        # callback branches below.
        _protocol("no-custom", "false"),
        _protocol("use-hot-keys", "true"),
        _protocol("markup-rows", "true"),
    ]
    if keep_filter is None:
        keep_filter = preserve
    if keep_selection is None:
        # Rofi snapshots this header before running the next script callback.
        # Arm it on the ordinary render so the first timeout or manual refresh
        # can apply a stable identity override to the rows it returns.
        keep_selection = True
    if keep_selection:
        # Rofi preserves the current filter and cursor across a script
        # callback when these headers are present.  This is especially useful
        # when a stale selection failed to open.
        headers.append(_protocol("keep-selection", "true"))
    if keep_filter:
        headers.append(_protocol("keep-filter", "true"))
    notice_message = sanitize(message)
    if not notice_message and isinstance(snapshot, Mapping) and not clear_message:
        notice_message = summarize_errors(snapshot.get("errors", []))
    inline_batch = batch_state is not None
    effective_message = _action_message(action, "" if inline_batch else notice_message)
    headers.append(_protocol("message", effective_message))
    watching_viewers = isinstance(snapshot, Mapping) and snapshot.get("_viewerWatch") is True
    if watching_viewers:
        timeout = True
    if timeout is not None:
        if timeout:
            if (
                not polling_batch
                and not watching_viewers
                and refresh_deadline is None
                and error_deadline is None
                and check_deadline is None
            ):
                refresh_deadline = time.time() + AUTO_REFRESH_MAX_SECONDS
            if polling_batch:
                timeout_delay = AUTO_REFRESH_POLL_SECONDS
            elif refresh_deadline is not None:
                timeout_delay = AUTO_REFRESH_POLL_SECONDS
            elif error_deadline is not None:
                timeout_delay = max(1, math.ceil(error_deadline - time.time()))
            elif check_deadline is not None:
                timeout_delay = max(1, math.ceil(check_deadline - time.time()))
            else:
                timeout_delay = AUTO_REFRESH_POLL_SECONDS
            if watching_viewers:
                observed = snapshot.get("_viewerObservedAt")
                viewer_delay = (
                    max(1, math.ceil(observed / 1000 + FRESH_SECONDS - time.time()))
                    if type(observed) is int and snapshot.get("_viewerPending") is not True
                    else AUTO_REFRESH_POLL_SECONDS
                )
                timeout_delay = (
                    min(timeout_delay, viewer_delay)
                    if polling_batch or refresh_deadline or error_deadline or check_deadline
                    else viewer_delay
                )
        else:
            timeout_delay = 0
        headers.append(_protocol("theme", _timeout_theme(timeout_delay)))
        headers.append(
            _protocol(
                "data",
                _refresh_data(
                    refresh_deadline if timeout else None,
                    error_deadline if timeout else None,
                    notice_message if timeout and error_deadline is not None else "",
                    check_deadline=check_deadline if timeout else None,
                    navigation=navigation,
                    action=action,
                    last_used=last_used,
                    batch_state=batch_state,
                ),
            )
        )
    elif navigation_was_provided:
        # Continuation callbacks without a timeout (navigation and opening
        # failures) still need to carry the active scope to the next callback.
        # Explicitly emit ``idle`` for All so stale continuation data cannot
        # leak across a root transition if Rofi retains the previous data.
        headers.append(
            _protocol(
                "data",
                _refresh_data(
                    navigation=navigation,
                    action=action,
                    last_used=last_used,
                    batch_state=batch_state,
                ),
            )
        )

    rendered_rows: list[str] = []
    emitted = 0
    if inline_batch:
        rows, active_membership = _group_rows_for_navigation(sessions, snapshot, navigation)
    else:
        rows = _sessions_for_navigation(sessions, navigation, snapshot)
        active_membership = set()
    selected_indices = [
        index
        for index, session in enumerate(rows)
        if selected_identity is not None and _session_identity(session) == selected_identity
    ]
    # Every picker frame starts with one typed control row. Session identity
    # overrides therefore use the leading-row offset in root and inline views.
    first_conversation = 1 if rows else 0
    if reset_selection:
        headers.append(_protocol("new-selection", first_conversation))
    elif len(selected_indices) == 1 and keep_selection:
        headers.append(_protocol("new-selection", selected_indices[0] + 1))
    elif batch_initial_control:
        headers.append(_protocol("new-selection", 0))
    elif keep_selection and (initial_open or preserve or selected_identity is not None):
        # A missing remembered/current selection falls back to the first real
        # conversation, or to All active when the page is empty.
        headers.append(_protocol("new-selection", first_conversation))
    if inline_batch:
        rendered_rows.append(
            _inline_batch_control_row(
                batch_state,
                batch_record,
                batch_job,
                batch_notice,
            )
        )
        target_membership, unmatched_targets = _inline_target_presentation(
            rows,
            batch_state,
            batch_record,
            batch_job,
        )
    else:
        active_count = len(_group_sessions(snapshot, navigation))
        root_label = f"All active sessions ({active_count})"
        control_options = [
            ("info", json.dumps({"type": "batch"}, separators=(",", ":"))),
            ("meta", "all active sessions"),
            ("display", _pango_escape(root_label)),
        ]
        if checking and (snapshot is None or snapshot.get("generatedAt") == 0):
            # No authority exists yet. Keep the loading frame inert so its
            # sole control cannot become a sticky batch selection on refresh.
            control_options.append(("nonselectable", "true"))
        rendered_rows.append(root_label + _row_options(control_options))
        target_membership, unmatched_targets = set(), []
    for session in rows:
        kind = str(session.get("kind") or "")
        info = selection_payload(session)
        identity = _session_identity(session)
        search_metadata = " ".join(
            (
                *(
                    sanitize(session.get(field) or "")
                    for field in (
                        "name",
                        "kind",
                        "host",
                        "hostId",
                        "windowHost",
                        "connectHost",
                        "cwd",
                        "activityState",
                    )
                ),
                PROVIDER_LABELS[kind],
                PROVIDER_SEARCH_TERMS[kind],
                _activity_label(session, snapshot=snapshot),
            )
        )
        options: list[tuple[str, object]] = [
            ("info", info),
            ("meta", search_metadata),
            ("icon", _provider_icon(kind)),
            (
                "display",
                _row_display(
                    session,
                    now,
                    snapshot=snapshot,
                    refresh_active=checking,
                    batch_target=identity in target_membership,
                ),
            ),
        ]
        _, row_urgent, row_active = _row_observation(
            session,
            snapshot,
            now,
            refresh_active=checking,
        )
        if row_active or (
            identity in active_membership and session.get("activityState") == "waiting"
        ):
            options.append(("active", "true"))
        if row_urgent:
            options.append(("urgent", "true"))
        rendered_rows.append(_row_text(session, now, snapshot=snapshot) + _row_options(options))
        emitted += 1

    for card in unmatched_targets:
        rendered_rows.append(
            _batch_record(
                card,
                row_type="batch-target",
                meta=card,
                display=card,
                nonselectable=True,
            )
        )

    if inline_batch and batch_state.screen == "preview" and batch_record is not None:
        exclusions = batch_record.get("exclusions")
        if isinstance(exclusions, list):
            for item in exclusions:
                if not isinstance(item, Mapping):
                    continue
                name = sanitize(item.get("name") or "Session")
                provider = sanitize(item.get("provider") or "")
                host = sanitize(item.get("host") or "")
                reason = sanitize(item.get("reason") or "excluded")
                card = "Excluded · " + "  ·  ".join(
                    part for part in (name, provider, host, reason) if part
                )
                rendered_rows.append(
                    _batch_record(
                        card,
                        row_type="batch-target",
                        meta=card,
                        display=card,
                        nonselectable=True,
                    )
                )

    if emitted == 0:
        scope_host = _scope_host_id(snapshot, navigation)
        if navigation.view == VIEW_ACTIVE:
            status = "No active sessions observed · Left/Right: change page"
        elif scope_host is not None:
            status = "No agent sessions on " + _breadcrumb(navigation, snapshot).removeprefix(
                "Agents › "
            )
        else:
            status = "No agent sessions found"
        if notice_message:
            if navigation.view == VIEW_ACTIVE:
                status += " · " + notice_message
            else:
                status = "No sessions · " + notice_message
        empty_options: list[tuple[str, object]] = [("nonselectable", "true")]
        refresh_outcome = _refresh_outcome(snapshot)
        empty_urgent = (
            snapshot is None
            or refresh_outcome in {"failed", "partial"}
            or (not checking and bool(notice_message))
        )
        if empty_urgent:
            empty_options.append(("urgent", "true"))
        rendered_rows.append(status + _row_options(empty_options))
        if keep_selection and (initial_open or selected_identity is not None):
            # The only selectable row on an empty page is the batch entry.
            # The status row is explicitly nonselectable.
            if not any(record.startswith("\x00new-selection\x1f") for record in headers):
                headers.append(_protocol("new-selection", 0))

    if continuation:
        return ROFI_RECORD_SEPARATOR.join((*headers, *rendered_rows)) + ROFI_RECORD_SEPARATOR

    # Rofi starts every script-mode process with LF records.  Change its
    # remembered delimiter in the final LF header, then use tabs for rows so a
    # literal newline can create a second visual line inside ``display``.
    headers.append(_protocol("delim", ROFI_DELIMITER_VALUE))
    return (
        "\n".join(headers)
        + "\n"
        + ROFI_RECORD_SEPARATOR.join(rendered_rows)
        + ROFI_RECORD_SEPARATOR
    )


def _parse_selection(raw: str | None) -> dict[str, Any]:
    if not raw:
        raise engine.PickerError("Rofi did not provide a session selection")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise engine.PickerError("Rofi selection metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise engine.PickerError("Rofi selection metadata is not an object")
    kind = payload.get("kind")
    identifier = payload.get("id")
    if kind not in PROVIDER_LABELS or not isinstance(identifier, str):
        raise engine.PickerError("Rofi selection metadata is incomplete")
    if any(char in identifier for char in "\x00\n\r\t"):
        raise engine.PickerError("Rofi selection contains invalid control characters")
    if kind == "codex" or kind == "claude":
        if not engine.UUID_PATTERN.fullmatch(identifier):
            raise engine.PickerError("Rofi selection contains an invalid session id")
    elif not engine.OPENCODE_ID_PATTERN.fullmatch(identifier):
        raise engine.PickerError("Rofi selection contains an invalid session id")
    if payload.get("contractMode") is not True:
        raise engine.PickerError("Rofi contract selection metadata is invalid")
    host_id = payload.get("hostId")
    backend = payload.get("backend")
    revision = backend.get("meshRevision") if isinstance(backend, Mapping) else object()
    if (
        not isinstance(host_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host_id, re.ASCII)
        or not isinstance(backend, Mapping)
        or backend.get("kind") != "contract"
        or backend.get("capability") != "host-mesh-v1+tmux-session-v1"
        or revision is not None
        and (
            not _contract_text(revision)
            or not isinstance(revision, str)
            or revision.strip() != revision
            or any(char.isspace() for char in revision)
        )
    ):
        raise engine.PickerError("Rofi contract selection metadata is incomplete")
    tmux = payload.get("tmux")
    verified = payload.get("providerOptionVerified")
    if "providerOptionVerified" in payload and not isinstance(verified, bool):
        raise engine.PickerError("Rofi contract tmux evidence marker is invalid")
    association_current = payload.get("tmuxAssociationCurrent")
    if "tmuxAssociationCurrent" in payload and not isinstance(association_current, bool):
        raise engine.PickerError("Rofi contract association evidence marker is invalid")
    if tmux is not None:
        if (
            not isinstance(tmux, Mapping)
            or tmux.get("meshRevision") != revision
            or not _contract_text(tmux.get("serverGeneration"))
            or not isinstance(tmux.get("sessionId"), str)
            or not re.fullmatch(r"\$[0-9]+", tmux["sessionId"], re.ASCII)
            or isinstance(tmux.get("createdAt"), bool)
            or not isinstance(tmux.get("createdAt"), int)
            or tmux["createdAt"] < 0
            or tmux.get("observedName") is not None
            and not _contract_text(tmux.get("observedName"))
        ):
            raise engine.PickerError("Rofi contract tmux metadata is invalid")
    return payload


def _parse_row_selection(raw: str | None) -> tuple[str, dict[str, Any]]:
    """Parse a leaf row; group metadata is no longer a valid action target."""

    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and "type" in payload:
            if payload.get("type") == "batch" and set(payload) == {"type"}:
                return "batch", {}
            raise engine.PickerError("Rofi control rows are not session action targets")
    return "session", _parse_selection(raw)


def _cycled_scope(
    snapshot: Mapping[str, Any] | None,
    navigation: NavigationState,
    direction: int,
) -> NavigationState:
    """Wrap through stable Host Mesh scopes without doing discovery."""

    ring = _scope_ring(snapshot)
    current = _canonical_navigation(snapshot, navigation)
    try:
        index = ring.index(current)
    except ValueError:
        index = 0
    return ring[(index + direction) % len(ring)]


def _navigation_from_preference(preference: ViewPreference) -> NavigationState:
    if preference.page_kind == VIEW_HOST:
        return NavigationState(VIEW_HOST, preference.page_host_id)
    if preference.page_kind in {VIEW_ACTIVE, VIEW_ALL, VIEW_LOCAL}:
        return NavigationState(preference.page_kind)
    return NavigationState()


def _preference_for_navigation(
    navigation: NavigationState,
    last_used: SessionIdentity | None,
) -> ViewPreference:
    return ViewPreference(
        page_kind=navigation.view,
        page_host_id=navigation.host_id if navigation.view == VIEW_HOST else None,
        last_used=last_used,
    )


def _save_preference_best_effort(
    preference_store: ViewPreferenceStore,
    navigation: NavigationState,
    last_used: SessionIdentity | None,
) -> None:
    """Persist UI hints without allowing storage failure to affect the action."""

    try:
        preference_store.save(_preference_for_navigation(navigation, last_used))
    except Exception:  # noqa: BLE001 - UI preference writes are strictly best effort
        pass


def _open_selection(
    selection: Mapping[str, Any],
    config: PickerConfig,
    timeout: float | None = None,
    *,
    store: CacheStore | None = None,
    context: PresentationContext | None = None,
) -> None:
    if selection.get("contractMode") is not True or store is None or context is None:
        raise engine.PickerError("contract-backed open requires a prepared authority")
    lifecycle = (
        ContractLifecycle(store, config, context)
        if timeout is None
        else ContractLifecycle(store, config, context, timeout=timeout)
    )
    lifecycle.open_or_create(selection)


def _new_session_selection(
    selection: Mapping[str, Any],
    config: PickerConfig,
    *,
    store: CacheStore | None = None,
    context: PresentationContext | None = None,
) -> None:
    """Run the independent guarded New session here lifecycle."""

    if selection.get("contractMode") is not True or store is None or context is None:
        raise engine.PickerError("contract-backed new session requires a prepared authority")
    ContractLifecycle(store, config, context).new_session_here(selection)


def _try_fast_open(selection: Mapping[str, Any]) -> tuple[bool, Exception | None]:
    """Attempt the guarded open for an option-backed existing session.

    ``True`` means the Tmux action completed and Rofi can close.  A ``None``
    error means the typed Tmux contract explicitly reported a no-action state
    and the caller should continue through the normal full revalidation path.
    Any other error is returned for rendering; retrying it could duplicate a
    terminal focus or launch whose result was ambiguous.
    """

    if selection.get("providerOptionVerified") is not True or not isinstance(
        selection.get("tmux"), Mapping
    ):
        return False, None
    backend = selection.get("backend")
    if not isinstance(backend, Mapping) or backend.get("meshRevision") is None:
        # Omitting --mesh-revision is an unpinned Tmux request, not a
        # local-only assertion.  Keep null-authority rows on the full path so
        # a newly available SSH/Host Mesh contract cannot change authority
        # between render and click.
        return False, None
    try:
        fast_open_selection(selection)
    except LifecycleError as error:
        if error.code in FAST_OPEN_FALLBACK_CODES:
            return False, None
        return False, error
    except Exception as error:  # noqa: BLE001 - fail closed at callback boundary
        return False, error
    return True, None


def _background_command() -> list[str]:
    entrypoint = Path(__file__).resolve().parents[1] / "bin" / "rofi-agent-plus"
    if entrypoint.is_file():
        return [sys.executable, str(entrypoint), "refresh", "--background"]
    return [sys.executable, "-m", "rofi_agent_plus", "refresh", "--background"]


def _presentation_context(
    store: CacheStore,
    config: PickerConfig,
) -> PresentationContext | None:
    candidate = store.presentation_context(config)
    return candidate if isinstance(candidate, PresentationContext) else None


def _presentation_snapshot(
    store: CacheStore,
    config: PickerConfig,
    context: PresentationContext | None = None,
) -> Mapping[str, Any] | None:
    """Read only the capability/revision selected for this invocation.

    Production context always gates by capability and Mesh revision.  An
    explicit invalid/mock context keeps the test seam as a safe
    non-production fallback instead of making class identity a security
    boundary.
    """

    snapshot = (
        store.load_current(config, context)
        if context is not None
        else store.load(config.fingerprint)
    )
    return _decorate_viewers(store, config, snapshot, context)


def _viewer_scope(
    store: CacheStore,
    config: PickerConfig,
    snapshot: Mapping[str, Any] | None,
    context: PresentationContext | None = None,
) -> Mapping[str, object] | None:
    from .viewer_state import make_scope

    if not callable(getattr(store, "viewer_store", None)):
        return None
    if context is not None:
        scope = store.viewer_scope(config, context)
        return scope if isinstance(scope, Mapping) else None
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("backend"), Mapping):
        return None
    local = next((host for host in _host_catalog(snapshot) if host.get("local") is True), None)
    if local is None or snapshot["backend"].get("kind") != "contract":
        return None
    return make_scope(config.fingerprint, snapshot["backend"], local["hostId"])


def _decorate_viewers(
    store: CacheStore,
    config: PickerConfig,
    snapshot: Mapping[str, Any] | None,
    context: PresentationContext | None = None,
) -> Mapping[str, Any] | None:
    if snapshot is None:
        return None
    try:
        scope = _viewer_scope(store, config, snapshot, context)
        return store.viewer_store().decorate(snapshot, scope) if scope is not None else snapshot
    except (engine.PickerError, OSError, ValueError):
        return snapshot


def _request_viewers(
    store: CacheStore, config: PickerConfig, context: PresentationContext | None
) -> None:
    try:
        scope = _viewer_scope(store, config, None, context)
        if scope is None or _background_active(store, _refresh_scope(store, config, context)):
            return
        command = _background_command()[:-2]
        store.viewer_store().request(scope, lambda request: [*command, "_viewer-refresh", request])
    except (engine.PickerError, OSError, ValueError):
        pass


def _refresh_scope(
    store: CacheStore,
    config: PickerConfig,
    context: PresentationContext | None = None,
) -> Mapping[str, object] | None:
    return store.cache_scope(config, context) if context is not None else None


def _background_active(
    store: CacheStore,
    scope: Mapping[str, object] | None,
) -> bool:
    return bool(
        store.background_active(max_age=AUTO_REFRESH_MAX_SECONDS)
        if scope is None
        else store.background_active(max_age=AUTO_REFRESH_MAX_SECONDS, scope=scope)
    )


def _message_for_cache(
    store: CacheStore,
    snapshot: Mapping[str, Any],
    config: PickerConfig,
    *,
    fresh: bool | None = None,
) -> str:
    errors = summarize_errors(snapshot.get("errors", []))
    if fresh is None:
        fresh = store.is_fresh(snapshot, config.refresh_seconds)
    if not fresh:
        prefix = "Checking sessions…"
        return prefix + (" · " + errors if errors else "")
    return errors


def _render_error_notice(
    snapshot: Mapping[str, Any] | None,
    message: str,
    *,
    selected_identity: SessionIdentity | None = None,
    preserve: bool = False,
    continuation: bool = False,
    refresh_deadline: float | None = None,
    error_deadline: float | None = None,
    check_deadline: float | None = None,
    navigation: NavigationState | None = None,
    keep_filter: bool | None = None,
    keep_selection: bool | None = None,
    reset_selection: bool = False,
    action: str = ACTION_RESUME,
    last_used: SessionIdentity | None = None,
    initial_open: bool = False,
    batch_initial_control: bool = False,
) -> str:
    """Render a user-visible error with a bounded, self-clearing timeout."""

    return render_snapshot(
        snapshot,
        message=message,
        selected_identity=selected_identity,
        preserve=preserve,
        timeout=True,
        refresh_deadline=refresh_deadline,
        error_deadline=error_deadline or time.time() + ERROR_NOTICE_SECONDS,
        check_deadline=check_deadline,
        checking=refresh_deadline is not None,
        clear_message=True,
        continuation=continuation,
        navigation=navigation,
        keep_filter=keep_filter,
        keep_selection=keep_selection,
        reset_selection=reset_selection,
        action=action,
        last_used=last_used,
        initial_open=initial_open,
        batch_initial_control=batch_initial_control,
    )


def _start_background_refresh(
    store: CacheStore,
    scope: Mapping[str, object] | None = None,
) -> tuple[bool, float | None]:
    """Claim the detached refresh and return whether polling should be enabled."""

    try:
        started = bool(
            store.spawn_background(_background_command())
            if scope is None
            else store.spawn_background(_background_command(), scope=scope)
        )
    except OSError:
        started = False
    if started:
        return True, time.time() + AUTO_REFRESH_MAX_SECONDS
    # Another picker invocation may already own the marker.  Continue polling
    # that worker, but never start a second one from this path.
    if _background_active(store, scope):
        return True, time.time() + AUTO_REFRESH_MAX_SECONDS
    return False, None


def _auto_refresh_callback(
    environ: Mapping[str, str],
    store: CacheStore,
    config: PickerConfig,
    context: PresentationContext | None,
) -> str:
    """Inspect cache state for the timeout callback without doing discovery."""

    _request_viewers(store, config, context)

    rofi_data = environ.get("ROFI_DATA")
    continuation_state = _parse_continuation_state(rofi_data)
    navigation = continuation_state.navigation
    raw_selection = environ.get("ROFI_INFO")
    selected_identity = _parse_selected_identity(raw_selection)
    if not raw_selection:
        # An inert cold frame has no native selection yet. Restore the saved
        # conversation once its finite provider refresh supplies real rows.
        selected_identity = continuation_state.last_used
    selected_control = _parse_batch_row(raw_selection)
    preserve_batch_control = (
        selected_control is not None and selected_control.get("type") == "batch"
    )
    now = time.time()
    active_state = continuation_state.active(now)
    action = active_state.action if active_state.action_valid else ACTION_RESUME

    def render(*args: object, **kwargs: object) -> str:
        kwargs.setdefault("action", action)
        kwargs.setdefault("last_used", continuation_state.last_used)
        kwargs.setdefault("batch_initial_control", preserve_batch_control)
        return render_snapshot(*args, **kwargs)

    def render_error(*args: object, **kwargs: object) -> str:
        kwargs.setdefault("action", action)
        kwargs.setdefault("last_used", continuation_state.last_used)
        kwargs.setdefault("batch_initial_control", preserve_batch_control)
        return _render_error_notice(*args, **kwargs)

    snapshot = _presentation_snapshot(store, config, context)
    if context is not None and context.error and snapshot is None:
        return render_error(
            None,
            f"Contract refresh failed: {sanitize(context.error)}",
            selected_identity=selected_identity,
            preserve=True,
            continuation=True,
            refresh_deadline=active_state.refresh_deadline,
            check_deadline=active_state.check_deadline,
            navigation=navigation,
        )
    fresh = snapshot is not None and store.is_fresh(snapshot, config.refresh_seconds)

    error_deadline = active_state.error_deadline
    error_message = active_state.error_message

    def render_fresh(candidate: Mapping[str, Any]) -> str:
        """Render a completed cache, including its bounded check notice."""

        errors = summarize_errors(candidate.get("errors", []))
        if errors:
            # A completed background refresh can introduce errors after the
            # original one-second polling deadline was encoded.  Start a new
            # bounded notice for those current errors, then keep that same
            # deadline across subsequent callbacks.  A check notice is never
            # carried alongside a completed snapshot with current errors.
            if (
                continuation_state.error_deadline is not None
                and error_deadline is None
                and continuation_state.error_message == errors
            ):
                return render(
                    candidate,
                    selected_identity=selected_identity,
                    preserve=True,
                    timeout=False,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                )
            notice_deadline = error_deadline
            if notice_deadline is None or error_message != errors:
                notice_deadline = now + ERROR_NOTICE_SECONDS
            if now < notice_deadline:
                return render(
                    candidate,
                    message=errors,
                    selected_identity=selected_identity,
                    preserve=True,
                    timeout=True,
                    error_deadline=notice_deadline,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                )
            return render(
                candidate,
                selected_identity=selected_identity,
                preserve=True,
                timeout=False,
                clear_message=True,
                continuation=True,
                navigation=navigation,
            )

        # Foreground operation failures carry their message in continuation
        # data.  Keep that notice visible until its own deadline even when
        # the cache itself has no refresh errors.
        if error_deadline is not None and error_message:
            return render(
                candidate,
                message=error_message,
                selected_identity=selected_identity,
                preserve=True,
                timeout=True,
                error_deadline=error_deadline,
                clear_message=True,
                continuation=True,
                navigation=navigation,
            )

        # An active completion notice is independent from the worker refresh
        # deadline.  When it expires, explicitly clear Rofi's old timeout,
        # data, and message in one callback.
        if continuation_state.check_deadline is not None:
            if active_state.check_deadline is not None:
                return render(
                    candidate,
                    message="Checked just now",
                    selected_identity=selected_identity,
                    preserve=True,
                    timeout=True,
                    check_deadline=active_state.check_deadline,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                )
            return render(
                candidate,
                selected_identity=selected_identity,
                preserve=True,
                timeout=False,
                clear_message=True,
                continuation=True,
                navigation=navigation,
            )

        # A refresh deadline in ROFI_DATA is the witness that this picker
        # started/continued a refresh.  Fresh ordinary opens have no such
        # component and therefore never display a completion acknowledgement.
        if continuation_state.refresh_deadline is not None:
            return render(
                candidate,
                message="Checked just now",
                selected_identity=selected_identity,
                preserve=True,
                timeout=True,
                check_deadline=now + CHECK_NOTICE_SECONDS,
                clear_message=True,
                continuation=True,
                navigation=navigation,
            )
        return render(
            candidate,
            selected_identity=selected_identity,
            preserve=True,
            timeout=False,
            clear_message=True,
            continuation=True,
            navigation=navigation,
        )

    if fresh:
        return render_fresh(snapshot)

    deadline = continuation_state.refresh_deadline
    timed_out = deadline is not None and now >= deadline
    marker_active = not timed_out and _background_active(
        store,
        _refresh_scope(store, config, context),
    )
    if marker_active:
        if deadline is None:
            deadline = now + AUTO_REFRESH_MAX_SECONDS
        # Errors in a stale snapshot belong to the previous refresh. A current
        # error notice is emitted once the new worker snapshot is fresh.
        background_message = "Checking sessions…"
        notice_active = error_deadline is not None and now < error_deadline and bool(error_message)
        return render(
            snapshot,
            message=error_message if notice_active else background_message,
            selected_identity=selected_identity,
            preserve=True,
            timeout=True,
            refresh_deadline=deadline,
            error_deadline=error_deadline if notice_active else None,
            checking=True,
            continuation=True,
            navigation=navigation,
        )

    # The worker writes the snapshot before removing its marker.  If both
    # operations happen between the first load and the marker check, take one
    # final read so a completed refresh wins over the stale stopped state.
    latest = _presentation_snapshot(store, config, context)
    if latest is not None and store.is_fresh(latest, config.refresh_seconds):
        latest_message = summarize_errors(latest.get("errors", []))
        if latest_message:
            return render_error(
                latest,
                latest_message,
                selected_identity=selected_identity,
                preserve=True,
                continuation=True,
                navigation=navigation,
            )
        return render_fresh(latest)

    if error_deadline is not None and now < error_deadline and error_message:
        return render(
            snapshot,
            message=error_message,
            selected_identity=selected_identity,
            preserve=True,
            timeout=True,
            error_deadline=error_deadline,
            clear_message=True,
            continuation=True,
            navigation=navigation,
        )
    if continuation_state.check_deadline is not None:
        return render(
            snapshot,
            selected_identity=selected_identity,
            preserve=True,
            timeout=False,
            clear_message=True,
            continuation=True,
            navigation=navigation,
        )
    if continuation_state.refresh_deadline is not None:
        outcome = _refresh_outcome(latest or snapshot)
        message = (
            "Check failed · showing last-known results"
            if outcome == "failed"
            else "Check stopped · showing last-known results"
        )
        return render_error(
            latest or snapshot,
            message,
            selected_identity=selected_identity,
            preserve=True,
            continuation=True,
            navigation=navigation,
        )
    return render(
        snapshot,
        selected_identity=selected_identity,
        preserve=True,
        timeout=False,
        clear_message=True,
        continuation=True,
        navigation=navigation,
    )


def _batch_scope(navigation: NavigationState) -> batch.Scope:
    return batch.Scope(navigation.view, navigation.host_id)


_BATCH_ACTION_ORDER = (batch.ACTION_RESUME, batch.ACTION_CLOSE)


def _batch_row_info(
    row_type: str,
    action: str | None = None,
    record_id: str | None = None,
) -> str:
    payload: dict[str, object] = {"type": row_type}
    if action is not None:
        payload["action"] = action
    if record_id is not None:
        payload["recordId"] = record_id
    return json.dumps(payload, separators=(",", ":"))


def _parse_batch_row(raw: str | None) -> Mapping[str, object] | None:
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    row_type = value.get("type")
    if row_type not in _BATCH_ROW_TYPES:
        return None
    if row_type == "batch-confirm":
        return (
            value
            if set(value) == {"type", "action", "recordId"}
            and value.get("action") in _BATCH_ACTION_ORDER
            and isinstance(value.get("recordId"), str)
            and batch._ID.fullmatch(value["recordId"]) is not None
            else None
        )
    if row_type == "batch-target" or row_type == "batch-job":
        return value if set(value) == {"type"} else None
    return value if set(value) == {"type"} else None


def _batch_record(
    text: str,
    *,
    row_type: str,
    meta: str,
    display: str | None = None,
    action: str | None = None,
    record_id: str | None = None,
    nonselectable: bool = False,
) -> str:
    options: list[tuple[str, object]] = [
        ("info", _batch_row_info(row_type, action, record_id)),
        ("meta", meta),
    ]
    if display is not None:
        options.append(("display", _pango_escape(display)))
    if nonselectable:
        options.append(("nonselectable", "true"))
    return _pango_escape(text) + _row_options(options)


def _batch_can_confirm(
    state: BatchUIState,
    record: Mapping[str, Any] | None,
) -> bool:
    return bool(
        record is not None
        and record.get("previewId") == state.record_id
        and record.get("action") == state.action
        and batch.operation_targets(record)
        and not record.get("stopReason")
    )


def _inline_batch_control_row(
    state: BatchUIState,
    record: Mapping[str, object] | None,
    job: Mapping[str, object] | None,
    notice: str,
) -> str:
    if notice.startswith("Batch view failed safely:"):
        subject = "Selected" if state.source_identity is not None else "All active"
        label = f"{subject} · Error"
        return _batch_record(
            label,
            row_type="batch-target",
            meta="batch view error",
            display=label,
            nonselectable=True,
        )
    if state.screen == "preparing":
        label = "All active · Preparing…"
        return _batch_record(
            label,
            row_type="batch-target",
            meta="read-only preview preparation",
            display=label,
        )
    if state.screen == "preview":
        if _batch_can_confirm(state, record):
            count = len(batch.operation_targets(record))
            scope = record.get("scope")
            single_close = (
                state.action == batch.ACTION_CLOSE
                and isinstance(scope, str)
                and scope.startswith("Selected conversation ·")
            )
            subject = "Selected" if single_close else "All active"
            operation = _action_label(state.action or batch.ACTION_RESUME)
            label = f"{subject} · Confirm {operation} ({count})"
            if notice:
                short_notice = sanitize(notice)
                if len(short_notice) > 40:
                    short_notice = short_notice[:39] + "…"
                label += " · " + short_notice
            return _batch_record(
                label,
                row_type="batch-confirm",
                action=state.action,
                record_id=state.record_id,
                meta="confirm fixed batch targets",
                display=label,
            )
        subject = "Selected" if state.source_identity is not None else "All active"
        label = (
            f"{subject} · Preview expired"
            if record is None
            else f"{subject} · Preview failed"
            if record.get("stopReason")
            else f"{subject} · No windows to {'open' if state.action == batch.ACTION_RESUME else 'close'}"
            if record.get("targets")
            else f"{subject} · No targets"
        )
        return _batch_record(
            label,
            row_type="batch-target",
            meta="preview cannot be confirmed",
            display=label,
            nonselectable=True,
        )
    status = sanitize(job.get("status") or "unavailable") if job else "unavailable"
    scope = str(job.get("scope") or "") if job else ""
    subject = "Selected" if scope.startswith("Selected conversation ·") else "All active"
    targets = job.get("targets") if job else None
    results = job.get("results") if job else None
    total = len(targets) if isinstance(targets, list) else 0
    processed = len(results) if isinstance(results, list) else 0
    if status == "running":
        operation = _action_label(str(job.get("action") or batch.ACTION_RESUME))
        progress = f"{operation} {processed}/{total}"
    elif status == "complete":
        progress = f"Done ({total})"
    elif status == "failed":
        progress = f"Failed ({processed}/{total})"
    else:
        progress = status.title()
    label = f"{subject} · {progress}"
    counts = job.get("counts") if job else None
    if isinstance(counts, Mapping):
        for key in ("skipped", "failed"):
            if counts.get(key):
                label += f" · {counts[key]} {key}"
    return _batch_record(
        label,
        row_type="batch-job",
        meta="batch job status",
        display=label,
        nonselectable=True,
    )


def _inline_target_presentation(
    rows: Sequence[Mapping[str, Any]],
    state: BatchUIState,
    record: Mapping[str, Any] | None,
    job: Mapping[str, Any] | None,
) -> tuple[set[SessionIdentity], list[str]]:
    membership: set[SessionIdentity] = set()
    targets: object = None
    action = state.action or batch.ACTION_RESUME
    results: list[object] = []
    if state.screen == "preview" and isinstance(record, Mapping):
        targets = batch.operation_targets(record)
        action = str(record.get("action") or action)
    elif state.screen == "job" and isinstance(job, Mapping):
        targets = job.get("targets")
        action = str(job.get("action") or action)
        raw_results = job.get("results")
        if isinstance(raw_results, list):
            results = raw_results
    target_rows = targets if isinstance(targets, list) else []
    missing: list[str] = []
    for index, target in enumerate(target_rows):
        if not isinstance(target, Mapping):
            continue
        raw_result = results[index] if index < len(results) else None
        result = raw_result if isinstance(raw_result, Mapping) else None
        matched = [row for row in rows if _target_matches_session(target, row)]
        if matched:
            for row in matched:
                identity = _session_identity(row)
                if identity is not None:
                    membership.add(identity)
            continue
        identity = _target_identity_key(target)
        if any(_session_identity(row) == identity for row in rows):
            card = _frozen_target_card(target, action, result)
            card += " · current tmux association differs; no row was marked"
        else:
            card = _frozen_target_card(target, action, result)
            card += " · target is not in the current conversation rows"
        missing.append(card)
    return membership, missing


def _render_batch_inline(
    snapshot: Mapping[str, Any] | None,
    continuation_state: ContinuationState,
    navigation: NavigationState,
    action: str,
    last_used: SessionIdentity | None,
    state: BatchUIState,
    *,
    record: Mapping[str, object] | None = None,
    job: Mapping[str, object] | None = None,
    selected_identity: SessionIdentity | None = None,
    notice: str = "",
    keep_filter: bool = False,
    initial_control: bool = False,
) -> str:
    active = continuation_state.active()
    polling_batch = bool(
        state.screen == "preparing"
        or (
            state.screen == "job"
            and isinstance(job, Mapping)
            and job.get("status") in {"queued", "running"}
        )
    )
    return render_snapshot(
        snapshot,
        message=notice,
        selected_identity=selected_identity,
        continuation=True,
        timeout=True if active.has_lifecycle or polling_batch else False,
        refresh_deadline=active.refresh_deadline,
        error_deadline=active.error_deadline,
        check_deadline=active.check_deadline,
        checking=active.refresh_deadline is not None,
        clear_message=True,
        navigation=navigation,
        keep_filter=keep_filter,
        keep_selection=True,
        action=action,
        last_used=last_used,
        batch_state=state,
        batch_record=record,
        batch_job=job,
        batch_notice=notice,
        batch_initial_control=initial_control,
        polling_batch=polling_batch,
    )


def _root_after_batch(
    store: CacheStore,
    config: PickerConfig,
    navigation: NavigationState,
    continuation_state: ContinuationState,
    action: str,
    last_used: SessionIdentity | None,
    source_identity: SessionIdentity | None,
    *,
    notice: str = "",
    active_control: bool = False,
) -> str:
    snapshot = _presentation_snapshot(store, config)
    selected_identity = None if active_control else source_identity or last_used
    active = continuation_state.active()
    if active.error_deadline is not None:
        message = active.error_message
    elif active.refresh_deadline is not None:
        message = "Checking sessions…"
    elif active.check_deadline is not None:
        message = "Checked just now"
    else:
        message = notice
    timeout: bool | None = (
        True if active.has_lifecycle else False if continuation_state.has_lifecycle else None
    )
    return render_snapshot(
        snapshot,
        message=message,
        selected_identity=selected_identity,
        preserve=False,
        continuation=True,
        timeout=timeout,
        refresh_deadline=active.refresh_deadline,
        error_deadline=active.error_deadline,
        check_deadline=active.check_deadline,
        checking=active.refresh_deadline is not None,
        clear_message=not bool(message),
        navigation=navigation,
        keep_filter=False,
        keep_selection=True,
        action=action,
        last_used=last_used,
        batch_initial_control=active_control,
        initial_open=True,
    )


def _page_after_batch(
    store: CacheStore,
    config: PickerConfig,
    navigation: NavigationState,
    continuation_state: ContinuationState,
    action: str,
    last_used: SessionIdentity | None,
    preference_store: ViewPreferenceStore,
    direction: int,
) -> str:
    """Exit inline batch state through the ordinary cache-only page ring."""

    snapshot = _presentation_snapshot(store, config)
    next_navigation = _cycled_scope(snapshot, navigation, direction)
    _save_preference_best_effort(preference_store, next_navigation, last_used)
    active = continuation_state.active()
    timeout: bool | None = (
        True if active.has_lifecycle else False if continuation_state.has_lifecycle else None
    )
    return render_snapshot(
        snapshot,
        selected_identity=None,
        preserve=True,
        continuation=True,
        timeout=timeout,
        refresh_deadline=active.refresh_deadline,
        error_deadline=active.error_deadline,
        check_deadline=active.check_deadline,
        checking=active.refresh_deadline is not None,
        clear_message=True,
        navigation=next_navigation,
        keep_filter=True,
        keep_selection=True,
        reset_selection=True,
        action=action,
        last_used=last_used,
    )


def _batch_job_state(
    batch_store: batch.BatchStateStore,
    source_identity: SessionIdentity | None,
) -> tuple[BatchUIState, Mapping[str, object]] | None:
    current = batch_store.active_job()
    if current is None:
        return None
    job_id = current.get("jobId")
    if not isinstance(job_id, str) or batch._ID.fullmatch(job_id) is None:
        return None
    return BatchUIState("job", source_identity, None, job_id), current


def _handle_batch_screen(
    environ: Mapping[str, str],
    store: CacheStore,
    config: PickerConfig,
    continuation_state: ContinuationState,
    navigation: NavigationState,
    action: str,
    last_used: SessionIdentity | None,
    batch_store: batch.BatchStateStore,
    preference_store: ViewPreferenceStore,
) -> str:
    state = continuation_state.batch_state
    assert state is not None
    try:
        retv = int(environ.get("ROFI_RETV", "0") or "0")
    except ValueError:
        retv = 0
    raw_info = environ.get("ROFI_INFO")
    selected_row = _parse_batch_row(raw_info) if retv == ROFI_RETV_SELECTED else None
    selected_identity = _parse_selected_identity(raw_info)
    source_identity = state.source_identity
    conversation_identity: SessionIdentity | None = None
    if retv == ROFI_RETV_SELECTED and raw_info:
        try:
            row_kind, selected_session = _parse_row_selection(raw_info)
            if row_kind == "session":
                conversation_identity = _session_identity(selected_session)
        except engine.PickerError:
            pass

    def render_context(
        state_value: BatchUIState = state,
        *,
        snapshot: Mapping[str, Any] | None = None,
        record: Mapping[str, object] | None = None,
        job: Mapping[str, object] | None = None,
        selected: SessionIdentity | None = selected_identity,
        notice: str = "",
        keep_filter: bool = False,
        initial_control: bool = False,
    ) -> str:
        if state_value.screen == "preparing":
            preparation = batch_store.read_preparation(state_value.record_id or "")
            if preparation is None or preparation["status"] == "failed":
                message = (
                    f"Batch preview failed: {sanitize(preparation['error'])}"
                    if preparation is not None
                    else "Preview preparation expired or was cancelled; choose the action again."
                )
                return _root_after_batch(
                    store,
                    config,
                    navigation,
                    continuation_state,
                    action,
                    last_used,
                    source_identity,
                    notice=message,
                    active_control=source_identity is None,
                )
            if preparation["status"] == "ready":
                preview_id = str(preparation["previewId"])
                record = batch_store.read_preview(preview_id)
                if record is None:
                    return _root_after_batch(
                        store,
                        config,
                        navigation,
                        continuation_state,
                        action,
                        last_used,
                        source_identity,
                        notice="Preview expired; choose the action again.",
                        active_control=source_identity is None,
                    )
                state_value = BatchUIState(
                    "preview", state_value.source_identity, state_value.action, preview_id
                )
        if snapshot is None:
            snapshot = _presentation_snapshot(store, config)
        if state_value.screen == "preview" and record is None:
            record = batch_store.read_preview(state_value.record_id or "")
        if state_value.screen == "job" and job is None:
            job = batch_store.current_job(state_value.record_id)
        return _render_batch_inline(
            snapshot,
            continuation_state,
            navigation,
            action,
            last_used,
            state_value,
            record=record,
            job=job,
            selected_identity=selected,
            notice=notice,
            keep_filter=keep_filter,
            initial_control=initial_control,
        )

    def discard_current_preview() -> str:
        if state.screen not in {"preparing", "preview"} or state.record_id is None:
            return ""
        try:
            if state.screen == "preparing":
                batch_store.discard_preparation(state.record_id)
            else:
                batch_store.discard_preview(state.record_id)
        except (batch.BatchError, OSError):
            return "Could not invalidate this preview; it is still available."
        return ""

    if retv in {ROFI_RETV_CUSTOM_2, ROFI_RETV_CUSTOM_3}:
        failure = discard_current_preview()
        if failure:
            return render_context(
                record=batch_store.read_preview(state.record_id or ""),
                notice=failure,
            )
        direction = 1 if retv == ROFI_RETV_CUSTOM_2 else -1
        return _page_after_batch(
            store,
            config,
            navigation,
            continuation_state,
            action,
            last_used,
            preference_store,
            direction,
        )

    if retv == ROFI_RETV_CUSTOM_4:
        if state.screen in {"preparing", "preview"}:
            failure = discard_current_preview()
            if failure:
                return render_context(
                    record=batch_store.read_preview(state.record_id or ""),
                    notice=failure,
                )
            return _root_after_batch(
                store,
                config,
                navigation,
                continuation_state,
                action,
                last_used,
                None,
                active_control=True,
            )
        # Alt+A from a confirmed job hides its status while the finite worker
        # continues; from a preview it also selects the root control row.
        return _root_after_batch(
            store,
            config,
            navigation,
            continuation_state,
            action,
            last_used,
            None,
            active_control=True,
        )

    if retv in {ROFI_RETV_CUSTOM_7, ROFI_RETV_CUSTOM_8}:
        direction = 1 if retv == ROFI_RETV_CUSTOM_7 else -1
        if state.screen in {"preparing", "preview"}:
            current_action = action if action in ACTION_ORDER else ACTION_RESUME
            next_action = ACTION_ORDER[
                (ACTION_ORDER.index(current_action) + direction) % len(ACTION_ORDER)
            ]
            # The one-file private preview is no longer referenced by the
            # dialog. A new Enter must prepare a fresh operation preview.
            failure = discard_current_preview()
            if failure:
                return render_context(
                    record=batch_store.read_preview(state.record_id or ""),
                    notice=failure,
                )
            return _root_after_batch(
                store,
                config,
                navigation,
                continuation_state,
                next_action,
                last_used,
                source_identity,
                active_control=source_identity is None,
            )
        job = batch_store.current_job(state.record_id)
        return render_context(
            job=job,
            keep_filter=True,
            notice="Action is fixed",
        )

    if retv == ROFI_RETV_CUSTOM_19:
        if state.screen == "job":
            active_job = batch_store.active_job()
            job = (
                batch_store.current_job(state.record_id)
                if active_job is None or active_job.get("jobId") != state.record_id
                else active_job
            )
            return render_context(job=job, keep_filter=True)
        if state.screen == "preview":
            record = batch_store.read_preview(state.record_id or "")
            if record is None:
                return _root_after_batch(
                    store,
                    config,
                    navigation,
                    continuation_state,
                    action,
                    last_used,
                    source_identity,
                    notice="Preview expired; choose the action again.",
                    active_control=source_identity is None,
                )
            return render_context(record=record, keep_filter=True)
        return render_context(keep_filter=True)

    if retv == ROFI_RETV_CUSTOM_1:
        return render_context(
            keep_filter=True,
            notice="Preview is fixed",
        )

    if retv in {2, 3}:
        notice = "Custom input is disabled" if retv == 2 else "Deletion is disabled"
        record = (
            batch_store.read_preview(state.record_id or "") if state.screen == "preview" else None
        )
        job = batch_store.current_job(state.record_id) if state.screen == "job" else None
        return render_context(record=record, job=job, keep_filter=True, notice=notice)

    if retv == ROFI_RETV_SELECTED:
        if conversation_identity is not None:
            failure = discard_current_preview()
            if failure:
                return render_context(
                    record=batch_store.read_preview(state.record_id or ""),
                    notice=failure,
                )
            return _root_after_batch(
                store,
                config,
                navigation,
                continuation_state,
                action,
                last_used,
                conversation_identity,
            )
        if state.screen == "preparing":
            # Even if the helper just finished, this Enter only displays the
            # ready preview. A separately selected matching Confirm is required.
            return render_context(keep_filter=True)
        if selected_row is None:
            record = (
                batch_store.read_preview(state.record_id or "")
                if state.screen == "preview"
                else None
            )
            job = batch_store.current_job(state.record_id) if state.screen == "job" else None
            return render_context(
                record=record,
                job=job,
                notice="Unrecognized control row; no batch action was taken.",
            )

        row_type = selected_row.get("type")
        if state.screen == "preview":
            record = batch_store.read_preview(state.record_id or "")
            if row_type == "batch-confirm":
                typed_matches = (
                    selected_row.get("action") == state.action
                    and selected_row.get("recordId") == state.record_id
                )
                if not typed_matches or not _batch_can_confirm(state, record):
                    current_job = _batch_job_state(batch_store, source_identity)
                    if current_job is not None:
                        job_state, job = current_job
                        return render_context(
                            job_state,
                            job=job,
                            selected=None,
                            initial_control=True,
                        )
                    return render_context(
                        record=record,
                        notice="Preview changed",
                    )
                try:
                    job_id, queued = batch_store.consume_and_submit(state.record_id or "")
                    job_state = BatchUIState("job", source_identity, None, job_id)
                    return render_context(
                        job_state,
                        job=queued,
                        selected=None,
                        initial_control=True,
                    )
                except batch.BatchBusy as error:
                    job = batch_store.current_job(error.job_id)
                    job_state = BatchUIState("job", source_identity, None, error.job_id)
                    return render_context(job_state, job=job, selected=None, initial_control=True)
                except Exception as error:  # noqa: BLE001 - one-confirmation boundary
                    return render_context(
                        record=record,
                        notice=f"Batch was not submitted: {sanitize(error)}",
                    )
            if row_type == "batch-target":
                return render_context(
                    record=record,
                    notice="Select Confirm",
                )
            return render_context(record=record, notice="Select Confirm")

        if state.screen == "job":
            job = batch_store.current_job(state.record_id)
            if row_type == "batch-job":
                return render_context(job=job, keep_filter=True)
            return render_context(job=job, notice="Job result rows are display only.")

    record = batch_store.read_preview(state.record_id or "") if state.screen == "preview" else None
    job = batch_store.current_job(state.record_id) if state.screen == "job" else None
    return render_context(record=record, job=job, keep_filter=True)


def run_rofi(
    environ: Mapping[str, str] | None = None,
    *,
    store: CacheStore | None = None,
    config: PickerConfig | None = None,
    preference_store: ViewPreferenceStore | None = None,
    batch_state_store: batch.BatchStateStore | None = None,
) -> int:
    """Process one Rofi script invocation."""

    environ = environ or os.environ
    try:
        retv = int(environ.get("ROFI_RETV", "0") or "0")
    except ValueError:
        retv = 0

    if retv == ROFI_RETV_CUSTOM_6:
        # Escape is native in the managed invocation.  Keep this immediate
        # return as a migration guard for an older binding that still routed
        # it through script mode; no cache/model/config work is safe here.
        return 0
    preference_store = preference_store or ViewPreferenceStore()
    if retv == 0:
        continuation_state = ContinuationState()
        saved_preference = preference_store.load()
        navigation = _navigation_from_preference(saved_preference)
        last_used = saved_preference.last_used
        action = ACTION_RESUME
    else:
        continuation_state = _parse_continuation_state(environ.get("ROFI_DATA"))
        navigation = continuation_state.navigation
        last_used = continuation_state.last_used
        action = continuation_state.action if continuation_state.action_valid else ACTION_RESUME

    raw_info = environ.get("ROFI_INFO")
    parsed_control = _parse_batch_row(raw_info)
    callback_control_selected = (
        retv
        in {
            ROFI_RETV_SELECTED,
            ROFI_RETV_CUSTOM_1,
            ROFI_RETV_CUSTOM_4,
            ROFI_RETV_CUSTOM_7,
            ROFI_RETV_CUSTOM_8,
            ROFI_RETV_CUSTOM_19,
        }
        and parsed_control is not None
        and parsed_control.get("type") == "batch"
    )

    def emit_snapshot(*args: object, **kwargs: object) -> str:
        kwargs.setdefault("last_used", last_used)
        kwargs.setdefault("batch_initial_control", callback_control_selected)
        if retv == 0:
            kwargs["selected_identity"] = last_used
            kwargs.setdefault("initial_open", True)
        return render_snapshot(*args, **kwargs)

    def emit_error(*args: object, **kwargs: object) -> str:
        kwargs.setdefault("last_used", last_used)
        kwargs.setdefault("batch_initial_control", callback_control_selected)
        if retv == 0:
            kwargs["selected_identity"] = last_used
            kwargs.setdefault("initial_open", True)
        return _render_error_notice(*args, **kwargs)

    callback_selection_identity = (
        _parse_selected_identity(environ.get("ROFI_INFO"))
        if retv
        in {
            ROFI_RETV_SELECTED,
            ROFI_RETV_CUSTOM_1,
            ROFI_RETV_CUSTOM_4,
            ROFI_RETV_CUSTOM_7,
            ROFI_RETV_CUSTOM_8,
            ROFI_RETV_CUSTOM_19,
        }
        else None
    )
    store = store or CacheStore()
    try:
        config = config or load_config()
    except Exception as exc:  # noqa: BLE001 - visible Rofi configuration boundary
        if retv == ROFI_RETV_CUSTOM_19:
            now = time.time()
            active = continuation_state.active(now)
            if (
                continuation_state.error_deadline is not None
                and continuation_state.error_deadline <= now
            ):
                # Preserve the old bounded-notice contract: once its deadline
                # has elapsed, clear it rather than starting another notice
                # merely because config loading still fails.  A still-live
                # background worker remains visible and keeps its poll alive.
                rendered = emit_snapshot(
                    None,
                    message=(
                        "Checking sessions…"
                        if active.refresh_deadline
                        else ("Checked just now" if active.check_deadline else "")
                    ),
                    selected_identity=callback_selection_identity,
                    preserve=True,
                    timeout=True if active.refresh_deadline or active.check_deadline else False,
                    refresh_deadline=active.refresh_deadline,
                    check_deadline=active.check_deadline,
                    checking=active.refresh_deadline is not None,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                    action=action,
                )
            else:
                # A config failure is a new operation error.  Do not let an
                # unrelated active refresh (or its old notice text) hide it;
                # carry the refresh deadline alongside a fresh bounded notice.
                rendered = emit_error(
                    None,
                    str(exc)
                    if active.refresh_deadline
                    else (continuation_state.error_message or str(exc)),
                    selected_identity=callback_selection_identity,
                    preserve=True,
                    continuation=True,
                    refresh_deadline=active.refresh_deadline,
                    error_deadline=active.error_deadline,
                    check_deadline=active.check_deadline,
                    navigation=navigation,
                    action=action,
                )
        else:
            rendered = emit_error(
                None,
                str(exc),
                preserve=retv != 0,
                continuation=retv != 0,
                refresh_deadline=continuation_state.active().refresh_deadline,
                check_deadline=continuation_state.active().check_deadline,
                navigation=navigation,
                action=action,
            )
        print(rendered, end="")
        return 0

    batch_state_store = batch_state_store or batch.BatchStateStore()
    if continuation_state.batch_state is not None:
        if retv == ROFI_RETV_CUSTOM_19:
            _request_viewers(store, config, _presentation_context(store, config))
        try:
            rendered = _handle_batch_screen(
                environ,
                store,
                config,
                continuation_state,
                navigation,
                action,
                last_used,
                batch_state_store,
                preference_store,
            )
        except Exception as exc:  # noqa: BLE001 - typed batch UI boundary
            safe_state = continuation_state.batch_state
            rendered = _render_batch_inline(
                _presentation_snapshot(store, config),
                continuation_state,
                navigation,
                action,
                last_used,
                safe_state,
                notice=f"Batch view failed safely: {sanitize(exc)}",
                initial_control=True,
            )
        print(rendered, end="")
        return 0

    if retv == ROFI_RETV_CUSTOM_4:
        try:
            snapshot = _presentation_snapshot(store, config)
            rendered = _render_continuation(
                snapshot,
                continuation_state,
                selected_identity=None,
                action=action,
                last_used=last_used,
                batch_initial_control=True,
            )
        except Exception as exc:  # noqa: BLE001 - cache-only batch entry boundary
            rendered = emit_error(
                None,
                message=f"Cache read failed: {sanitize(exc)}",
                selected_identity=None,
                preserve=True,
                continuation=True,
                navigation=navigation,
                action=action,
                batch_initial_control=True,
            )
        print(rendered, end="")
        return 0

    if retv == ROFI_RETV_SELECTED:
        raw_info = environ.get("ROFI_INFO")
        if raw_info:
            try:
                typed_payload = json.loads(raw_info)
            except (ValueError, json.JSONDecodeError):
                typed_payload = None
            if isinstance(typed_payload, Mapping) and "type" in typed_payload:
                parsed_control = _parse_batch_row(raw_info)
                if typed_payload.get("type") == "batch" and parsed_control is not None:
                    try:
                        snapshot = _presentation_snapshot(store, config)
                    except Exception:
                        snapshot = None
                    if not continuation_state.action_valid:
                        rendered = emit_error(
                            snapshot,
                            "Invalid Agent action state; choose Resume and try again.",
                            selected_identity=None,
                            preserve=True,
                            continuation=True,
                            navigation=navigation,
                            action=ACTION_RESUME,
                            batch_initial_control=True,
                        )
                    elif action == ACTION_NEW:
                        rendered = emit_error(
                            snapshot,
                            "Select a conversation to create a new session.",
                            selected_identity=None,
                            preserve=True,
                            continuation=True,
                            navigation=navigation,
                            keep_filter=True,
                            keep_selection=True,
                            action=action,
                            batch_initial_control=True,
                        )
                    else:
                        operation = (
                            batch.ACTION_CLOSE if action == ACTION_CLOSE else batch.ACTION_RESUME
                        )
                        try:
                            current_job = _batch_job_state(batch_state_store, None)
                            if current_job is not None:
                                state, job = current_job
                                rendered = _render_batch_inline(
                                    snapshot,
                                    continuation_state,
                                    navigation,
                                    action,
                                    last_used,
                                    state,
                                    job=job,
                                    initial_control=True,
                                )
                            else:
                                request_id = batch_state_store.start_preparation(
                                    operation, _batch_scope(navigation)
                                )
                                state = BatchUIState("preparing", None, operation, request_id)
                                rendered = _render_batch_inline(
                                    snapshot,
                                    continuation_state,
                                    navigation,
                                    action,
                                    last_used,
                                    state,
                                    initial_control=True,
                                )
                        except batch.BatchBusy as exc:
                            job = batch_state_store.current_job(exc.job_id)
                            state = BatchUIState("job", None, None, exc.job_id)
                            rendered = _render_batch_inline(
                                snapshot,
                                continuation_state,
                                navigation,
                                action,
                                last_used,
                                state,
                                job=job,
                                initial_control=True,
                            )
                        except Exception as exc:  # noqa: BLE001 - bounded preview boundary
                            rendered = emit_snapshot(
                                snapshot,
                                message=f"Batch preview failed: {sanitize(exc)}",
                                selected_identity=None,
                                preserve=True,
                                keep_filter=True,
                                keep_selection=True,
                                continuation=True,
                                navigation=navigation,
                                action=action,
                                batch_initial_control=True,
                            )
                else:
                    rendered = emit_error(
                        _presentation_snapshot(store, config),
                        "Rofi control rows cannot be opened as sessions.",
                        preserve=True,
                        continuation=True,
                        navigation=navigation,
                        action=action,
                    )
                print(rendered, end="")
                return 0

    if retv in {ROFI_RETV_CUSTOM_7, ROFI_RETV_CUSTOM_8}:
        # Tab only changes the named, per-dialog action.  It must never
        # prepare Host Mesh, refresh a provider, or mutate cache/history.
        try:
            snapshot = _presentation_snapshot(store, config)
            if not continuation_state.action_valid:
                rendered = emit_snapshot(
                    snapshot,
                    message="Invalid Agent action state; choose Resume and try again.",
                    selected_identity=callback_selection_identity,
                    preserve=True,
                    keep_filter=True,
                    keep_selection=True,
                    continuation=True,
                    navigation=navigation,
                    action=ACTION_RESUME,
                )
            else:
                direction = 1 if retv == ROFI_RETV_CUSTOM_7 else -1
                next_action = ACTION_ORDER[
                    (ACTION_ORDER.index(action) + direction) % len(ACTION_ORDER)
                ]
                rendered = _render_continuation(
                    snapshot,
                    continuation_state,
                    selected_identity=callback_selection_identity,
                    preserve=True,
                    preserve_filter=True,
                    action=next_action,
                    last_used=last_used,
                    batch_initial_control=callback_control_selected,
                )
        except Exception as exc:  # noqa: BLE001 - cache-only callback boundary
            rendered = emit_error(
                None,
                f"Action selection failed: {sanitize(exc)}",
                selected_identity=callback_selection_identity,
                preserve=True,
                continuation=True,
                refresh_deadline=continuation_state.active().refresh_deadline,
                check_deadline=continuation_state.active().check_deadline,
                navigation=navigation,
                keep_filter=True,
                keep_selection=True,
                action=action,
            )
        print(rendered, end="")
        return 0

    if retv in {ROFI_RETV_CUSTOM_2, ROFI_RETV_CUSTOM_3}:
        # Left/Right is deliberately cache-only.  Do not prepare Host Mesh,
        # provider clients, or a presentation context just to change the
        # immutable scope ring.  Preserve the filter, but let Rofi reset its
        # cursor for the newly rendered leaf list.
        direction = 1 if retv == ROFI_RETV_CUSTOM_2 else -1
        next_navigation = _cycled_scope(None, navigation, direction)
        preference_saved = False
        try:
            snapshot = _presentation_snapshot(store, config)
            next_navigation = _cycled_scope(snapshot, navigation, direction)
            _save_preference_best_effort(preference_store, next_navigation, last_used)
            preference_saved = True
            rendered = _render_continuation(
                snapshot,
                continuation_state,
                navigation=next_navigation,
                preserve_filter=True,
                reset_selection=True,
                last_used=last_used,
            )
        except Exception as exc:  # noqa: BLE001 - structural callback boundary
            if not preference_saved:
                _save_preference_best_effort(preference_store, next_navigation, last_used)
            rendered = emit_error(
                None,
                f"Navigation failed: {sanitize(exc)}",
                preserve=False,
                continuation=True,
                refresh_deadline=continuation_state.active().refresh_deadline,
                check_deadline=continuation_state.active().check_deadline,
                navigation=next_navigation,
                keep_filter=True,
                keep_selection=True,
                reset_selection=True,
                action=action,
                last_used=last_used,
            )
        print(rendered, end="")
        return 0

    # Parse a selected row before preparing Host Mesh or loading the model so
    # an option-backed existing session can take the bounded Tmux fast path.
    # Rows without current option evidence continue through the normal context
    # setup below.
    preselected: dict[str, Any] | None = None
    preselected_type = "session"
    preselection_error: Exception | None = None
    fast_error: Exception | None = None
    if retv == ROFI_RETV_SELECTED:
        try:
            preselected_type, preselected = _parse_row_selection(environ.get("ROFI_INFO"))
        except Exception as exc:  # noqa: BLE001 - selected callback boundary
            preselection_error = exc
        else:
            if continuation_state.action_valid and action == ACTION_CLOSE:
                selected_identity = _session_identity(preselected)
                try:
                    current_job = _batch_job_state(batch_state_store, selected_identity)
                    if current_job is not None:
                        state, job = current_job
                        snapshot = _presentation_snapshot(store, config)
                        rendered = _render_batch_inline(
                            snapshot,
                            continuation_state,
                            navigation,
                            action,
                            last_used,
                            state,
                            job=job,
                            initial_control=True,
                        )
                    elif preselected_type != "session":
                        raise engine.PickerError(
                            "Close only applies to a selected conversation or All active sessions"
                        )
                    else:
                        context = _presentation_context(store, config)
                        preview = batch.build_single_close_preview(
                            store,
                            config,
                            preselected,
                            context=context,
                        )
                        preview_id = batch_state_store.write_preview(preview)
                        state = BatchUIState(
                            "preview", selected_identity, batch.ACTION_CLOSE, preview_id
                        )
                        persisted = batch_state_store.read_preview(preview_id)
                        if persisted is None:
                            raise batch.BatchError("Close preview could not be saved safely")
                        snapshot = _presentation_snapshot(store, config, context)
                        rendered = _render_batch_inline(
                            snapshot,
                            continuation_state,
                            navigation,
                            action,
                            last_used,
                            state,
                            record=persisted,
                            initial_control=True,
                        )
                except batch.BatchBusy as exc:
                    job = batch_state_store.current_job(exc.job_id)
                    state = BatchUIState("job", selected_identity, None, exc.job_id)
                    rendered = _render_batch_inline(
                        _presentation_snapshot(store, config),
                        continuation_state,
                        navigation,
                        action,
                        last_used,
                        state,
                        job=job,
                        initial_control=True,
                    )
                except Exception as exc:  # noqa: BLE001 - guarded single-close boundary
                    try:
                        snapshot = _presentation_snapshot(store, config)
                    except Exception:
                        snapshot = None
                    rendered = emit_error(
                        snapshot,
                        f"Close preview failed safely: {sanitize(exc)}",
                        selected_identity=selected_identity,
                        preserve=True,
                        continuation=True,
                        navigation=navigation,
                        action=ACTION_CLOSE,
                    )
                print(rendered, end="")
                return 0
            if (
                continuation_state.action_valid
                and action == ACTION_RESUME
                and preselected_type == "session"
            ):
                completed, fast_error = _try_fast_open(preselected)
                if completed:
                    # Tmux Plus has already validated and opened this exact
                    # reference.  No cache reconciliation is needed because
                    # the reference itself did not change.
                    _save_preference_best_effort(
                        preference_store,
                        navigation,
                        _session_identity(preselected) or last_used,
                    )
                    return 0

    if retv == ROFI_RETV_SELECTED and action == ACTION_CLOSE and preselection_error is not None:
        try:
            snapshot = _presentation_snapshot(store, config)
        except Exception:
            snapshot = None
        print(
            emit_error(
                snapshot,
                f"Close preview failed safely: {sanitize(preselection_error)}",
                selected_identity=callback_selection_identity,
                preserve=True,
                continuation=True,
                navigation=navigation,
                action=ACTION_CLOSE,
            ),
            end="",
        )
        return 0

    try:
        context = _presentation_context(store, config)
    except Exception as exc:  # noqa: BLE001 - private model boundary
        print(
            emit_error(
                None,
                f"Model setup failed: {sanitize(exc)}",
                selected_identity=callback_selection_identity,
                preserve=retv != 0,
                continuation=retv != 0,
                refresh_deadline=continuation_state.active().refresh_deadline,
                check_deadline=continuation_state.active().check_deadline,
                navigation=navigation,
                action=action,
            ),
            end="",
        )
        return 0

    # A present but malformed companion pair is never the same thing as an
    # absent capability.  Render its bounded diagnostic for every non-exit
    # callback, including structural navigation and a selected row, before
    # any callback can silently render an empty untyped list.
    if context is not None and context.error:
        try:
            snapshot = _presentation_snapshot(store, config, context)
            if snapshot is None:
                snapshot = store.refresh(config, force=True, context=context)
            print(
                emit_error(
                    snapshot,
                    f"Contract refresh failed: {sanitize(context.error)}",
                    selected_identity=callback_selection_identity,
                    preserve=retv != 0,
                    continuation=retv != 0,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    check_deadline=continuation_state.active().check_deadline,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
        except Exception as exc:  # noqa: BLE001 - defensive callback boundary
            print(
                emit_error(
                    None,
                    f"Contract refresh failed: {sanitize(exc)}",
                    selected_identity=callback_selection_identity,
                    preserve=retv != 0,
                    continuation=retv != 0,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
        return 0

    if retv in {2, 3}:
        # ``no-custom`` normally prevents these callbacks.  If a user has a
        # global Rofi binding that still emits one, keep the list intact and
        # tell Rofi to preserve its current cursor/filter instead of treating
        # it as a mutation request.
        selected = None
        if retv == 3:
            try:
                selected = _parse_selection(environ.get("ROFI_INFO"))
            except engine.PickerError:
                selected = None
        try:
            snapshot = _presentation_snapshot(store, config, context)
        except Exception as exc:  # noqa: BLE001 - private model boundary
            print(
                emit_error(
                    None,
                    f"Model refresh failed: {sanitize(exc)}",
                    preserve=True,
                    continuation=True,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    check_deadline=continuation_state.active().check_deadline,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
            return 0
        notice = "Custom input is disabled" if retv == 2 else "Deletion is disabled"
        print(
            emit_snapshot(
                snapshot,
                message=notice,
                selected=selected,
                preserve=True,
                continuation=True,
                navigation=navigation,
                action=action,
            ),
            end="",
        )
        return 0

    if retv == ROFI_RETV_SELECTED:
        selected = preselected
        row_type = preselected_type
        try:
            if not continuation_state.action_valid:
                raise engine.PickerError("Invalid Agent action state; choose Resume and try again.")
            if preselection_error is not None:
                raise preselection_error
            if selected is None:
                row_type, selected = _parse_row_selection(environ.get("ROFI_INFO"))
            if fast_error is not None:
                raise fast_error
            if action == ACTION_NEW:
                _new_session_selection(selected, config, store=store, context=context)
            else:
                _open_selection(selected, config, store=store, context=context)
            _save_preference_best_effort(
                preference_store,
                navigation,
                _session_identity(selected) or last_used,
            )
            # No rows means Rofi closes after a successful action.
            return 0
        except Exception as exc:  # noqa: BLE001 - selected callback boundary
            try:
                snapshot = _presentation_snapshot(store, config, context)
            except Exception:  # noqa: BLE001 - preserve the original callback error
                snapshot = None
            print(
                emit_error(
                    snapshot,
                    message=(
                        f"Unable to start new session: {sanitize(exc)}"
                        if action == ACTION_NEW
                        else f"Unable to open session: {sanitize(exc)}"
                    ),
                    selected_identity=callback_selection_identity,
                    preserve=True,
                    continuation=True,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    check_deadline=continuation_state.active().check_deadline,
                    navigation=navigation,
                    action=ACTION_RESUME if action == ACTION_NEW else action,
                ),
                end="",
            )
            return 0

    if retv == ROFI_RETV_CUSTOM_19:
        try:
            rendered = _auto_refresh_callback(environ, store, config, context)
        except Exception as exc:  # noqa: BLE001 - timeout callback boundary
            rendered = emit_error(
                None,
                f"Refresh failed: {sanitize(exc)}",
                selected_identity=callback_selection_identity,
                preserve=True,
                continuation=True,
                refresh_deadline=continuation_state.active().refresh_deadline,
                check_deadline=continuation_state.active().check_deadline,
                navigation=navigation,
                action=action,
            )
        print(rendered, end="")
        return 0

    if retv == ROFI_RETV_CUSTOM_1:
        try:
            # Keep the current authoritative rows in place and hand the
            # potentially slow discovery to the existing detached worker.
            # This applies even when the cache is fresh: Alt+R is an explicit
            # refresh request, not permission to block Rofi on network I/O.
            snapshot = _presentation_snapshot(store, config, context)
            polling, deadline = _start_background_refresh(
                store,
                _refresh_scope(store, config, context),
            )
            if not polling:
                print(
                    emit_error(
                        snapshot,
                        "Unable to start background refresh",
                        selected_identity=callback_selection_identity,
                        preserve=True,
                        continuation=True,
                        navigation=navigation,
                        action=action,
                    ),
                    end="",
                )
                return 0
            print(
                emit_snapshot(
                    snapshot,
                    message="Checking sessions…",
                    selected_identity=callback_selection_identity,
                    preserve=True,
                    timeout=True,
                    refresh_deadline=deadline,
                    checking=True,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
        except Exception as exc:  # noqa: BLE001 - bounded refresh callback boundary
            try:
                snapshot = _presentation_snapshot(store, config, context)
            except Exception:  # noqa: BLE001 - preserve the original refresh error
                snapshot = None
            print(
                emit_error(
                    snapshot,
                    message=f"Refresh failed: {sanitize(exc)}",
                    selected_identity=callback_selection_identity,
                    preserve=True,
                    continuation=True,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    check_deadline=continuation_state.active().check_deadline,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
        return 0

    try:
        snapshot = _presentation_snapshot(store, config, context)
    except Exception as exc:  # noqa: BLE001 - initial model boundary
        print(
            emit_error(
                None,
                f"Model refresh failed: {sanitize(exc)}",
                refresh_deadline=continuation_state.active().refresh_deadline,
                check_deadline=continuation_state.active().check_deadline,
                navigation=navigation,
                action=action,
            ),
            end="",
        )
        return 0
    polling = False
    refresh_deadline = None
    if retv == 0 and snapshot is not None and store.is_fresh(snapshot, config.refresh_seconds):
        _request_viewers(store, config, context)
        snapshot = _decorate_viewers(store, config, snapshot, context)
    if snapshot is None:
        scope = _viewer_scope(store, config, None, context)
        if scope is not None:
            # An unknown initial frame is enough while the ordinary finite
            # provider refresh supplies both session and viewer observations.
            mesh = context.selected.mesh
            snapshot = {
                "backend": dict(context.backend),
                "generatedAt": 0,
                "sessions": [],
                "hosts": {},
                "errors": [],
                "hostCatalog": [
                    {"hostId": host.host_id, "display": host.display, "local": host.local}
                    for host in mesh.hosts
                ],
            }
            snapshot = _decorate_viewers(store, config, snapshot, context)
            polling, refresh_deadline = _start_background_refresh(
                store, _refresh_scope(store, config, context)
            )
            print(
                emit_snapshot(
                    snapshot,
                    message="Checking sessions…"
                    if polling
                    else "Unable to start background refresh",
                    timeout=polling,
                    refresh_deadline=refresh_deadline,
                    checking=polling,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
            return 0
        try:
            snapshot = store.refresh(config, context=context) if context else store.refresh(config)
            snapshot = _decorate_viewers(store, config, snapshot, context)
        except Exception as exc:  # noqa: BLE001 - initial refresh boundary
            print(
                emit_error(
                    None,
                    f"Refresh failed: {sanitize(exc)}",
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    check_deadline=continuation_state.active().check_deadline,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
            return 0
    else:
        fresh = store.is_fresh(snapshot, config.refresh_seconds)
        if not fresh:
            polling, refresh_deadline = _start_background_refresh(
                store,
                _refresh_scope(store, config, context),
            )
            if not polling:
                # If another worker finished between the first load and the
                # marker check, show its fresh snapshot instead of stopping
                # with rows that are already obsolete.
                latest = _presentation_snapshot(store, config, context)
                if latest is not None and store.is_fresh(latest, config.refresh_seconds):
                    latest_message = summarize_errors(latest.get("errors", []))
                    print(
                        emit_error(latest, latest_message, navigation=navigation, action=action)
                        if latest_message
                        else emit_snapshot(latest, navigation=navigation, action=action),
                        end="",
                    )
                    return 0
            message = (
                "Checking sessions…" if polling else summarize_errors(snapshot.get("errors", []))
            )
            if not polling and message:
                print(
                    emit_error(snapshot, message, navigation=navigation, action=action),
                    end="",
                )
                return 0
            print(
                emit_snapshot(
                    snapshot,
                    message=message,
                    timeout=True if polling else None,
                    refresh_deadline=refresh_deadline,
                    checking=polling,
                    navigation=navigation,
                    action=action,
                ),
                end="",
            )
            return 0
    message = _message_for_cache(store, snapshot, config)
    if message:
        print(emit_error(snapshot, message, navigation=navigation, action=action), end="")
    else:
        print(emit_snapshot(snapshot, navigation=navigation, action=action), end="")
    return 0
