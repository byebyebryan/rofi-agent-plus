"""Rofi script-mode adapter for Agent Plus."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import sys
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from . import engine
from .cache import CacheStore, PresentationContext
from .config import PickerConfig, load_config
from .contract_lifecycle import ContractLifecycle, LifecycleError, fast_open_selection

ROFI_RETV_SELECTED = 1
ROFI_RETV_CUSTOM_1 = 10
ROFI_RETV_CUSTOM_2 = 11
ROFI_RETV_CUSTOM_3 = 12
# 15 is retained as a migration guard for an older managed invocation that
# still routed Escape through a script callback.  It closes immediately in
# ``run_rofi`` and must never render a replacement list.
ROFI_RETV_CUSTOM_6 = 15
ROFI_RETV_CUSTOM_19 = 28
MAX_MESSAGE_LENGTH = 360
FORCED_REFRESH_TIMEOUT_SECONDS = 30
AUTO_REFRESH_POLL_SECONDS = 1
AUTO_REFRESH_MAX_SECONDS = 30
AUTO_REFRESH_DATA_PREFIX = "background-refresh:"
AUTO_REFRESH_IDLE_DATA = "idle"
ERROR_NOTICE_SECONDS = 3
FAST_OPEN_FALLBACK_CODES = frozenset(
    {"stale_session", "session_not_found", "stale_mesh", "invalid_input"}
)
ERROR_NOTICE_DATA_PREFIX = "error-notice:"
NAVIGATION_DATA_PREFIX = "navigation:"
NAVIGATION_DATA_VERSION = 2
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
_VIEWS = frozenset({VIEW_ALL, VIEW_LOCAL, VIEW_HOST})


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

    ``all`` and ``local`` are typed scopes.  ``host`` carries an authoritative
    Host Mesh id rather than a display label.  Continuation data is untrusted,
    so malformed values normalize to the safe mixed scope.
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

    @property
    def has_lifecycle(self) -> bool:
        return self.refresh_deadline is not None or self.error_deadline is not None

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
        )


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
    """Build the stable All/Local/remote peer ring from Host Mesh order."""

    catalog = _host_catalog(snapshot)
    if not catalog:
        return [NavigationState()]
    local = next((item for item in catalog if item["local"]), None)
    remotes = [item for item in catalog if not item["local"]]
    if not remotes:
        return [NavigationState(VIEW_LOCAL)] if local is not None else [NavigationState()]
    ring = (
        [NavigationState(), NavigationState(VIEW_LOCAL)]
        if local is not None
        else [NavigationState()]
    )
    ring.extend(NavigationState(VIEW_HOST, str(item["hostId"])) for item in remotes)
    return ring


def _sessions_for_navigation(
    sessions: Sequence[Mapping[str, Any]],
    navigation: NavigationState,
    snapshot: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Select leaf rows by logical scope and sort them newest-first."""

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
    if navigation.view == VIEW_ALL:
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
        if view in {VIEW_ALL, VIEW_LOCAL} and "hostId" not in payload:
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
    if "tmux" in session:
        payload["tmux"] = session["tmux"]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _row_text(session: Mapping[str, Any], now: float | None = None) -> str:
    kind = str(session.get("kind") or "")
    provider = PROVIDER_LABELS.get(kind, kind.title() or "Agent")
    name = sanitize(session.get("name") or session.get("id") or "Agent")
    host = sanitize(session.get("host") or session.get("hostId") or "local")
    cwd = _shorten_cwd(session.get("cwd"))
    age = _age(session.get("recencyAt"), now)
    activity = sanitize(
        session.get("activityState") or ("active" if session.get("active") else "idle")
    )
    return f"{name}  ·  {provider}  ·  {host}  ·  {cwd}  ·  {age}  ·  {activity}"


