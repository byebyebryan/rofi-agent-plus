"""The two native TUI adapters supported by the direct host helper."""

from . import claude, codex

ADAPTERS = {"codex": codex, "claude": claude}
