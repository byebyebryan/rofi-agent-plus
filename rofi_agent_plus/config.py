"""Strict provider-owned configuration for Rofi Agent Plus."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .engine import DEFAULT_LIMIT, PickerError

DEFAULT_REFRESH_SECONDS = 30
MIN_MAX_SESSIONS = 1
MAX_MAX_SESSIONS = 100
MIN_REFRESH_SECONDS = 5
MAX_REFRESH_SECONDS = 300
MAX_HOST_PROVIDER_ENTRIES = 128
MAX_HOST_ID_LENGTH = 256
SUPPORTED_PROVIDERS = ("codex", "claude")
_HOST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z", re.ASCII)
CONFIG_RELATIVE_PATH = Path("rofi-agent-plus") / "config.toml"
CONFIG_KEYS = frozenset({"max_sessions", "refresh_seconds", "host_providers"})


class ConfigError(PickerError):
    """A user-facing configuration error."""


def _config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home) if config_home else Path.home() / ".config"
    return root / CONFIG_RELATIVE_PATH


def _integer(value: Any, key: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"config {key} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigError(f"config {key} must be between {minimum} and {maximum}")
    return value


def _host_providers(value: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(value, Mapping):
        raise ConfigError("config host_providers must be a table")
    if not value:
        raise ConfigError("config host_providers must contain at least one host")
    if len(value) > MAX_HOST_PROVIDER_ENTRIES:
        raise ConfigError(
            f"config host_providers must contain at most {MAX_HOST_PROVIDER_ENTRIES} hosts"
        )

    normalized: dict[str, tuple[str, ...]] = {}
    for host_id, selected in value.items():
        if (
            not isinstance(host_id, str)
            or len(host_id) > MAX_HOST_ID_LENGTH
            or not _HOST_ID.fullmatch(host_id)
        ):
            raise ConfigError("config host_providers contains an invalid host id")
        key = host_id.casefold()
        if key in normalized:
            raise ConfigError("config host_providers contains case-colliding host ids")
        if not isinstance(selected, (list, tuple)) or not selected:
            raise ConfigError(f"config host_providers.{host_id} must select at least one provider")

        selected_providers: set[str] = set()
        for provider in selected:
            if not isinstance(provider, str) or provider not in SUPPORTED_PROVIDERS:
                raise ConfigError(
                    f"config host_providers.{host_id} contains an unsupported provider"
                )
            if provider in selected_providers:
                raise ConfigError(f"config host_providers.{host_id} contains a duplicate provider")
            selected_providers.add(provider)
        normalized[key] = tuple(
            provider for provider in SUPPORTED_PROVIDERS if provider in selected_providers
        )
    return tuple(sorted(normalized.items()))


@dataclass(frozen=True)
class PickerConfig:
    """Only the provider cache's durable settings belong to Agent Plus."""

    max_sessions: int = DEFAULT_LIMIT
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    host_providers: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def validate(self) -> None:
        _integer(self.max_sessions, "max_sessions", MIN_MAX_SESSIONS, MAX_MAX_SESSIONS)
        _integer(
            self.refresh_seconds,
            "refresh_seconds",
            MIN_REFRESH_SECONDS,
            MAX_REFRESH_SECONDS,
        )
        if not isinstance(self.host_providers, tuple):
            raise ConfigError("config host_providers must use an immutable canonical value")
        if len(self.host_providers) > MAX_HOST_PROVIDER_ENTRIES:
            raise ConfigError(
                f"config host_providers must contain at most {MAX_HOST_PROVIDER_ENTRIES} hosts"
            )
        seen: set[str] = set()
        previous_host: str | None = None
        for entry in self.host_providers:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ConfigError("config host_providers must use an immutable canonical value")
            host_id, selected = entry
            if (
                not isinstance(host_id, str)
                or len(host_id) > MAX_HOST_ID_LENGTH
                or not _HOST_ID.fullmatch(host_id)
                or host_id != host_id.casefold()
                or not isinstance(selected, tuple)
                or not selected
            ):
                raise ConfigError("config host_providers must use an immutable canonical value")
            if host_id in seen or (previous_host is not None and host_id <= previous_host):
                raise ConfigError("config host_providers must use an immutable canonical value")
            if any(provider not in SUPPORTED_PROVIDERS for provider in selected):
                raise ConfigError("config host_providers contains an unsupported provider")
            if len(set(selected)) != len(selected):
                raise ConfigError("config host_providers contains a duplicate provider")
            canonical = tuple(provider for provider in SUPPORTED_PROVIDERS if provider in selected)
            if selected != canonical:
                raise ConfigError("config host_providers must use an immutable canonical value")
            seen.add(host_id)
            previous_host = host_id

    def providers_for(self, host_id: str) -> tuple[str, ...]:
        """Return the configured provider set, defaulting to both supported providers."""

        self.validate()
        if (
            not isinstance(host_id, str)
            or len(host_id) > MAX_HOST_ID_LENGTH
            or not _HOST_ID.fullmatch(host_id)
        ):
            raise ConfigError("host id is invalid")
        wanted = host_id.casefold()
        for configured_host, providers in self.host_providers:
            if configured_host == wanted:
                return providers
        return SUPPORTED_PROVIDERS

    @property
    def fingerprint(self) -> str:
        """Fingerprint only settings that alter provider discovery results."""

        self.validate()
        encoded = json.dumps(
            {
                "max_sessions": self.max_sessions,
                "host_providers": [
                    [host_id, list(providers)] for host_id, providers in self.host_providers
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def with_overrides(self, **values: Any) -> PickerConfig:
        unknown = sorted(set(values) - CONFIG_KEYS)
        if unknown:
            raise ConfigError(f"unknown config key(s): {', '.join(unknown)}")
        candidate = self
        for field, minimum, maximum in (
            ("max_sessions", MIN_MAX_SESSIONS, MAX_MAX_SESSIONS),
            ("refresh_seconds", MIN_REFRESH_SECONDS, MAX_REFRESH_SECONDS),
        ):
            value = values.get(field)
            if value is not None:
                candidate = replace(candidate, **{field: _integer(value, field, minimum, maximum)})
        if "host_providers" in values:
            candidate = replace(candidate, host_providers=_host_providers(values["host_providers"]))
        candidate.validate()
        return candidate


def load_config(path: Path | None = None) -> PickerConfig:
    """Load the optional strict TOML config, or provider defaults."""

    path = path or _config_path()
    if not path.exists():
        return PickerConfig()
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a TOML table")
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(unknown)}")
    overrides: dict[str, Any] = {
        "max_sessions": raw.get("max_sessions"),
        "refresh_seconds": raw.get("refresh_seconds"),
    }
    if "host_providers" in raw:
        overrides["host_providers"] = raw["host_providers"]
    return PickerConfig().with_overrides(
        **overrides,
    )


def config_from_mapping(values: Mapping[str, Any]) -> PickerConfig:
    """Build config from a mapping, primarily for deterministic tests."""

    unknown = sorted(set(values) - CONFIG_KEYS)
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(unknown)}")
    overrides: dict[str, Any] = {
        "max_sessions": values.get("max_sessions"),
        "refresh_seconds": values.get("refresh_seconds"),
    }
    if "host_providers" in values:
        overrides["host_providers"] = values["host_providers"]
    return PickerConfig().with_overrides(**overrides)
