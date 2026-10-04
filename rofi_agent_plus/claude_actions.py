"""Strict native cues and identity checks for consumer-owned Claude actions.

This module accepts only the bounded ``claude --bg`` attachment cue and the
matching public Observer projection. It does not read Claude-owned files or
start providers itself.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .observer_contract import ObserverSnapshot

CLAUDE_BACKGROUND_VERSION = "2.1.287"
CLAUDE_BACKGROUND_SHA256 = "3920489a5109cff5786a1a392c25277408ff22bc796d5edb9c16a60e5a1718f0"
MAX_BACKGROUND_STDOUT = 32 * 1024
MAX_BACKGROUND_STDERR = 64 * 1024
MAX_BACKGROUND_LINE = 512

_SGR = re.compile(r"\x1b\[(?:[0-9]{1,3}(?:;[0-9]{1,3}){0,15})?m")
_BACKGROUND_LINE = re.compile(r"backgrounded · ([0-9a-f]{8})(?: .{0,400})?\Z", re.ASCII)
_ATTACH_LINE = re.compile(
    r" {2}claude attach ([0-9a-f]{8}) +open in this terminal *\Z",
    re.ASCII,
)


class ClaudeActionError(ValueError):
    """A bounded native cue or projected identity is invalid or ambiguous."""


def parse_background_job_id(stdout: bytes) -> str:
    """Extract one exact native attach cue from pinned ``claude --bg`` output.

    SGR styling is discarded. Other terminal controls, format drift, multiple
    attach cues, or conflicting short IDs fail closed. Repeated background
    IDs are expected in this CLI's pinned output and are accepted only when
    they all agree with the one attach cue.
    """

    if not isinstance(stdout, bytes) or len(stdout) > MAX_BACKGROUND_STDOUT:
        raise ClaudeActionError("Claude background output exceeded its limit")
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ClaudeActionError("Claude background output is not UTF-8") from error
    text = _SGR.sub("", text)
    if "\r" in text:
        if "\r\n" not in text or text.replace("\r\n", "").find("\r") >= 0:
            raise ClaudeActionError("Claude background output has unsupported controls")
        text = text.replace("\r\n", "\n")
    if any(unicodedata.category(char).startswith("C") and char != "\n" for char in text):
        raise ClaudeActionError("Claude background output has unsupported controls")

    background_ids: set[str] = set()
    attach_ids: list[str] = []
    for line in text.split("\n"):
        if len(line) > MAX_BACKGROUND_LINE:
            raise ClaudeActionError("Claude background output line exceeded its limit")
        if "backgrounded" in line:
            match = _BACKGROUND_LINE.fullmatch(line)
            if match is None:
                raise ClaudeActionError("Claude background output format is unsupported")
            background_ids.add(match.group(1))
        if "claude attach" in line:
            match = _ATTACH_LINE.fullmatch(line)
            if match is None:
                raise ClaudeActionError("Claude background attach cue is unsupported")
            attach_ids.append(match.group(1))

    if len(background_ids) != 1 or len(attach_ids) != 1:
        raise ClaudeActionError("Claude background output has missing or ambiguous identity")
    job_id = attach_ids[0]
    if job_id not in background_ids:
        raise ClaudeActionError("Claude background output identities do not agree")
    return job_id


def workspace_trust_required(stderr: bytes, *, cwd: str) -> bool:
    """Recognize the one exact pinned trust gate without exposing its path."""

    if not isinstance(stderr, bytes) or len(stderr) > MAX_BACKGROUND_STDERR:
        return False
    try:
        text = stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    text = _SGR.sub("", text)
    if text.endswith("\n"):
        text = text[:-1]
    if (
        any(unicodedata.category(char).startswith("C") and char != "\n" for char in text)
        or "\n" in text
    ):
        return False
    expected = (
        "Workspace not trusted. Run `claude` in "
        f"{cwd} once and accept the trust prompt, then retry."
    )
    return text == expected


def background_session(
    snapshot: ObserverSnapshot,
    *,
    host_id: str,
    cwd: str,
    config_home: str,
    config_home_kind: str,
    namespace: str,
    job_id: str,
) -> dict[str, Any] | None:
    """Return one exact current background row, or ``None`` while it settles.

    A short job ID that appears on a mismatched or duplicate row is an
    immediate ambiguity. Rows without that explicit ID are not inferred by
    cwd, title, or recency.
    """

    if snapshot.source_health not in {"current", "partial"}:
        return None
    candidates = [
        row
        for row in snapshot.sessions
        if row.get("kind") == "claude"
        and isinstance(row.get("nativeIds"), Mapping)
        and row["nativeIds"].get("jobId") == job_id
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ClaudeActionError("Claude background identity is ambiguous")

    row = candidates[0]
    identity = row.get("observerIdentity")
    native_ids = row.get("nativeIds")
    source = row.get("observerSource")
    runtime = source.get("runtime") if isinstance(source, Mapping) else None
    presence = row.get("presence")
    job = row.get("job")
    if (
        not isinstance(identity, Mapping)
        or identity.get("hostScope") != host_id
        or identity.get("provider") != "claude"
        or identity.get("namespace") != namespace
        or identity.get("nativeIdKind") != "session"
        or identity.get("nativeId") != row.get("id")
        or row.get("namespace") != namespace
        or row.get("cwd") != cwd
        or row.get("sessionKind") != "bg"
        or not isinstance(native_ids, Mapping)
        or not isinstance(row.get("id"), str)
        or native_ids.get("sessionId") != row.get("id")
        or native_ids.get("jobId") != job_id
        or not isinstance(job, Mapping)
        or job.get("id") != job_id
        or not isinstance(source, Mapping)
        or source.get("schemaVersion") != 1
        or source.get("provider") != "claude"
        or source.get("namespace") != namespace
        or source.get("configHome") != config_home
        or source.get("configHomeKind") != config_home_kind
        or source.get("sourceHealth") != "current"
        or not isinstance(runtime, Mapping)
        or runtime.get("version") != CLAUDE_BACKGROUND_VERSION
        or runtime.get("binarySha256") != CLAUDE_BACKGROUND_SHA256
        or runtime.get("topology") != "private_session_registry_and_job_store"
    ):
        raise ClaudeActionError("Claude background Observer identity did not match creation")

    if (
        not isinstance(presence, Mapping)
        or presence.get("health") != "current"
        or presence.get("value") != "present"
    ):
        return None
    return dict(row)
