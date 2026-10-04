"""Shared picker constants and bounded expected errors.

Provider discovery and monitoring belong to the public Observer command.
"""

from __future__ import annotations

import re

DEFAULT_LIMIT = 40
DEFAULT_TIMEOUT = 4.0
VERSION = "0.14.0a1"
UUID_PATTERN = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


class PickerError(RuntimeError):
    """An expected failure that can be reported without a traceback."""
