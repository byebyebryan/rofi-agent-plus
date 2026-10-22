"""Direct-TUI configuration, independent of the legacy session picker."""

from __future__ import annotations

import os
import pwd
import stat
import tomllib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

DIRECT_CONFIG_RELATIVE_PATH = Path("rofi-agent-plus") / "direct.toml"
MAX_CONFIG_BYTES = 256 << 10
MAX_ENVIRONMENTS = 128
MAX_PATH_LENGTH = 4096
MAX_LABEL_LENGTH = 256
MAX_DECLARATION_ID_LENGTH = 128
SUPPORTED_PROVIDERS = ("codex", "claude")
DEFAULT_TERMINAL = ("kitty",)
DEFAULT_CATALOG_TTL_SECONDS = 30


class ConfigError(ValueError):
    """A malformed top-level direct configuration."""


@dataclass(frozen=True)
class DesktopConfig:
    terminal: tuple[str, ...] = DEFAULT_TERMINAL
    catalog_ttl_seconds: int = DEFAULT_CATALOG_TTL_SECONDS


@dataclass(frozen=True)
class EnvironmentDeclaration:
    provider: str
    home: str
    label: str
    declaration_id: str | None = None


@dataclass(frozen=True)
class DeclarationIssue:
    index: int
    code: str
    message: str
    provider: str | None = None
    home: str | None = None


@dataclass(frozen=True)
class DirectConfig:
    desktop: DesktopConfig
    environments: tuple[EnvironmentDeclaration, ...]
    issues: tuple[DeclarationIssue, ...] = ()


def _check_text(value: object, label: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ConfigError(f"{label} is invalid")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError(f"{label} must be between {minimum} and {maximum}")
    return value


def owning_user_home(*, uid: int | None = None) -> Path:
    effective_uid = os.geteuid() if uid is None else uid
    try:
        return Path(pwd.getpwuid(effective_uid).pw_dir)
    except KeyError as error:
        raise ConfigError("the effective user has no account home") from error


def default_config_path(
    *, env: Mapping[str, str] | None = None, user_home: Path | str | None = None
) -> Path:
    environment = os.environ if env is None else env
    configured = environment.get("XDG_CONFIG_HOME")
    if configured:
        root = Path(configured)
        if not root.is_absolute():
            raise ConfigError("XDG_CONFIG_HOME must be absolute")
    else:
        root = Path(user_home) if user_home is not None else owning_user_home()
        root = root / ".config"
    return root / DIRECT_CONFIG_RELATIVE_PATH


def canonical_native_home(value: str, *, user_home: Path | str) -> str:
    """Resolve a native selector using only the owning account's path rules."""

    raw = _check_text(value, "environment home", MAX_PATH_LENGTH)
    if "$" in raw:
        raise ConfigError("environment home cannot contain shell variables")
    home = Path(user_home)
    if raw == "~":
        candidate = home
    elif raw.startswith("~/"):
        candidate = home / raw[2:]
    elif raw.startswith("~"):
        raise ConfigError("named-user home expansion is unsupported")
    else:
        candidate = Path(raw)
    if not candidate.is_absolute():
        raise ConfigError("environment home must be absolute or start with `~/`")
    try:
        return str(candidate.resolve(strict=False))
    except (OSError, RuntimeError) as error:
        raise ConfigError("environment home cannot be canonicalized") from error


def expand_native_home(value: str, *, user_home: Path | str) -> str:
    """Expand only a leading `~` using the owning account's home."""
    return canonical_native_home(value, user_home=user_home)


def _default_label(provider: str, home: str) -> str:
    basename = PurePosixPath(home).name
    if provider == "codex" and basename == ".codex":
        scope = "Default"
    elif provider == "claude" and basename == ".claude":
        scope = "Default"
    else:
        scope = basename or "Home"
    return f"{provider.capitalize()} · {scope}"


def _parse_desktop(value: object) -> DesktopConfig:
    if not isinstance(value, Mapping):
        raise ConfigError("desktop must be a table")
    unknown = set(value) - {"terminal", "catalog_ttl_seconds"}
    if unknown:
        raise ConfigError("desktop contains an unsupported setting")
    terminal = value.get("terminal", list(DEFAULT_TERMINAL))
    if (
        not isinstance(terminal, list)
        or not 1 <= len(terminal) <= 16
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > 4096
            or any(unicodedata.category(char).startswith("C") for char in item)
            for item in terminal
        )
    ):
        raise ConfigError("desktop terminal must be a bounded argument vector")
    if Path(terminal[0]).name != "kitty":
        raise ConfigError("desktop terminal must select the supported Kitty backend")
    ttl = _integer(
        value.get("catalog_ttl_seconds", DEFAULT_CATALOG_TTL_SECONDS), "catalog_ttl_seconds", 1, 300
    )
    return DesktopConfig(tuple(terminal), ttl)


