"""Strict provider-owned configuration for Rofi Agent Plus."""

from __future__ import annotations

import hashlib
import json
import os
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
CONFIG_RELATIVE_PATH = Path("rofi-agent-plus") / "config.toml"
CONFIG_KEYS = frozenset({"max_sessions", "refresh_seconds"})


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


@dataclass(frozen=True)
class PickerConfig:
    """Only the provider cache's durable settings belong to Agent Plus."""

    max_sessions: int = DEFAULT_LIMIT
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS

    def validate(self) -> None:
        _integer(self.max_sessions, "max_sessions", MIN_MAX_SESSIONS, MAX_MAX_SESSIONS)
        _integer(
            self.refresh_seconds,
            "refresh_seconds",
            MIN_REFRESH_SECONDS,
            MAX_REFRESH_SECONDS,
        )

    @property
    def fingerprint(self) -> str:
        """Fingerprint only settings that alter provider discovery results."""

        encoded = json.dumps(
            {"max_sessions": self.max_sessions}, sort_keys=True, separators=(",", ":")
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
    return PickerConfig().with_overrides(
        max_sessions=raw.get("max_sessions"),
        refresh_seconds=raw.get("refresh_seconds"),
    )


def config_from_mapping(values: Mapping[str, Any]) -> PickerConfig:
    """Build config from a mapping, primarily for deterministic tests."""

    unknown = sorted(set(values) - CONFIG_KEYS)
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(unknown)}")
    return PickerConfig().with_overrides(
        max_sessions=values.get("max_sessions"),
        refresh_seconds=values.get("refresh_seconds"),
    )