def _row_display(session: Mapping[str, Any], now: float | None = None) -> str:
    """Return the two-line Pango presentation for one session row."""

    name = sanitize(session.get("name") or session.get("id") or "Agent")
    host = sanitize(session.get("host") or session.get("hostId") or "local")
    cwd = _shorten_cwd(session.get("cwd"))
    age = _age(session.get("recencyAt"), now)
    activity = sanitize(
        session.get("activityState") or ("active" if session.get("active") else "idle")
    )
    secondary = "  ·  ".join((host, cwd, age, activity))
    return (
        f"<b>{_pango_escape(name)}</b>"
        f'{ROW_SEPARATOR}<span size="smaller" alpha="75%">'
        f"{_pango_escape(secondary)}</span>"
    )


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
    navigation: NavigationState | None = None,
) -> str:
    """Encode refresh, notice, and optional navigation state for Rofi."""

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
    if navigation is not None and not navigation.is_default:
        values.append(_navigation_data(navigation))
    if not values:
        return AUTO_REFRESH_IDLE_DATA
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


def _parse_continuation_state(value: object) -> ContinuationState:
    """Parse current and older Rofi continuation components together."""

    refresh_deadline = _parse_refresh_deadline(value)
    error_deadline, error_message = _parse_error_notice(value)
    return ContinuationState(
        navigation=_parse_navigation_state(value),
        refresh_deadline=refresh_deadline,
        error_deadline=error_deadline,
        error_message=error_message,
    )


# Keep a descriptive public spelling for focused callers and tests.
parse_continuation_state = _parse_continuation_state


def _render_continuation(
    snapshot: Mapping[str, Any] | None,
    state: ContinuationState,
    *,
    navigation: NavigationState | None = None,
    preserve: bool = False,
    preserve_filter: bool = False,
    clear_message: bool = True,
    continuation: bool = True,
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
    if active.error_deadline is not None:
        message = active.error_message
    elif active.refresh_deadline is not None:
        message = "Refreshing in background"
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
        preserve=preserve,
        keep_filter=True if preserve_filter else None,
        timeout=timeout,
        refresh_deadline=active.refresh_deadline,
        error_deadline=active.error_deadline,
        clear_message=clear_message,
        continuation=continuation,
        navigation=target,
    )


def render_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    message: str = "",
    selected: Mapping[str, Any] | None = None,
    preserve: bool = False,
    now: float | None = None,
    continuation: bool = False,
    timeout: bool | None = None,
    refresh_deadline: float | None = None,
    error_deadline: float | None = None,
    clear_message: bool = False,
    navigation: NavigationState | None = None,
    keep_filter: bool | None = None,
    keep_selection: bool | None = None,
) -> str:
    """Render a snapshot as Rofi script headers and rows."""

    navigation_was_provided = navigation is not None
    navigation = _canonical_navigation(snapshot, navigation or NavigationState())
    sessions = _valid_sessions(snapshot)
    headers = [
        _protocol("prompt", _breadcrumb(navigation, snapshot)),
        _protocol("no-custom", "true"),
        _protocol("use-hot-keys", "true"),
        _protocol("markup-rows", "true"),
    ]
    if keep_filter is None:
        keep_filter = preserve
    if keep_selection is None:
        keep_selection = preserve or selected is not None
    if keep_selection:
        # Rofi preserves the current filter and cursor across a script
        # callback when these headers are present.  This is especially useful
        # when a stale selection failed to open.
        headers.append(_protocol("keep-selection", "true"))
    if keep_filter:
        headers.append(_protocol("keep-filter", "true"))
    effective_message = sanitize(message)
    if not effective_message and isinstance(snapshot, Mapping) and not clear_message:
        effective_message = summarize_errors(snapshot.get("errors", []))
    if effective_message or clear_message:
        headers.append(_protocol("message", effective_message))
    if timeout is not None:
        if timeout:
            if refresh_deadline is None and error_deadline is None:
                refresh_deadline = time.time() + AUTO_REFRESH_MAX_SECONDS
            if refresh_deadline is not None:
                timeout_delay = AUTO_REFRESH_POLL_SECONDS
            elif error_deadline is not None:
                timeout_delay = max(1, math.ceil(error_deadline - time.time()))
            else:
                timeout_delay = AUTO_REFRESH_POLL_SECONDS
        else:
            timeout_delay = 0
        headers.append(_protocol("theme", _timeout_theme(timeout_delay)))
        headers.append(
            _protocol(
                "data",
                _refresh_data(
                    refresh_deadline if timeout else None,
                    error_deadline if timeout else None,
                    effective_message if timeout and error_deadline is not None else "",
                    navigation=navigation,
                ),
            )
        )
    elif navigation_was_provided:
        # Continuation callbacks without a timeout (navigation and opening
        # failures) still need to carry the active scope to the next callback.
        # Explicitly emit ``idle`` for All so stale continuation data cannot
        # leak across a root transition if Rofi retains the previous data.
        headers.append(_protocol("data", _refresh_data(navigation=navigation)))

    rendered_rows: list[str] = []
    emitted = 0
    rows = _sessions_for_navigation(sessions, navigation, snapshot)
    for session in rows:
        kind = str(session.get("kind") or "")
        info = selection_payload(session)
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
            )
        )
        options: list[tuple[str, object]] = [
            ("info", info),
            ("meta", search_metadata),
            ("icon", _provider_icon(kind)),
            ("display", _row_display(session, now)),
        ]
        if session.get("active"):
            options.append(("active", "true"))
        rendered_rows.append(_row_text(session, now) + _row_options(options))
        emitted += 1

    if emitted == 0:
        scope_host = _scope_host_id(snapshot, navigation)
        if scope_host is not None:
            status = "No agent sessions on " + _breadcrumb(navigation, snapshot).removeprefix(
                "Agents › "
            )
        else:
            status = "No agent sessions found"
        if effective_message:
            status = "No sessions · " + effective_message
        rendered_rows.append(status + _row_options([("nonselectable", "true"), ("urgent", "true")]))

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
        if isinstance(payload, dict) and payload.get("type") == "group":
            raise engine.PickerError("Rofi group rows are no longer actionable")
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

    if context is not None:
        return store.load_current(config, context)
    return store.load(config.fingerprint)


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
        prefix = "Refreshing in background"
        return prefix + (" · " + errors if errors else "")
    return errors


