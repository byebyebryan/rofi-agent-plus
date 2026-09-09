# Rofi Agent Plus

`rofi-agent-plus` is a standalone Rofi script-mode picker for Codex CLI,
Claude Code, and OpenCode sessions. It owns provider-native discovery,
correlation, and presentation while the Rofi Plus suite owns hosts and tmux
lifecycle.

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

Version `0.3.0` supports Python 3.11+ and has no runtime package dependencies.
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
  -kb-custom-6 Escape \
  -kb-cancel Control+g \
  -kb-move-char-forward Control+f -kb-move-char-back Control+b \
  -eh 2
```

The normal Rofi invocation is configured as the `agent-plus` script mode.
`Mod+A` or a similar Niri binding can invoke it with `rofi -show agent-plus`.
The picker opens
in `Agents › Recent`, a mixed newest-first list.  Left and Right cycle the
top-level `Recent`, `Hosts`, and `Providers` views; `Enter` enters a
host/provider group or opens a session.  `Escape` returns to a view root when
nested and exits from a root; `Ctrl+G` always exits.  `Tab` and `Shift+Tab`
use Rofi's normal next/previous row navigation.  Navigation transitions clear
the filter and selection.  `Alt+R` performs a bounded foreground refresh.
Custom input and deletion remain disabled.  Rofi must be launched with `-eh 2`
so each list element reserves height for both display lines.

The Rofi callback boundary fails closed: configuration, model, and callback
errors become bounded notices, while root Escape still returns no rows even
when setup fails. If a nested Escape callback cannot reload its model, it
renders the enclosing root with a safe error notice so the next Escape closes
the dialog. Ctrl+G remains Rofi's native unconditional cancel binding and is
never handled as a script callback.

The `Hosts` view groups sessions by their logical displayed host and orders
hosts by the newest session they contain.  The `Providers` view uses the
stable Codex, Claude Code, and OpenCode order and omits empty providers.
Group rows show the session count, any active count, and newest age.  Their
metadata is typed JSON, so opening a session never depends on parsing visible
text.  A trailing `›` and host/provider icon make drill-down groups easy to
recognize; groups containing active sessions receive the same active styling as
session rows.  Breadcrumb prompts identify the current root or nested group,
for example `Agents › Hosts › host-a` or
`Agents › Providers › Codex`.

Rows use a two-line layout: the session title is primary, while a smaller
secondary line shows the display host, shortened working directory, age, and
active/idle state.  The provider is represented by a bundled icon; provider
names and aliases remain in the row's filter text and invisible Rofi metadata,
so searching for `codex`, `claude`, `Claude Code`, or `opencode` still works.
The complete session identity is carried in Rofi's `info` metadata, not parsed
from visible text.  Active rows are marked with Rofi's active-row metadata.  A
selection uses Tmux Plus to focus or launch the terminal, or to create a
deferred provider-resume wrapper with typed provider options. Icon provenance
and trademark notes are in [`ASSETS.md`](ASSETS.md).

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
renders immediately.  A stale cache renders immediately with a short
`Refreshing in background` message and starts at most one detached refresh.
While that worker is running, the open dialog polls the marker about once per
second and replaces the cached rows as soon as the fresh snapshot is written;
the status then clears and polling stops.  A failed or stalled worker also
stops polling and clears the transient status while leaving the cached rows
usable.  Current refresh/provider errors are shown for about three seconds and
then cleared automatically; the rows remain available throughout.  The new
result is also visible the next time the picker opens or after `Alt+R`.
Per-host snapshots and rows from failed provider stages are retained while a
host is unavailable, and current errors are summarized in the message area.
The detached-refresh marker is scoped to the cache fingerprint and backend
authority; an old owner cannot suppress or overwrite a newer Mesh refresh.
There is intentionally no resident process or push-update channel.

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
