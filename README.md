# Rofi Agent Plus

`rofi-agent-plus` is a standalone Rofi script-mode picker for Codex CLI,
Claude Code, and OpenCode sessions. It owns provider-native discovery,
correlation, and presentation while the Rofi Plus suite owns hosts and tmux
lifecycle.

The repository contains the published P9 consumer implementation: it vendors
and independently validates exact released Host Mesh v1 and Tmux Session v1
bundles through their public process contracts. Managed suite deployment is
coordinated through chezmoi.

The repository, executable, Python package, Rofi mode, configuration, and cache
use the `rofi-agent-plus` name. `rofi-tmux-plus` is mandatory and supplies
live generic tmux inventory plus all open/create terminal lifecycle. When
`rofi-ssh-plus` is present, Agent Plus consumes its Host Mesh v1. When it is
absent, Agent Plus uses the suite's local-only identity and still discovers and
resumes local provider sessions through Tmux Plus. A present malformed or
unsupported companion contract is a visible failure, never a fallback. Agent
Plus revalidates a typed provider row and calls the public Tmux Session v1
`open` or `create` command; it never guesses an SSH route, Niri window,
terminal argv, or raw tmux target. Rename and kill remain Tmux Plus management
actions, not Agent Plus actions.

Version `0.6.0` supports Python 3.11+ and has no runtime package dependencies.
The core contract requires Python 3, the Codex CLI, and `rofi-tmux-plus` on
`PATH`. Claude Code and OpenCode are optional provider tools. Remote hosts
also require `rofi-ssh-plus` Host Mesh, tmux, and the provider tools they
expose; they do not need this repository installed.

## Install and run

The executable can be run directly from a checkout:

```sh
./bin/rofi-agent-plus list | jq
rofi -show agent-plus -modes "agent-plus:$(pwd)/bin/rofi-agent-plus" \
  -kb-custom-1 Alt+r -kb-custom-2 Right -kb-custom-3 Left \
  -kb-custom-7 Tab -kb-custom-8 ISO_Left_Tab \
  -kb-element-next "" -kb-element-prev "" \
  -kb-accept-custom "" -kb-delete-entry "" \
  -kb-cancel Escape,Control+g \
  -kb-move-char-forward Control+f -kb-move-char-back Control+b \
  -eh 2
```

The normal Rofi invocation is configured as the `agent-plus` script mode.
`Mod+A` or a similar Niri binding can invoke it with `rofi -show agent-plus`.
The picker opens in `Agents › All`, a mixed newest-first list, with `Resume`
as its action. Up and Down move through rows. `Tab` switches the action from
`Resume` to `New session here`; `Shift+Tab` cycles in reverse, with wraparound.
The prompt and message area always show the selected action. Enter applies that
action to the row highlighted at Enter. Left and Right cycle `All`, `Local`,
and the remote hosts in stable Host Mesh order. Escape and `Ctrl+G` use Rofi's
native cancel path and always close. View changes preserve the filter and reset
the selection. `Alt+R` starts a background refresh while retaining the current
rows, filter, selection, and action. Custom input and deletion remain disabled. Rofi
must be launched with `-eh 2` so each list element reserves height for both
display lines.

The Rofi callback boundary fails closed: configuration, model, and callback
errors become bounded notices. Left and Right read only the cached snapshot;
they do not prepare Host Mesh or provider clients. Empty or unavailable hosts
remain in the view ring as non-actionable status rows, and a local-only Mesh
collapses the redundant `All` view into `Local`.

The host catalog is authoritative and ordered by Host Mesh, while provider
groups are not navigation scopes. Provider icons remain visible, and provider
names and aliases remain in filter metadata, so searching for `codex`,
`claude`, `Claude Code`, or `opencode` still works. Every selectable row is a
leaf session with typed JSON metadata; forged group metadata is rejected.

Rows use a two-line layout: the session title is primary, while a smaller
secondary line shows the display host, shortened working directory, age, and
active/idle state.  The provider is represented by a bundled icon; provider
names and aliases remain in the row's filter text and invisible Rofi metadata,
so searching for `codex`, `claude`, `Claude Code`, or `opencode` still works.
The complete session identity is carried in Rofi's `info` metadata, not parsed
from visible text.  Session recency and activity state are independent from
observation confidence.  Rows with current provider and supporting evidence
are ordinary; while an automatic stale-cache refresh or explicit `Alt+R` check
is running, current rows show `Checking` and retained rows show `Rechecking ·
last seen …`.  After a check, retained provider rows show `Last known · seen
…`, activity-only rows show `Activity seen · details unavailable`, and rows
with current provider data but secondary activity or Tmux failures show
`Details limited`.  Active-row styling
is emitted only when current activity evidence supports it. The `Resume`
action uses Tmux Plus to focus or launch the terminal, or to create a deferred
provider-resume wrapper with typed provider options. Icon provenance and
trademark notes are in [`ASSETS.md`](ASSETS.md).