def _render_error_notice(
    snapshot: Mapping[str, Any] | None,
    message: str,
    *,
    preserve: bool = False,
    continuation: bool = False,
    refresh_deadline: float | None = None,
    error_deadline: float | None = None,
    navigation: NavigationState | None = None,
    keep_filter: bool | None = None,
    keep_selection: bool | None = None,
) -> str:
    """Render a user-visible error with a bounded, self-clearing timeout."""

    return render_snapshot(
        snapshot,
        message=message,
        preserve=preserve,
        timeout=True,
        refresh_deadline=refresh_deadline,
        error_deadline=error_deadline or time.time() + ERROR_NOTICE_SECONDS,
        clear_message=True,
        continuation=continuation,
        navigation=navigation,
        keep_filter=keep_filter,
        keep_selection=keep_selection,
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
) -> str:
    """Inspect cache state for the timeout callback without doing discovery."""

    context = _presentation_context(store, config)
    snapshot = _presentation_snapshot(store, config, context)
    if context is not None and context.error and snapshot is None:
        return _render_error_notice(
            None,
            f"Contract refresh failed: {sanitize(context.error)}",
            preserve=True,
            continuation=True,
            navigation=_parse_continuation_state(environ.get("ROFI_DATA")).navigation,
        )
    fresh = snapshot is not None and store.is_fresh(snapshot, config.refresh_seconds)
    rofi_data = environ.get("ROFI_DATA")
    continuation_state = _parse_continuation_state(rofi_data)
    navigation = continuation_state.navigation
    error_deadline = continuation_state.error_deadline
    error_message = continuation_state.error_message
    now = time.time()
    if fresh:
        errors = summarize_errors(snapshot.get("errors", []))
        if errors:
            # A completed background refresh can introduce errors after the
            # original one-second polling deadline was encoded.  Start a new
            # bounded notice for those current errors, then keep that same
            # deadline across subsequent callbacks.
            if error_deadline is not None and now >= error_deadline and error_message == errors:
                return render_snapshot(
                    snapshot,
                    preserve=True,
                    timeout=False,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                )
            if error_deadline is None or error_message != errors:
                error_deadline = now + ERROR_NOTICE_SECONDS
            if now < error_deadline:
                return render_snapshot(
                    snapshot,
                    message=errors,
                    preserve=True,
                    timeout=True,
                    error_deadline=error_deadline,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                )
            return render_snapshot(
                snapshot,
                preserve=True,
                timeout=False,
                clear_message=True,
                continuation=True,
                navigation=navigation,
            )

        # Foreground operation failures carry their message in continuation
        # data.  Keep that notice visible until its own deadline even when
        # the cache itself has no refresh errors.
        if error_deadline is not None and now < error_deadline and error_message:
            return render_snapshot(
                snapshot,
                message=error_message,
                preserve=True,
                timeout=True,
                error_deadline=error_deadline,
                clear_message=True,
                continuation=True,
                navigation=navigation,
            )
        return render_snapshot(
            snapshot,
            preserve=True,
            timeout=False,
            clear_message=True,
            continuation=True,
            navigation=navigation,
        )

    deadline = continuation_state.refresh_deadline
    timed_out = deadline is not None and now >= deadline
    marker_active = not timed_out and _background_active(
        store,
        _refresh_scope(store, config, context),
    )
    if marker_active:
        if deadline is None:
            deadline = now + AUTO_REFRESH_MAX_SECONDS
        if snapshot is None:
            background_message = "Refreshing in background"
        else:
            # Errors in a stale snapshot belong to the previous refresh.  A
            # current error notice is emitted once the new worker snapshot is
            # fresh, so keep the polling status unambiguous here.
            background_message = "Refreshing in background"
        notice_active = error_deadline is not None and now < error_deadline and bool(error_message)
        return render_snapshot(
            snapshot,
            message=error_message if notice_active else background_message,
            preserve=True,
            timeout=True,
            refresh_deadline=deadline,
            error_deadline=error_deadline if notice_active else None,
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
            return _render_error_notice(
                latest,
                latest_message,
                preserve=True,
                continuation=True,
                navigation=navigation,
            )
        return render_snapshot(
            latest,
            preserve=True,
            timeout=False,
            clear_message=True,
            continuation=True,
            navigation=navigation,
        )

    if error_deadline is not None and now < error_deadline and error_message:
        return render_snapshot(
            snapshot,
            message=error_message,
            preserve=True,
            timeout=True,
            error_deadline=error_deadline,
            clear_message=True,
            continuation=True,
            navigation=navigation,
        )
    return render_snapshot(
        snapshot,
        preserve=True,
        timeout=False,
        clear_message=True,
        continuation=True,
        navigation=navigation,
    )


def _forced_refresh(
    store: CacheStore,
    config: PickerConfig,
    context: PresentationContext | None = None,
) -> dict[str, Any]:
    """Refresh with a hard foreground bound for the Alt+R callback."""

    if not hasattr(signal, "SIGALRM"):
        return (
            store.refresh(config, force=True, context=context)
            if context
            else store.refresh(config, force=True)
        )

    def timeout_handler(_signum: int, _frame: object) -> None:
        raise engine.PickerError("refresh timed out")

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, FORCED_REFRESH_TIMEOUT_SECONDS)
    try:
        return (
            store.refresh(config, force=True, context=context)
            if context
            else store.refresh(config, force=True)
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def run_rofi(
    environ: Mapping[str, str] | None = None,
    *,
    store: CacheStore | None = None,
    config: PickerConfig | None = None,
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
    continuation_state = _parse_continuation_state(environ.get("ROFI_DATA"))
    navigation = continuation_state.navigation
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
                rendered = render_snapshot(
                    None,
                    message="Refreshing in background" if active.refresh_deadline else "",
                    preserve=True,
                    timeout=True if active.refresh_deadline else False,
                    refresh_deadline=active.refresh_deadline,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                )
            else:
                # A config failure is a new operation error.  Do not let an
                # unrelated active refresh (or its old notice text) hide it;
                # carry the refresh deadline alongside a fresh bounded notice.
                rendered = _render_error_notice(
                    None,
                    str(exc)
                    if active.refresh_deadline
                    else (continuation_state.error_message or str(exc)),
                    preserve=True,
                    continuation=True,
                    refresh_deadline=active.refresh_deadline,
                    error_deadline=active.error_deadline,
                    navigation=navigation,
                )
        else:
            rendered = _render_error_notice(
                None,
                str(exc),
                preserve=retv != 0,
                continuation=retv != 0,
                refresh_deadline=continuation_state.active().refresh_deadline,
                navigation=navigation,
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
        try:
            snapshot = _presentation_snapshot(store, config)
            next_navigation = _cycled_scope(snapshot, navigation, direction)
            rendered = _render_continuation(
                snapshot,
                continuation_state,
                navigation=next_navigation,
                preserve_filter=True,
            )
        except Exception as exc:  # noqa: BLE001 - structural callback boundary
            rendered = _render_error_notice(
                None,
                f"Navigation failed: {sanitize(exc)}",
                preserve=False,
                continuation=True,
                refresh_deadline=continuation_state.active().refresh_deadline,
                navigation=next_navigation,
                keep_filter=True,
                keep_selection=False,
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
            if preselected_type == "session":
                completed, fast_error = _try_fast_open(preselected)
                if completed:
                    # Tmux Plus has already validated and opened this exact
                    # reference.  No cache reconciliation is needed because
                    # the reference itself did not change.
                    return 0

    try:
        context = _presentation_context(store, config)
    except Exception as exc:  # noqa: BLE001 - private model boundary
        print(
            _render_error_notice(
                None,
                f"Model setup failed: {sanitize(exc)}",
                preserve=retv != 0,
                continuation=retv != 0,
                refresh_deadline=continuation_state.active().refresh_deadline,
                navigation=navigation,
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
                _render_error_notice(
                    snapshot,
                    f"Contract refresh failed: {sanitize(context.error)}",
                    preserve=retv != 0,
                    continuation=retv != 0,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    navigation=navigation,
                ),
                end="",
            )
        except Exception as exc:  # noqa: BLE001 - defensive callback boundary
            print(
                _render_error_notice(
                    None,
                    f"Contract refresh failed: {sanitize(exc)}",
                    preserve=retv != 0,
                    continuation=retv != 0,
                    navigation=navigation,
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
                _render_error_notice(
                    None,
                    f"Model refresh failed: {sanitize(exc)}",
                    preserve=True,
                    continuation=True,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    navigation=navigation,
                ),
                end="",
            )
            return 0
        notice = "Custom input is disabled" if retv == 2 else "Deletion is disabled"
        print(
            render_snapshot(
                snapshot,
                message=notice,
                selected=selected,
                preserve=True,
                continuation=True,
                navigation=navigation,
            ),
            end="",
        )
        return 0

    if retv == ROFI_RETV_SELECTED:
        selected = preselected
        row_type = preselected_type
        try:
            if preselection_error is not None:
                raise preselection_error
            if selected is None:
                row_type, selected = _parse_row_selection(environ.get("ROFI_INFO"))
            if fast_error is not None:
                raise fast_error
            _open_selection(selected, config, store=store, context=context)
            # No rows means Rofi closes after a successful action.
            return 0
        except Exception as exc:  # noqa: BLE001 - selected callback boundary
            try:
                snapshot = _presentation_snapshot(store, config, context)
            except Exception:  # noqa: BLE001 - preserve the original callback error
                snapshot = None
            print(
                _render_error_notice(
                    snapshot,
                    message=f"Unable to open session: {sanitize(exc)}",
                    preserve=True,
                    continuation=True,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    navigation=navigation,
                ),
                end="",
            )
            return 0

    if retv == ROFI_RETV_CUSTOM_19:
        try:
            rendered = _auto_refresh_callback(environ, store, config)
        except Exception as exc:  # noqa: BLE001 - timeout callback boundary
            rendered = _render_error_notice(
                None,
                f"Refresh failed: {sanitize(exc)}",
                preserve=True,
                continuation=True,
                refresh_deadline=continuation_state.active().refresh_deadline,
                navigation=navigation,
            )
        print(rendered, end="")
        return 0

    if retv == ROFI_RETV_CUSTOM_1:
        try:
            snapshot = _forced_refresh(store, config, context)
            fresh = store.is_fresh(snapshot, config.refresh_seconds)
            polling = False
            deadline = None
            if not fresh and _background_active(store, _refresh_scope(store, config, context)):
                polling = True
                deadline = _parse_refresh_deadline(environ.get("ROFI_DATA"))
                if deadline is None:
                    deadline = time.time() + AUTO_REFRESH_MAX_SECONDS
            message = _message_for_cache(store, snapshot, config, fresh=fresh)
            if fresh and message:
                print(
                    _render_error_notice(
                        snapshot,
                        message,
                        preserve=True,
                        continuation=True,
                        refresh_deadline=continuation_state.active().refresh_deadline,
                        navigation=navigation,
                    ),
                    end="",
                )
                return 0
            if not fresh and not polling:
                message = summarize_errors(snapshot.get("errors", []))
            elif not fresh:
                # Errors in this snapshot belong to the previous refresh;
                # current provider errors are reported when the new snapshot
                # completes and receive their own bounded notice.
                message = "Refreshing in background"
            if not fresh and not polling and message:
                print(
                    _render_error_notice(
                        snapshot,
                        message,
                        preserve=True,
                        continuation=True,
                        refresh_deadline=continuation_state.active().refresh_deadline,
                        navigation=navigation,
                    ),
                    end="",
                )
                return 0
            print(
                render_snapshot(
                    snapshot,
                    message=message,
                    preserve=True,
                    timeout=polling,
                    refresh_deadline=deadline,
                    clear_message=True,
                    continuation=True,
                    navigation=navigation,
                ),
                end="",
            )
        except Exception as exc:  # noqa: BLE001 - bounded refresh callback boundary
            try:
                snapshot = _presentation_snapshot(store, config, context)
            except Exception:  # noqa: BLE001 - preserve the original refresh error
                snapshot = None
            print(
                _render_error_notice(
                    snapshot,
                    message=f"Refresh failed: {sanitize(exc)}",
                    preserve=True,
                    continuation=True,
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    navigation=navigation,
                ),
                end="",
            )
        return 0

    try:
        snapshot = _presentation_snapshot(store, config, context)
    except Exception as exc:  # noqa: BLE001 - initial model boundary
        print(
            _render_error_notice(
                None,
                f"Model refresh failed: {sanitize(exc)}",
                refresh_deadline=continuation_state.active().refresh_deadline,
                navigation=navigation,
            ),
            end="",
        )
        return 0
    polling = False
    refresh_deadline = None
    if snapshot is None:
        try:
            snapshot = store.refresh(config, context=context) if context else store.refresh(config)
        except Exception as exc:  # noqa: BLE001 - initial refresh boundary
            print(
                _render_error_notice(
                    None,
                    f"Refresh failed: {sanitize(exc)}",
                    refresh_deadline=continuation_state.active().refresh_deadline,
                    navigation=navigation,
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
                        _render_error_notice(latest, latest_message, navigation=navigation)
                        if latest_message
                        else render_snapshot(latest, navigation=navigation),
                        end="",
                    )
                    return 0
            message = (
                "Refreshing in background"
                if polling
                else summarize_errors(snapshot.get("errors", []))
            )
            if not polling and message:
                print(_render_error_notice(snapshot, message, navigation=navigation), end="")
                return 0
            print(
                render_snapshot(
                    snapshot,
                    message=message,
                    timeout=True if polling else None,
                    refresh_deadline=refresh_deadline,
                    navigation=navigation,
                ),
                end="",
            )
            return 0
    message = _message_for_cache(store, snapshot, config)
    if message:
        print(_render_error_notice(snapshot, message, navigation=navigation), end="")
    else:
        print(render_snapshot(snapshot, navigation=navigation), end="")
    return 0