def _parse_declaration(value: object, index: int) -> EnvironmentDeclaration:
    if not isinstance(value, Mapping):
        raise ConfigError("environment declaration must be a table")
    unknown = set(value) - {"provider", "home", "label", "id"}
    if unknown:
        raise ConfigError("environment declaration contains an unsupported setting")
    provider = value.get("provider")
    if not isinstance(provider, str) or provider not in SUPPORTED_PROVIDERS:
        raise ConfigError("environment provider is unsupported")
    home = _check_text(value.get("home"), "environment home", MAX_PATH_LENGTH)
    if "$" in home:
        raise ConfigError("environment home cannot contain shell variables")
    if not (home.startswith("~/") or home == "~" or Path(home).is_absolute()):
        raise ConfigError("environment home must be absolute or start with `~/`")
    if home.startswith("~") and home not in {"~"} and not home.startswith("~/"):
        raise ConfigError("named-user home expansion is unsupported")
    label_value = value.get("label")
    if label_value is None:
        label = _default_label(provider, home)
    else:
        label = _check_text(label_value, "environment label", MAX_LABEL_LENGTH)
    declaration_id_value = value.get("id")
    declaration_id = (
        None
        if declaration_id_value is None
        else _check_text(declaration_id_value, "environment id", MAX_DECLARATION_ID_LENGTH)
    )
    return EnvironmentDeclaration(provider, home, label, declaration_id)


def parse_direct_config(value: object) -> DirectConfig:
    if not isinstance(value, Mapping):
        raise ConfigError("direct configuration must be a table")
    unknown = set(value) - {"schema_version", "desktop", "environments"}
    if unknown:
        raise ConfigError("direct configuration contains an unsupported setting")
    version = value.get("schema_version")
    if isinstance(version, bool) or version != 1 or not isinstance(version, int):
        raise ConfigError("unsupported direct configuration schema version")
    desktop = _parse_desktop(value.get("desktop", {}))
    environments_value = value.get("environments", [])
    if not isinstance(environments_value, list) or len(environments_value) > MAX_ENVIRONMENTS:
        raise ConfigError("environments must be a bounded array of tables")
    environments: list[EnvironmentDeclaration] = []
    issues: list[DeclarationIssue] = []
    for index, item in enumerate(environments_value):
        provider = item.get("provider") if isinstance(item, Mapping) else None
        home = item.get("home") if isinstance(item, Mapping) else None
        try:
            environments.append(_parse_declaration(item, index))
        except ConfigError as error:
            safe_provider = provider if provider in SUPPORTED_PROVIDERS else None
            safe_home = home if isinstance(home, str) and len(home) <= MAX_PATH_LENGTH else None
            issues.append(
                DeclarationIssue(index, "invalid_declaration", str(error), safe_provider, safe_home)
            )
    return DirectConfig(desktop, tuple(environments), tuple(issues))


def load_direct_config(
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    user_home: Path | str | None = None,
) -> DirectConfig:
    """Read only the new direct.toml; an absent file means native defaults."""

    config_path = (
        Path(path) if path is not None else default_config_path(env=env, user_home=user_home)
    )
    try:
        descriptor = os.open(config_path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ConfigError("direct configuration is not a regular file")
            raw = stream.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return DirectConfig(DesktopConfig(), ())
    except OSError as error:
        raise ConfigError("direct configuration cannot be read") from error
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError("direct configuration exceeds its byte limit")
    try:
        text = raw.decode("utf-8", "strict")
        document: Any = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError("direct configuration is not valid UTF-8 TOML") from error
    return parse_direct_config(document)
