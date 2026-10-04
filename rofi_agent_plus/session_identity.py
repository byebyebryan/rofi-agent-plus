"""Provider logical identity is independent of viewer and runtime handles."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from . import engine

# Keep the first three positions compatible with provider/UUID checks while
# extending every equality/deduplication key with namespace and native kind.
SessionIdentity = tuple[str, str, str, str, str]
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z", re.ASCII)
_KINDS = {"codex": "thread", "claude": "session"}


def logical_identity(row: Mapping[str, object]) -> SessionIdentity | None:
    host, provider, identifier = row.get("hostId"), row.get("kind"), row.get("id")
    namespace, native_kind = row.get("namespace"), row.get("nativeIdKind")
    if (
        not isinstance(host, str)
        or not _HOST.fullmatch(host)
        or not isinstance(provider, str)
        or provider not in _KINDS
        or not isinstance(identifier, str)
        or not engine.UUID_PATTERN.fullmatch(identifier)
        or not isinstance(namespace, str)
        or not 0 < len(namespace) <= 4096
        or any(unicodedata.category(c).startswith("C") for c in namespace)
        or native_kind != _KINDS[provider]
    ):
        return None
    return host.casefold(), provider, identifier.lower(), namespace, native_kind
