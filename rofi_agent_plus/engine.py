"""Shared version and bounded picker errors."""

from __future__ import annotations

VERSION = "0.15.0a9"


class PickerError(RuntimeError):
    """An expected failure that can be reported without a traceback."""
