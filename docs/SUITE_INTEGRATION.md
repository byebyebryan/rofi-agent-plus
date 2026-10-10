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

## Owner conformance tooling

`scripts/check` runs this project's source and synthetic guard checks.
`scripts/check-public-contract` runs isolated public CLI probes: SSH owns Host
Mesh; Tmux supplies `--ssh-checkout /absolute/ssh`; Agent supplies that argument
and `--tmux-checkout /absolute/tmux`. Checkouts must be clean. Run with the
project's selected Python environment and declared dependency versions. The
probe uses a private HOME/XDG/PATH, synthetic local-only alpha authority, empty
tmux state and provider stubs. It imports no sibling implementation and inspects
no ordinary provider sessions. Consumer bundle-sync gates retain canonical
producer ancestry, historical/current manifests and vendored provenance.

`scripts/check-installed --self-test` proves owned callback and parser guards.
Explicit `--live` checks installed public behavior with isolated UI state and
semantic SSH history preservation. Tmux's separate `--lifecycle` option owns
uniquely named disposable sessions, exact reference cleanup, stale-name and
collision guards and baseline preservation. Run it only for authorized lifecycle
acceptance; pure relocation does not need it. SSH probes no legacy custom-connect
mutation. Source/synthetic, installed, provider actions, graphical/focus and
physical acceptance remain separate.

## Foreground-picker deployment history

The retired managed ledger tracked the earlier foreground runtime. 0.9.0
introduced batch viewer actions; 0.10.0 shared Resume/Close/New, 0.10.1 added
asynchronous fixed previews, 0.10.2 kept the action bar through compact progress,
and 0.10.3 excluded already satisfied viewers. 0.11.0 joined endpoint viewer
facts; 0.11.1 corrected provider/viewer display, 0.11.2 renewed positive evidence
before expiry, and 0.11.3 made uncertainty suffixes consistent. 0.12.0 added Open
and compact state rows; 0.12.1 kept viewer timers valid across page changes.

Their accepted interaction remains in the batch/navigation/UI design documents;
it is historical acceptance for those artifacts. Native 0.14.0a1 discovery and
write delegation have their own proof and currently unsupported viewer bindings.
[Immutable release ledger](https://github.com/byebyebryan/dotfiles/blob/5ff8b412a45b69cac64b8eb356444e6cf0705012/docs/rofi-plus-status.md#agent-plus-121-viewer-timer-across-view-changes)
retains exact commits, installed/operator results and pending gates. P8 flat peers
and P9 CLI contracts retain [original rollout wording](https://github.com/byebyebryan/dotfiles/blob/5ff8b412a45b69cac64b8eb356444e6cf0705012/docs/rofi-plus-p9-cli-contracts.md);
canonical Host Mesh and Tmux Session contracts remain producer-owned.

## Lessons retained from DMS Agent Picker

The earlier [DMS Agent Picker runtime and launch notes](https://github.com/byebyebryan/dms-agent-picker/blob/09cb232992e62ad5dcef605b272dafd223ab15d3/README.md)
contain three principles that still apply under the current suite ownership:

- Saved conversation metadata, a running provider process, and a tmux
  association are separate evidence. A metadata query's short-lived Codex
  app-server does not prove that an interactive conversation is running.
  Shared daemons and supervised runtimes require their own correlation model;
  the supported runtime and known identity limits remain in
  [Agent Plus retains](#boundaries).
- Provider startup needs the attached terminal for capability and color
  probes. Agent Plus requests `--defer-until-attached` through the public
  create command; Tmux Plus owns the bounded wait and pending-session
  mechanics in [Tmux Session v1](https://github.com/byebyebryan/rofi-tmux-plus/blob/main/docs/TMUX_SESSION_V1.md#create).
  Picker lifetime and terminal ownership are handled by Tmux Plus's
  [process lifecycle](https://github.com/byebyebryan/rofi-tmux-plus/blob/main/docs/DESIGN.md#process-ownership-and-errors).
- Cached history should remain usable during a bounded refresh, while
  current activity requires current observation evidence. The old plugin's
  per-host streaming publication is historical; Agent Plus publishes a
  complete authority-scoped snapshot and keeps observation labels separate
  from operation authority, as described in [P9 locked CLI contracts](#boundaries)
  and [Discovery flow](#discovery).

DMS launcher rows, badges, plugin settings, direct launch plumbing, and old
live observations stay with the retired plugin. The current Rofi interaction,
process contracts, and suite acceptance records govern this implementation.