`New session here` is an independent lifecycle. It refreshes the selected
logical host through the current Host Mesh authority and requires one exact,
current provider row with the same provider, ID, host, and provider-reported
absolute working directory. It checks that directory and the bare provider
executable on the target host before calling public `rofi-tmux-plus create`.
The create request uses the exact directory, a sanitized
`<cwd-basename>-<provider>` name with a bounded numeric collision suffix,
`--defer-until-attached --open -- <provider>`, and the current Mesh revision
when present. It carries no provider resume ID or provider options. A failed
New attempt returns the picker to `Resume`; the selected native provider
session and its existing tmux session remain untouched.

When discovery correlated a session through its exact provider tmux option,
the row carries a private `providerOptionVerified` marker. Selecting that row
can use Tmux Plus's guarded `open --require-option` operation directly, which
rechecks the complete stable reference, expected name when present, Mesh
revision, and provider option before focusing or launching. Typed pre-action
errors (`stale_session`, `session_not_found`, `stale_mesh`, or compatibility
`invalid_input`) fall back once to the normal selected-host refresh. Ambiguous
or potentially post-action failures never retry. Process-only, retained
without proof, and create-path rows keep the full revalidation path.
Local-only rows with a `null` Mesh revision also stay on full revalidation:
omitting `--mesh-revision` is an unpinned Tmux request, not a local-only
assertion.

## Configuration

Configuration is optional and lives at
`$XDG_CONFIG_HOME/rofi-agent-plus/config.toml`, or
`~/.config/rofi-agent-plus/config.toml`.  The accepted keys and an example
are in [`examples/config.toml`](examples/config.toml):

```toml
max_sessions = 40
refresh_seconds = 30
```

Agent Plus accepts only provider-owned `max_sessions` and `refresh_seconds`.
Host routes, aliases, SSH policy, and terminal settings belong to SSH Plus or
Tmux Plus and are rejected here. Malformed TOML, unknown keys, wrong types,
and out-of-range values are reported visibly in Rofi. The diagnostic `list`
command may temporarily override only `max_sessions` with `--limit`.

## Cache visibility

The picker stores a private, versioned snapshot under
`$XDG_CACHE_HOME/rofi-agent-plus/`, or `~/.cache/rofi-agent-plus/`.  The
directory is mode 0700 and cache/lock files are mode 0600.  Writes use a
temporary file, fsync, and atomic replacement. The snapshot fingerprint
includes the provider session limit. Snapshots also carry contract identity and
the exact Host Mesh revision (or the explicit local-only `null` revision), so
data from a changed authority is never rendered as current. A
discovery-affecting configuration or authority change causes a synchronous
refresh.

On a cache miss, the first invocation refreshes synchronously.  A fresh cache
renders immediately.  Cache age triggers a check but never, by itself, makes a
row warning or `Last known`.  A stale cache renders immediately with a short
`Checking sessions…` message and starts at most one detached refresh; explicit
`Alt+R` starts the same background check even when the cache is fresh.  While
that worker is running, the open dialog polls its private marker about once per
second.  Rows continue to come from the committed snapshot and publish
together when the complete authority-scoped transaction is written; there is
no resident process, push-update channel, or progressive row publication.
Successful completion shows a bounded `Checked just now` acknowledgement,
then polling stops and the acknowledgement clears.  A failed, stopped, or
stalled worker stops polling and shows a visible check failure while leaving
usable last-known rows in place.  Current refresh/provider errors are shown
for about three seconds and then cleared automatically; the rows remain
available throughout.

The private v4 cache accepts a valid v3 snapshot in memory without rewriting it
on read or causing a cache miss; a later legitimate cache mutation persists
the v4 form.  Per-host snapshots and rows from failed provider stages are
retained while a host is unavailable, and current errors are summarized in the
message area.  The v4 observation fields are presentation provenance only:
they do not authorize selection or lifecycle actions.  The detached-refresh
marker is scoped to the cache fingerprint and backend authority; an old owner
cannot suppress or overwrite a newer Mesh refresh.

## Diagnostic CLI

The same executable has a JSON CLI when called without `ROFI_RETV`:

```sh
./bin/rofi-agent-plus list --limit 40
./bin/rofi-agent-plus active
./bin/rofi-agent-plus refresh
```

`list` and `refresh` exercise the selected public contract backend. `active`
is a provider-process diagnostic only: it does not inspect tmux or open a
session. Direct `open`, provider-specific open, host/route/alias, no-local,
SSH-policy, and terminal CLI options were retired in 0.3.0. Existing tmux
option spellings remain correlation inputs, including `@codex_thread_id`,
`@claude_session_id`, `@opencode_session_id`, and `@agent_picker_waiting`.
OpenCode discovery keeps the root-only `parent_id IS NULL` filter and
all-project scope.

## Deployment and ownership

This repository is the canonical implementation of Agent Plus. A coordinated
Chezmoi deployment pins its release, installs the public command and Rofi mode,
and keeps only provider-owned Agent Plus configuration. DMS remains responsible
for the bar, notifications, idle handling, lock screen, polkit, and the general
Spotlight launcher.

The former DMS Agent Picker repository is retained for compatibility and
history, but is deprecated; new picker behavior belongs here.  Project
scoping and synthetic-session cleanup remain out of scope until they have a
separately reviewed design.
