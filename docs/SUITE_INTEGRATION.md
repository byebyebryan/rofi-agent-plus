# Agent Plus suite ownership after the native-runtime migration

This document describes the `0.13.0a3` migration candidate. Earlier P6/P9/P10
acceptance records apply to their foreground-runtime versions. Native discovery,
source installation and live attachment acceptance are tracked separately in
[Agent Observer's execution ledger](../../agent-observer/docs/execution-status.md).

## Boundaries

| Responsibility | Owner |
| --- | --- |
| Host identity, display order, routes and SSH policy | SSH Plus / Host Mesh v1 |
| Passive provider identity, history, work, presence and source coverage | Agent Observer, one independent installation per host |
| Native daemon, worker and logical session lifecycle | Codex / Claude Code |
| Picker presentation, cache, preferences and fresh action validation | Agent Plus |
| Tmux sessions, deferred terminal clients and endpoint-local viewers | Tmux Plus / Tmux Session v1 |

Agent Plus consumes commands and bounded JSON. There are no cross-package
Python imports, shared virtual environments, static remote host lists or
provider-private file readers in the consumer. Vendored released Host Mesh v1
and Tmux Session v1 bundles retain their existing manifests and provenance.
The Observer snapshot is a prerelease schema-v1 contract independently parsed
by the consumer; an unsupported version fails visibly.

## Discovery

For each selected Host Mesh host, invoke exactly one public
`agent-observer snapshot --host-scope HOST_ID`, with repeated `--provider`
arguments for that host's configured providers. The backend runs local calls or
existing marked SSH routes under the shared refresh deadline. Missing Observer,
unsupported provider artifacts, incomplete identity or failed observation never
trigger provider CLI discovery or daemon auto-start. Source and per-provider
coverage stay explicit; a healthy provider survives another provider's failure.

Every native key contains host, provider, namespace, native ID kind and native
ID. Server-loaded presence is not OS worker presence or terminal attachment.
History recency, work evidence, presence and attachment have independent clocks.
Provider cwd, title, PID, file time and historical tmux options cannot join an
unknown identity. OpenCode is removed from the current provider surface.
Confirmed Codex child sessions are excluded from the picker. Claude saved
history comes through Observer's pinned SDK metadata helper; it does not add a
legacy transcript collector to Agent Plus.

## Actions

An explicit user action refreshes the selected host, revalidates the exact same
identity and requires the accepted native source/artifact. Codex uses its native
session ID for `codex resume --all`; Claude uses the exact short background job
ID for `claude attach`. Native configuration preserves Observer's
default or explicit context: default Claude entry clears `CLAUDE_CONFIG_DIR`,
which otherwise relocates native preferences even at the default directory.
Explicit roots use the exact provider variable. Tmux Plus receives typed argv through public
`create --defer-until-attached --open` and owns safe routing and terminal launch.
A collision selects a bounded new wrapper name; no unrelated wrapper is adopted.

Claude saved history has a separate fresh Resume route:
`claude --resume FULL_UUID --bg` with closed stdin, followed by the exact native
attach cue and public Observer identity. If Claude creates a copy, the viewer
attaches to the actual returned job/session. An uncertain command, transport or
post-action validation result never dispatches another resume.

Current client-to-conversation binding remains unsupported. A native Resume
creates a fresh attachment and never treats a historical provider option as
permission to focus or close an existing viewer. Native rows cannot enter
conversation-specific Open/Close or group viewer actions. Generic Tmux Plus
viewer management remains independently available.

New requires an unchanged provider-reported absolute directory on the exact
host and namespace. Codex starts an individual native TUI. Claude supervised
creation uses an empty `claude --bg` and then validates its new exact job via
Observer before attach. Stdin is closed, and a per-launch isolation overlay
keeps the exact selected directory. Creation is an explicit consumer action; no observation
path invokes it. A timeout or ambiguous post-action result cannot retry creation.
If attachment fails after creation, the native session is preserved and the
failure is reported. A bare foreground Claude TUI is not a supervised worker.

## Configuration and delivery

Agent Plus accepts `max_sessions`, `refresh_seconds` and the optional
`[host_providers]` table. Provider selection can disable a provider on an
existing host; it cannot define hosts or routes. Host Mesh owns hosts, aliases
and SSH policy; Tmux Plus owns terminal argv. Malformed present
contracts fail closed. An absent SSH Plus retains the shared local-only identity;
an absent Tmux Plus is a visible failure. Refreshes do not increment SSH usage
history.

Each package has its own installation and public entry point. The managed
pilot records exact artifact hashes and scoped chezmoi changes. Source tests,
installed module equality, native provider work, ordinary hook execution,
cross-host attach, visual/focus behavior and physical RLCD acceptance are
separate evidence categories. No earlier suite acceptance silently certifies
the new runtime boundary.
