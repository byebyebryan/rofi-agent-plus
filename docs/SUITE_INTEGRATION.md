# Agent Plus suite ownership after the native-runtime migration

This document describes the `0.14.0a1` Observer v2 consumer candidate. Earlier P6/P9/P10
acceptance records apply to their foreground-runtime versions. Native discovery,
source installation and live attachment acceptance are tracked separately in
[consumer execution record](observer-v2-execution.md).

## Boundaries

| Responsibility | Owner |
| --- | --- |
| Host identity, display order, routes and SSH policy | SSH Plus / Host Mesh v1 |
| Passive provider identity, history, work, presence and source coverage | Agent Observer, one independent installation per host |
| Native daemon, worker and logical session lifecycle | Codex / Claude Code |
| Picker presentation, cache, preferences and fresh action validation | Agent Plus |
| Tmux sessions, deferred terminal clients and endpoint-local viewers | Tmux Plus / Tmux Session v1 |

Agent Plus consumes public commands and bounded JSON, plus the pinned pure
Observer v2 contract validator library. Installations are independent; Plus
imports no collectors or provider action implementation. Released Host Mesh v1
and Tmux Session v1 bundles retain their manifests and provenance. The frozen
Observer corpus records its accepted prerelease artifact and contract versions.

## Discovery

For each Host Mesh host, invoke public `agent-observer snapshot --host-scope
HOST_ID` with that host's configured providers. Existing marked SSH routes and
shared deadlines remain external composition. There are no provider-private
readers or daemon auto-start fallbacks. Independent current facts survive partial
history/runtime feeds; missing rows remain explicitly stale without renewing
phase or activity clocks.

Native keys contain host, provider, store namespace, native ID kind and full ID.
Phase (working/blocked/waiting), runtime, worker, outcome and attachment remain
separate. Default attention order is blocked, waiting, working, unknown, then
actual conversation activity. Unsupported age stays unknown. Configured root,
relative path, Git and project metadata are presentation context, never native
identity. Confirmed child rows of both providers are excluded. OpenCode remains
deferred.

## Actions

An explicit action refreshes the selected owning host and resolves the same full
reference. Plus passes a public request to `agent-observer-write prepare`, then
executes once on the prepared route. Observer owns provider acceptance, native
route selection, config/cwd/binary/runtime validation and result correlation.
The write result separates pending/requested/actual identity and confirmed versus
uncertain effects, including copied Claude history. Plus constructs no provider
argv and parses no native launch cue.

Tmux Plus receives `agent-observer-write enter --plan-json PLAN` through its
public deferred create/open interface. Native TTY entry revalidates the plan.
Typed viewer name collisions can retry the same plan; unknown transport,
post-write stale authority and viewer failure never repeat provider work.
Write-client cleanup preserves provider descendants. New contains no source
session reference; Plus uses the selected row only to choose a fresh directory
context. Observer's standalone New supports zero rows independently.

Current client-to-conversation binding remains unsupported. A fresh native
attachment does not authorize focus/Close from a historical wrapper option.
Conversation-specific Open/Close and group viewer actions remain unavailable;
generic Tmux viewer management is independent. Native permissions/trust remain
provider-owned.

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
