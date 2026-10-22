"""Flat, typed Rofi presentation for direct host targets and local windows."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .actions import ActionError, Actions
from .cache import CacheError, CacheStore, CatalogSnapshot, HostCatalog
from .config import ConfigError, DesktopConfig, DirectConfig, load_direct_config, owning_user_home
from .desktop import Desktop, DesktopError, Window
from .model import (
    EntryReference,
    RecordError,
    Target,
    UserScope,
    parse_entry_reference,
    parse_target,
)
from .registry import Instance, Registry, RegistryError, target_storage_key
from .startup_refresh import POLL_SECONDS, RefreshStatus, read_status
from .transport import Transport, TransportError
from .view_preferences import ViewPreference, ViewPreferenceStore
from .window_titles import freeze_titles

ROFI_RETV_SELECTED = 1
ROFI_RETV_CUSTOM_1 = 10
ROFI_RETV_CUSTOM_7 = 16
ROFI_RETV_CUSTOM_8 = 17
ROFI_RETV_CUSTOM_19 = 28
MAX_DATA_BYTES = 16 * 1024
MAX_MESSAGE_LENGTH = 720
RECORD_SEPARATOR = "\x1e"
ACTION_OPEN = "open"
ACTION_ADDITIONAL = "additional"
_HEX_64 = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_HEX_32 = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_ACTIONS = {ACTION_OPEN, ACTION_ADDITIONAL}
_PROVIDER_NAMES = {"codex": "Codex", "claude": "Claude"}
_ACTIVE_STATES = {"reserved", "dispatching", "opening", "live", "uncertain"}
_STALE_FOCUS_CODES = {
    "instance_missing",
    "instance_not_live",
    "selected_window_disappeared",
    "desktop_generation_changed",
    "terminal_generation_changed",
}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("picker record contains a duplicate JSON field")
        result[key] = value
    return result


def clean_text(value: object, *, limit: int = 4096) -> str:
    """Bound display/search text and remove controls before markup escaping."""

    raw = str(value) if value is not None else ""
    clean = "".join(" " if unicodedata.category(char).startswith("C") else char for char in raw)
    return " ".join(clean.split())[:limit]


def _protocol(name: str, value: object) -> str:
    return f"\x00{name}\x1f{value}"


@dataclass(frozen=True)
class PickerRow:
    kind: str
    key: str | None
    display: str
    meta: str
    target_id: str | None = None
    instance_id: str | None = None
    target: Target | None = None
    reference: EntryReference | None = None
    record: Instance | None = None
    selectable: bool = False

    @property
    def info(self) -> dict[str, object] | None:
        if not self.selectable or self.target_id is None:
            return None
        if self.kind == "target":
            return {"schemaVersion": 1, "kind": "target", "targetId": self.target_id}
        if self.kind == "instance" and self.instance_id is not None:
            return {
                "schemaVersion": 1,
                "kind": "instance",
                "targetId": self.target_id,
                "instanceId": self.instance_id,
            }
        return None


@dataclass(frozen=True)
class PickerState:
    selected_key: str | None = None
    action: str = ACTION_OPEN
    request_id: str = ""
    notice: str = ""
    row_revision: str | None = None
    restore_selection: bool = False
    prefix_revision: str | None = None
    position_revision: str | None = None


@dataclass
class PickerContext:
    env: dict[str, str]
    config: DirectConfig
    config_error: str | None
    transport: Transport
    cache: CacheStore
    preferences: ViewPreferenceStore
    desktop: Desktop | object | None
    desktop_error: str | None
    registry: Registry | None
    actions: Actions | None


def build_context(
    environ: Mapping[str, str] | None = None,
    *,
    transport: Transport | None = None,
    desktop: Desktop | object | None = None,
    registry: Registry | None = None,
    actions: Actions | None = None,
    cache: CacheStore | None = None,
    preferences: ViewPreferenceStore | None = None,
    registry_root: str | Path | None = None,
    cache_root: str | Path | None = None,
    state_root: str | Path | None = None,
    config: DirectConfig | None = None,
) -> PickerContext:
    """Build the small local service context; callers may inject test services."""

    env = dict(os.environ if environ is None else environ)
    config_error: str | None = None
    if config is None:
        try:
            config = load_direct_config(env=env, user_home=owning_user_home())
        except ConfigError as error:
            config_error = str(error)
            config = DirectConfig(desktop=DesktopConfig(), environments=())
    transport = transport or Transport(env=env)
    cache = cache or CacheStore(cache_root, env=env)
    preferences = preferences or ViewPreferenceStore(state_root, env=env)
    desktop_error: str | None = None
    if desktop is None:
        try:
            desktop = Desktop(env)
        except DesktopError as error:
            desktop_error = str(error)
    if registry is None and desktop is not None:
        try:
            registry = Registry(
                str(desktop.generation), Path(registry_root) if registry_root else None
            )
        except (RegistryError, OSError, ValueError) as error:
            desktop_error = desktop_error or str(error)
    if actions is None and desktop is not None and registry is not None:
        try:
            actions = Actions(
                desktop,  # type: ignore[arg-type]
                registry,
                transport,
                terminal=config.desktop.terminal,
                env=env,
            )
        except (ActionError, DesktopError, RegistryError) as error:
            desktop_error = desktop_error or str(error)
    return PickerContext(
        env,
        config,
        config_error,
        transport,
        cache,
        preferences,
        desktop,
        desktop_error,
        registry,
        actions,
    )


def _safe_reference(target: Target) -> EntryReference:
    return EntryReference(
        target.key.host_id,
        UserScope(target.key.uid, target.key.account),
        target.key,
        target.spec_revision,
    )


def _record_target(record: Instance) -> tuple[Target, EntryReference]:
    try:
        target = parse_target(record.value["target"])
        reference = parse_entry_reference(record.value["entryReference"])
        target_id = target_storage_key(target.key.identity)
    except (RecordError, RegistryError, KeyError, TypeError) as error:
        raise RegistryError("invalid_instance_target_snapshot") from error
    if (
        target_id != record.target_id
        or reference.target_key != target.key
        or reference.expected_host_id != target.key.host_id
        or reference.expected_user.uid != target.key.uid
        or reference.expected_user.account != target.key.account
        or reference.spec_revision != target.spec_revision
    ):
        raise RegistryError("instance_target_reference_mismatch")
    return target, reference


def _status_row(display: str, meta: str = "") -> PickerRow:
    safe = clean_text(display)
    return PickerRow("status", None, safe, clean_text(meta or safe), selectable=False)


def _provider_targets(host: HostCatalog) -> dict[str, Target]:
    result: dict[str, Target] = {}
    for provider in host.providers:
        for target in provider.targets:
            result[target.key.identity] = target
    return result


def _read_instances(
    registry: Registry | None,
    desktop: Desktop | object | None,
    windows: tuple[Window, ...],
) -> tuple[dict[str, list[Instance]], list[PickerRow]]:
    grouped: dict[str, list[Instance]] = {}
    diagnostics: list[PickerRow] = []
    if registry is None:
        return grouped, diagnostics
    try:
        target_ids = registry.target_ids()
    except (RegistryError, OSError) as error:
        return grouped, [_status_row(f"Local registrations unavailable · {error}")]
    for target_id in target_ids:
        try:
            if desktop is not None:
                records = registry.reconcile(target_id, desktop, windows)  # type: ignore[arg-type]
            else:
                records = registry.records(target_id)
        except (RegistryError, DesktopError, OSError) as error:
            diagnostics.append(_status_row(f"Window record needs repair · {error}"))
            continue
        for record in records:
            if record.state not in _ACTIVE_STATES:
                continue
            try:
                _record_target(record)
            except RegistryError as error:
                diagnostics.append(_status_row(f"Window record needs refresh · {error}"))
                continue
            grouped.setdefault(target_id, []).append(record)
    for records in grouped.values():
        records.sort(key=lambda item: (item.value["ordinal"], item.instance_id))
    return grouped, diagnostics


def build_rows(
    snapshot: CatalogSnapshot | None,
    *,
    registry: Registry | None = None,
    desktop: Desktop | object | None = None,
    windows: tuple[Window, ...] = (),
    desktop_error: str | None = None,
) -> tuple[PickerRow, ...]:
    """Build the stable flat all-host target/instance list without probing hosts."""

    rows: list[PickerRow] = []
    active_by_target, record_diagnostics = _read_instances(registry, desktop, windows)
    rows.extend(record_diagnostics)
    targets: dict[str, Target] = {}
    host_order: list[str] = []
    host_display: dict[str, str] = {}
    if snapshot is not None:
        host_order = [host.host_id for host in snapshot.hosts]
        host_display = {host.host_id: host.display for host in snapshot.hosts}
        for host in snapshot.hosts:
            targets.update(_provider_targets(host))
            if host.status in {"partial", "error", "removed"}:
                suffix = {
                    "partial": "inventory partial",
                    "error": "inventory unavailable; showing last-known targets",
                    "removed": "not in current Host Mesh; showing registered windows",
                }[host.status]
                rows.append(_status_row(f"{host.display} · {suffix}", host.host_id))
            for provider in host.providers:
                if provider.status == "missing" and not provider.errors and not provider.targets:
                    continue
                if provider.status in {"partial", "unknown", "missing", "unsupported"}:
                    reason = provider.reason or provider.status
                    rows.append(
                        _status_row(
                            f"{host.display} · {_PROVIDER_NAMES[provider.provider]} {provider.status}: {reason}",
                            f"{host.host_id} {_PROVIDER_NAMES[provider.provider]} {provider.status}",
                        )
                    )
    for records in active_by_target.values():
        for record in records:
            target, _reference = _record_target(record)
            targets.setdefault(target.key.identity, target)
            if target.key.host_id not in host_order:
                host_order.append(target.key.host_id)
                host_display.setdefault(target.key.host_id, target.key.host_id)
    if desktop_error:
        rows.insert(0, _status_row(f"Local window state unknown · {desktop_error}"))
    if snapshot is None:
        rows.insert(0, _status_row("No host catalog is available; use Alt+R to refresh"))

    targets_by_host: dict[str, list[Target]] = {}
    for target in targets.values():
        targets_by_host.setdefault(target.key.host_id, []).append(target)
    for host_id in sorted(set(targets_by_host) - set(host_order)):
        host_order.append(host_id)

    for host_id in host_order:
        host_targets = targets_by_host.get(host_id, [])
        provider_order = {name: index for index, name in enumerate(("codex", "claude"))}
        host_targets.sort(
            key=lambda item: (provider_order[item.key.provider], item.key.home, item.key.identity)
        )
        for target in host_targets:
            target_id = target_storage_key(target.key.identity)
            records = active_by_target.get(target_id, [])
            host = host_display.get(host_id, host_id)
            provider_name = _PROVIDER_NAMES[target.key.provider]
            environment = target.label
            # Older helpers included a provider prefix in default labels;
            # declarations may contain just the environment name.
            for prefix in (f"{provider_name} · ", f"{target.key.provider.capitalize()} · "):
                if environment.startswith(prefix):
                    environment = environment[len(prefix) :]
                    break
            display_name = (
                provider_name
                if environment == provider_name
                else f"{provider_name} · {environment}"
            )
            identity_text = f"{host} {display_name} {target.key.home}"
            if records:
                for record in records:
                    number = record.value["ordinal"]
                    title = ""
                    if desktop is not None and record.state == "live":
                        terminal = record.process("terminal")
                        try:
                            window = (
                                desktop.match(windows, record.marker, terminal)
                                if terminal is not None
                                else None
                            )
                        except DesktopError:
                            window = None
                        if window is not None:
                            title = clean_text(window.title, limit=1024)
                    if record.state == "live":
                        detail = f"Window {number}" + (f" · {title}" if title else "")
                        kind = "instance"
                        selectable = True
                    else:
                        labels = {
                            "reserved": "starting",
                            "dispatching": "starting",
                            "opening": "opening",
                            "uncertain": "presence uncertain",
                        }
                        detail = f"Window {number} · {labels.get(record.state, 'status unknown')}"
                        kind = "instance-status"
                        selectable = False
                    key = f"i:{target_id}:{record.instance_id}"
                    rows.append(
                        PickerRow(
                            kind,
                            key,
                            clean_text(f"{host} · {display_name} — {detail}"),
                            clean_text(f"{identity_text} {detail}"),
                            target_id,
                            record.instance_id,
                            target,
                            _safe_reference(target),
                            record,
                            selectable,
                        )
                    )
                continue
            if target.capability.status != "supported":
                reason = target.capability.reason or target.capability.status
                rows.append(
                    _status_row(
                        f"{host} · {display_name} — launch {target.capability.status}: {reason}",
                        identity_text,
                    )
                )
                continue
            key = f"t:{target_id}"
            rows.append(
                PickerRow(
                    "target",
                    key,
                    clean_text(f"{host} · {display_name}"),
                    clean_text(identity_text),
                    target_id,
                    target=target,
                    reference=_safe_reference(target),
                    selectable=True,
                )
            )

    if not rows:
        rows.append(_status_row("No supported host targets or registered windows"))
    return tuple(rows)


def _parse_info(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, str) or len(raw) > 1024:
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, json.JSONDecodeError, RecursionError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or type(value.get("schemaVersion")) is not int
    ):
        return None
    kind = value.get("kind")
    target_id = value.get("targetId")
    if (
        not isinstance(kind, str)
        or kind not in {"target", "instance"}
        or not isinstance(target_id, str)
        or not _HEX_64.fullmatch(target_id)
    ):
        return None
    if kind == "target" and set(value) == {"schemaVersion", "kind", "targetId"}:
        return {"kind": kind, "targetId": target_id}
    instance_id = value.get("instanceId")
    if (
        kind == "instance"
        and set(value)
        == {
            "schemaVersion",
            "kind",
            "targetId",
            "instanceId",
        }
        and isinstance(instance_id, str)
        and _HEX_32.fullmatch(instance_id)
    ):
        return {"kind": kind, "targetId": target_id, "instanceId": instance_id}
    return None


def _row_for_info(rows: tuple[PickerRow, ...], info: Mapping[str, str] | None) -> PickerRow | None:
    if info is None:
        return None
    kind, target_id = info["kind"], info["targetId"]
    wanted = f"t:{target_id}" if kind == "target" else f"i:{target_id}:{info['instanceId']}"
    return next((row for row in rows if row.key == wanted and row.selectable), None)


def _state_json(state: PickerState) -> str:
    value = {
        "schemaVersion": 1,
        "selectedKey": state.selected_key,
        "action": state.action,
        "requestId": state.request_id,
        "notice": clean_text(state.notice, limit=MAX_MESSAGE_LENGTH),
        "rowRevision": state.row_revision,
        "restoreSelection": state.restore_selection,
        "prefixRevision": state.prefix_revision,
        "positionRevision": state.position_revision,
    }
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_DATA_BYTES:
        raise ValueError("picker callback state exceeds its limit")
    return encoded


def _parse_state(raw: object) -> PickerState:
    if not isinstance(raw, str) or len(raw.encode("utf-8", "ignore")) > MAX_DATA_BYTES:
        return PickerState(request_id=uuid.uuid4().hex)
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, json.JSONDecodeError, RecursionError):
        return PickerState(request_id=uuid.uuid4().hex)
    expected = {
        "schemaVersion",
        "selectedKey",
        "action",
        "requestId",
        "notice",
    }
    if (
        not isinstance(value, dict)
        or not expected <= set(value)
        or set(value)
        - expected
        - {"rowRevision", "restoreSelection", "prefixRevision", "positionRevision"}
        or type(value.get("schemaVersion")) is not int
        or value["schemaVersion"] != 1
    ):
        return PickerState(request_id=uuid.uuid4().hex)
    key = value.get("selectedKey")
    if key is not None and (not isinstance(key, str) or len(key) > 128):
        key = None
    action = value.get("action")
    if not isinstance(action, str) or action not in _ACTIONS:
        action = ACTION_OPEN
    request_id = value.get("requestId")
    if not isinstance(request_id, str) or not _HEX_32.fullmatch(request_id):
        request_id = uuid.uuid4().hex
    notice = clean_text(value.get("notice", ""), limit=MAX_MESSAGE_LENGTH)
    revision = value.get("rowRevision")
    if not isinstance(revision, str) or not _HEX_64.fullmatch(revision):
        revision = None
    restore = value.get("restoreSelection") is True and revision is not None
    prefix = value.get("prefixRevision")
    if not isinstance(prefix, str) or not _HEX_64.fullmatch(prefix):
        prefix = None
    position = value.get("positionRevision")
    if not isinstance(position, str) or not _HEX_64.fullmatch(position):
        position = None
    return PickerState(key, action, request_id, notice, revision, restore, prefix, position)


def _local_view(context: PickerContext) -> tuple[tuple[Window, ...], str | None]:
    if context.desktop is None:
        return (), context.desktop_error or "desktop identity unavailable"
    try:
        windows = tuple(context.desktop.windows())  # type: ignore[attr-defined]
        return freeze_titles(windows, context.env), None
    except (DesktopError, OSError, ValueError, TypeError) as error:
        return (), str(error)


def _preferred_key(preference: ViewPreference, rows: tuple[PickerRow, ...]) -> str | None:
    if preference.last_target_id is None:
        return None
    if preference.last_instance_id is not None:
        exact = f"i:{preference.last_target_id}:{preference.last_instance_id}"
        if any(row.key == exact and row.selectable for row in rows):
            return exact
    target = f"t:{preference.last_target_id}"
    if any(row.key == target and row.selectable for row in rows):
        return target
    return None


def _selected_index(rows: tuple[PickerRow, ...], key: str | None) -> int | None:
    if key is None:
        return None
    return next((index for index, row in enumerate(rows) if row.key == key), None)


def _action_message(state: PickerState) -> str:
    labels = []
    for action, text in ((ACTION_OPEN, "Open"), (ACTION_ADDITIONAL, "New window")):
        label = html.escape(text, quote=False)
        if action == state.action:
            label = f'<span foreground="#42a5f5" weight="bold">[{label}]</span>'
        labels.append(label)
    hint = f"Enter: {' · '.join(labels)}  │  Tab: Cycle actions"
    if not state.notice:
        return hint
    notice = html.escape(clean_text(state.notice, limit=MAX_MESSAGE_LENGTH), quote=False)
    return f"{hint}\u2028\u2028{notice}"


def _render(
    rows: tuple[PickerRow, ...],
    state: PickerState,
    *,
    preserve: bool,
    select: bool,
    polling: bool = False,
) -> str:
    prompt = "New window" if state.action == ACTION_ADDITIONAL else "Open"
    signatures = [(row.key, row.selectable, row.display, row.meta) for row in rows]
    revision = hashlib.sha256(json.dumps(signatures).encode()).hexdigest()
    index = _selected_index(rows, state.selected_key)
    prefix = hashlib.sha256(
        json.dumps(signatures[: index + 1] if index is not None else []).encode()
    ).hexdigest()
    position = hashlib.sha256(
        json.dumps(
            [(row.key, row.selectable) for row in rows[: index + 1]] if index is not None else []
        ).encode()
    ).hexdigest()
    restore = (
        preserve
        and state.row_revision is not None
        and state.row_revision != revision
        and state.prefix_revision != prefix
        and index is not None
        and (not state.restore_selection or state.position_revision != position)
    )
    # Rofi applies new-selection against the *previous* filtered line map.
    # After rows change, restore identity in one short follow-up once Rofi has
    # rebuilt that map. With native sorting disabled, unchanged rows through
    # the selection need no correction when later rows arrive or change.
    # A correction must finish even when live titles keep changing. Only a
    # further change to row positions/eligibility can restart that correction.
    # Explicit user actions still honor their current row.
    state = replace(
        state,
        row_revision=revision,
        restore_selection=restore,
        prefix_revision=prefix,
        position_revision=position,
    )
    delay = 0.02 if restore else (POLL_SECONDS if polling else 0.0)
    headers = [
        _protocol("prompt", prompt),
        # Rofi suppresses hotkeys when a filter has no selected row if this is
        # true. Typed/custom input remains inert at the action boundary below.
        _protocol("no-custom", "false"),
        _protocol("use-hot-keys", "true"),
        _protocol("markup-rows", "true"),
        _protocol(
            "theme",
            f'configuration {{ timeout {{ delay: {delay}; action: "kb-custom-19"; }} }}',
        ),
        _protocol("data", _state_json(state)),
        # Rofi reads retention flags from the preceding frame, including the
        # first timeout or Tab callback after opening.
        _protocol("keep-filter", "true"),
        _protocol("keep-selection", "true"),
        # Always replace the message so completed notices leave only the bar.
        _protocol("message", _action_message(state)),
    ]
    if select and not restore:
        if index is not None:
            headers.append(_protocol("new-selection", index))
    rendered: list[str] = []
    for row in rows:
        text = html.escape(clean_text(row.display), quote=False)
        options: list[str] = []
        info = row.info
        if info is not None:
            options.extend(("info", json.dumps(info, separators=(",", ":"))))
        options.extend(("meta", clean_text(row.meta, limit=8192)))
        if not row.selectable:
            options.extend(("nonselectable", "true"))
        rendered.append(text + "\x00" + "\x1f".join(options))
    if preserve:
        return RECORD_SEPARATOR.join((*headers, *rendered)) + RECORD_SEPARATOR
    headers.append(_protocol("delim", r"\x1e"))
    return "\n".join(headers) + "\n" + RECORD_SEPARATOR.join(rendered) + RECORD_SEPARATOR


def _load_snapshot(context: PickerContext) -> tuple[CatalogSnapshot | None, str | None]:
    try:
        return context.cache.load(), None
    except CacheError as error:
        return None, f"Catalog cache unavailable · {error}"


def _refresh_notice(refresh: RefreshStatus | None, rows: tuple[PickerRow, ...]) -> str:
    if refresh is None or (refresh.pending and any(row.selectable for row in rows)):
        return ""
    return refresh.message


def _refresh(context: PickerContext) -> tuple[CatalogSnapshot | None, str | None]:
    """Synchronously refresh the bounded fleet snapshot and read the cache."""

    refresh_error: str | None = None
    try:
        context.cache.refresh(context.transport)
    except (CacheError, TransportError, OSError, ValueError) as error:
        refresh_error = f"Host refresh failed · {clean_text(error, limit=512)}"
    snapshot, cache_error = _load_snapshot(context)
    if cache_error:
        if refresh_error:
            return snapshot, f"{refresh_error}; {cache_error}"
        return snapshot, cache_error
    return snapshot, refresh_error


def _current_row(rows: tuple[PickerRow, ...], key: str | None) -> PickerRow | None:
    return next((row for row in rows if row.key == key and row.selectable), None)


def _request_for(state: PickerState) -> PickerState:
    # One token is armed in each rendered frame. Replayed callbacks therefore
    # carry the same registry id even if they arrive before Rofi redraws. A
    # deliberate additional action gets a new token when Tab cycles the mode.
    if _HEX_32.fullmatch(state.request_id):
        return state
    return replace(state, request_id=uuid.uuid4().hex)


def _handle_selection(
    context: PickerContext, row: PickerRow, state: PickerState
) -> tuple[bool, PickerState]:
    state = _request_for(state)
    if context.actions is None:
        return False, replace(
            state, notice="Local desktop is unavailable; window actions are disabled"
        )
    try:
        if state.action == ACTION_ADDITIONAL:
            if row.target is None or row.reference is None:
                raise ActionError("stale_selection", "selected target is no longer available")
            record = context.actions.open_target(
                row.target,
                row.reference,
                additional=True,
                request_id=state.request_id,
            )
        elif row.kind == "instance":
            if row.target_id is None or row.instance_id is None:
                raise ActionError("stale_selection", "selected window identity is invalid")
            record = context.actions.focus_instance(row.target_id, row.instance_id)
        else:
            if row.target is None or row.reference is None:
                raise ActionError("stale_selection", "selected target is no longer available")
            record = context.actions.open_target(
                row.target,
                row.reference,
                additional=False,
                request_id=state.request_id,
            )
        return True, replace(
            state,
            selected_key=f"i:{record.target_id}:{record.instance_id}",
            notice="",
        )
    except ActionError as error:
        notice = f"{error.code.replace('_', ' ')} · {clean_text(error, limit=512)}"
        if error.code in _STALE_FOCUS_CODES:
            notice = "Window selection is stale; refresh the local view and select again"
        return False, replace(state, notice=notice)


def _save_success(context: PickerContext, row: PickerRow, record: Instance) -> None:
    target_id = row.target_id or record.target_id
    context.preferences.save(ViewPreference(target_id, record.instance_id))


def run_rofi(
    environ: Mapping[str, str] | None = None,
    *,
    transport: Transport | None = None,
    context: PickerContext | None = None,
    registry_root: str | Path | None = None,
) -> int:
    """Serve one Rofi callback from a cached snapshot with explicit refreshes."""

    env = dict(os.environ if environ is None else environ)
    context = context or build_context(env, transport=transport, registry_root=registry_root)
    if transport is not None:
        context.transport = transport
    try:
        retv = int(env.get("ROFI_RETV", "0"))
    except (TypeError, ValueError):
        retv = 0
    refresh = read_status(env)
    polling = refresh is not None and refresh.pending

    if retv == 0:
        state = PickerState(request_id=uuid.uuid4().hex)
        saved = context.preferences.load()
        snapshot, cache_error = _load_snapshot(context)
        notice = cache_error or ""
        if (
            refresh is None
            and snapshot is not None
            and snapshot.is_stale(context.config.desktop.catalog_ttl_seconds)
        ):
            notice = notice or "Showing saved inventory; use Alt+R to refresh"
        windows, window_error = _local_view(context)
        rows = build_rows(
            snapshot,
            registry=context.registry,
            desktop=context.desktop,
            windows=windows,
            desktop_error=window_error,
        )
        state = replace(state, notice=notice or _refresh_notice(refresh, rows))
        if context.config_error:
            state = replace(
                state, notice=f"Configuration ignored · {clean_text(context.config_error)}"
            )
        preferred = _preferred_key(saved, rows)
        default_key = next((row.key for row in rows if row.selectable), None)
        state = replace(state, selected_key=preferred or default_key)
        print(_render(rows, state, preserve=False, select=True, polling=polling), end="")
        return 0

    state = _parse_state(env.get("ROFI_DATA"))
    snapshot, cache_error = _load_snapshot(context)
    notice = state.notice or cache_error or ""
    raw_info = _parse_info(env.get("ROFI_INFO"))
    selected_key: str | None = state.selected_key
    restoring = retv == ROFI_RETV_CUSTOM_19 and state.restore_selection
    if raw_info is not None and not restoring:
        selected_key = (
            f"t:{raw_info['targetId']}"
            if raw_info["kind"] == "target"
            else f"i:{raw_info['targetId']}:{raw_info['instanceId']}"
        )
    state = replace(
        state,
        selected_key=selected_key,
        notice=notice,
        prefix_revision=state.prefix_revision if selected_key == state.selected_key else None,
    )
    did_action = False

    if retv == ROFI_RETV_CUSTOM_1:
        refresh_error = None
        if not polling:
            snapshot, refresh_error = _refresh(context)
        state = replace(
            state,
            notice=refresh.message if polling else (refresh_error or ""),
        )
    elif retv in {ROFI_RETV_CUSTOM_7, ROFI_RETV_CUSTOM_8}:
        action = ACTION_ADDITIONAL if state.action == ACTION_OPEN else ACTION_OPEN
        state = replace(state, action=action, request_id=uuid.uuid4().hex, notice="")
    elif retv == ROFI_RETV_CUSTOM_19:
        # Only read published cache/status; polling never starts another probe.
        state = replace(state, notice=refresh.message if refresh is not None else "")
    elif retv == ROFI_RETV_SELECTED:
        if raw_info is None:
            state = replace(
                state,
                notice="Custom input is ignored; select a listed row",
            )
        else:
            windows, window_error = _local_view(context)
            rows = build_rows(
                snapshot,
                registry=context.registry,
                desktop=context.desktop,
                windows=windows,
                desktop_error=window_error,
            )
            row = _row_for_info(rows, raw_info)
            if row is None:
                refresh_error = None
                if not polling:
                    snapshot, refresh_error = _refresh(context)
                state = replace(
                    state,
                    notice=refresh_error
                    or "Selection is stale or unavailable; refreshed inventory; choose a current row",
                )
            else:
                did_action, state = _handle_selection(context, row, state)
                if did_action:
                    result = _current_row(rows, state.selected_key)
                    record = None
                    if row.target_id is not None and context.registry is not None:
                        try:
                            record = next(
                                item
                                for item in context.registry.records(row.target_id)
                                if item.instance_id == state.selected_key.rsplit(":", 1)[-1]
                            )
                        except (RegistryError, StopIteration):
                            record = None
                    if record is not None:
                        _save_success(context, result or row, record)
                    return 0
    else:
        state = replace(state, notice="Unsupported picker callback; use Open, Tab, or Alt+R")

    windows, window_error = _local_view(context)
    rows = build_rows(
        snapshot,
        registry=context.registry,
        desktop=context.desktop,
        windows=windows,
        desktop_error=window_error,
    )
    if retv == ROFI_RETV_CUSTOM_19 or (retv == ROFI_RETV_CUSTOM_1 and polling):
        state = replace(state, notice=_refresh_notice(refresh, rows))
    if state.selected_key and not any(row.key == state.selected_key for row in rows):
        fallback = state.selected_key.split(":", 2)
        target_key = f"t:{fallback[1]}" if len(fallback) > 1 else None
        if target_key and any(row.key == target_key and row.selectable for row in rows):
            state = replace(state, selected_key=target_key, prefix_revision=None)
        else:
            next_key = next((row.key for row in rows if row.selectable), None)
            state = replace(
                state,
                selected_key=next_key,
                prefix_revision=None,
                notice=state.notice or "Selected window is no longer present",
            )
    select = (
        retv in {ROFI_RETV_CUSTOM_1, ROFI_RETV_CUSTOM_7, ROFI_RETV_CUSTOM_8, ROFI_RETV_SELECTED}
        or raw_info is not None
        or restoring
    )
    print(_render(rows, state, preserve=True, select=select, polling=polling), end="")
    return 0
