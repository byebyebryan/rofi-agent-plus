"""Dialog-local title snapshots; current window identities remain authoritative."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from .desktop import Window
from .registry import RegistryError, atomic_json, read_private_json

TITLES_ENV = "ROFI_AGENT_PLUS_WINDOW_TITLES"
TITLES_NAME = "window-titles.json"
MAX_WINDOWS = 1024
MAX_TITLE = 1024
_MARKER = re.compile(r"org[.]agentplus[.][a-f0-9]{32}\Z", re.ASCII)


def freeze_titles(windows: tuple[Window, ...], environ: Mapping[str, str]) -> tuple[Window, ...]:
    """Sample each dedicated window's title once in a launch-owned directory.

    Animated native titles are presentation, not inventory changes. Keeping
    their display/search text steady prevents background refreshes from
    repeatedly refiltering rows and correcting over the user's navigation.
    Presence, PID, marker and focus still come from the current desktop read.
    """

    name = environ.get(TITLES_ENV)
    if not name:
        return windows
    path = Path(name)
    try:
        parent = path.parent.lstat()
        if (
            not path.is_absolute()
            or path.name != TITLES_NAME
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            return windows
        try:
            path.lstat()
        except FileNotFoundError:
            value = {"schemaVersion": 1, "titles": {}}
        else:
            value = read_private_json(path, limit=8 * 1024 * 1024)
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "titles"}
            or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1
            or not isinstance(value["titles"], dict)
            or len(value["titles"]) > MAX_WINDOWS
            or any(
                not isinstance(key, str)
                or len(key) > 512
                or not isinstance(title, str)
                or len(title) > MAX_TITLE
                for key, title in value["titles"].items()
            )
        ):
            return windows
        titles = value["titles"]
        changed = False
        result = []
        for window in windows:
            if not _MARKER.fullmatch(window.app_id):
                result.append(window)
                continue
            key = json.dumps([window.window_id, window.pid, window.app_id])
            if key not in titles and len(titles) < MAX_WINDOWS:
                titles[key] = window.title[:MAX_TITLE]
                changed = True
            result.append(replace(window, title=titles.get(key, window.title)))
        if changed:
            atomic_json(path, value)
        return tuple(result)
    except (OSError, RegistryError):
        return windows
