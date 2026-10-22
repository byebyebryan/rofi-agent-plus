# Rofi Agent Plus suite ownership and acceptance

This source candidate implements the direct environment/window picker described
in the [design](agent-plus-daemon-tui-design.md),
[design review](agent-plus-daemon-tui-design-review.md), and
[implementation plan](agent-plus-daemon-tui-implementation-plan.md). Those
documents preserve the design record and proof requirements; candidate source,
wheel and native results are recorded separately in the
[candidate execution report](direct-tui-candidate-20261021.md).

## Ownership and runtime boundary

| Responsibility | Owner |
| --- | --- |
| Logical host IDs, aliases, routes, SSH executable and connection policy | SSH Plus public Host Mesh |
| Environment targets, labels, capabilities, passive daemon observations and native manager entry | Agent Plus headless host helper and provider adapters |
| Local list, cache, selection hints, launch requests, window registration and exact focus | Agent Plus desktop picker/actions |
| Conversations, agent work, native settings and authentication | Codex and Claude Code |
| Supported desktop and terminal surface | Niri compositor with one dedicated Kitty window per registered TUI |

The host helper works without Rofi, Niri, Kitty, Agent Observer or Tmux Plus.
The desktop uses the public `rofi-ssh-plus` Mesh output and fixed
`rofi-agent-plus-host` operations on each host. Remote hosts must have the same
Agent Plus artifact installed with the host helper visible in noninteractive
`PATH`. A configured Mesh failure is a host error; it cannot be replaced by an
OS hostname. The local-only identity fallback is used only when the SSH Plus
CLI is absent.

The direct artifact has no runtime Python dependencies. It does not import
Observer collectors or Tmux lifecycle code. Inventory reads provider
capabilities and daemon status without starting daemons, authenticating,
creating homes, enumerating conversations or executing provider commands from
inventory fields. Codex and Claude Code own their native manager and work
lifecycle. Agent Plus owns only its exact foreground client/terminal/window
registrations; it never kills a daemon or worker descendant.

## Identity, discovery and transport

A target's canonical identity is logical host ID, effective UID/account,
provider and native home/configuration directory. Aliases and labels do not
create new targets. The specification revision hashes launch-relevant adapter,
executable and environment selection; it excludes labels, daemon status,
observation timestamps, daemon PID/socket and ordinary provider settings.
Entry references contain expected host/user, canonical target key and
specification revision. The owning helper resolves its own identity and target
again immediately before native exec.

Each host discovers the effective account's passwd-home `~/.codex` and
`~/.claude` defaults, regardless of inherited `CODEX_HOME` or
`CLAUDE_CONFIG_DIR`. Extra homes are explicit host-local declarations in
`direct.toml`; default and declared aliases collapse by canonical identity. An
additional Codex home requires a readable `config.toml`; an additional Claude
configuration directory must already exist and be usable. A missing default
may remain available for first-use setup on explicit Open. Discovery does not
create homes. On entry, Codex sets `CODEX_HOME` and clears `CODEX_PLUS_SOCKET`;
Claude sets `CLAUDE_CONFIG_DIR`. Entry uses the owning user's home as cwd and
executes the installed `codex agents` or `claude agents` CLI in the real
foreground TTY.

Capability and daemon observations are independent. Provider capability help
is skipped for an uninstalled, undeclared provider. That normal absence has no
picker row or host-partial warning; missing coverage remains in the diagnostic
wire so cached targets can be withdrawn. Explicit declarations for missing
providers still report the configuration problem. Provider capability help
is cached by executable identity and adapter revision; status is a passive
per-environment query. Only recognized positive running/stopped evidence is
normalized. A nonzero status exit alone is unknown, and daemon status being
unknown does not block fresh validated Open. Partial provider/host errors retain
valid sibling targets and last-known targets with their original observation
times. A complete current inventory can remove absent targets. An unreachable
host is not an empty or stopped host.

Passive remote inventory uses current Host Mesh routes, fixed shell-quoted
helper arguments, BatchMode and strict host-key checking. It has bounded route
fallback and writes no SSH history. Interactive entry selects one route from a
fresh Mesh view, sends one bounded reference over a TTY and permits native SSH
authentication. Once entry dispatch begins, an uncertain result is never
retried through another route. A window proves local presence, not successful
remote login or provider connectivity.

## Window model and picker behavior

The list spans current Mesh hosts in Mesh order, with targets ordered by
provider and canonical home. Each target with no registered TUI has one
selectable launcher row. Each target with registered TUIs has one selectable
row per instance, adjacent in stable ordinal order, and no launcher row. A
title is sanitized display/search text only; it is never an identity or an
action input. Host/provider status lines are nonselectable. Registered local
windows remain visible when their source host is offline or removed.

The initial supported desktop is Niri plus dedicated Kitty. Every launch has a
random instance ID, desktop generation, expected process-birth handles and a
launch marker; its Kitty control socket is private to that instance. Before
focus, Agent Plus freshly validates the compositor generation, process
identity, exact window marker and single Kitty OS-window/tab/pane foreground
layout. Ambiguity or unavailable compositor evidence refuses focus. A title or
reused window ID cannot retarget an action.

Open on an instance revalidates and focuses that exact instance without a host
or provider probe. If the row is stale, it reports and refreshes; it never
launches as a fallback. Open on a target validates a fresh EntryReference and
dispatches one TUI. Tab selects **New window**, which creates a
separate native manager client with its own terminal state. One callback replay
uses one request ID and resolves to the same instance; a deliberate new action
gets a new ID. Dispatch intent is durable before spawning. Uncertain dispatch
is reconciled without automatic retry. Normal terminal controls close windows;
the picker has no Close, batch or conversation action.

| Key | Behavior |
| --- | --- |
| Up / Down | Select a row |
| Enter | Open a target or focus the exact selected window |
| Tab / Shift+Tab | Cycle Open / New window |
| Alt+R | Refresh the bounded catalog before redraw |
| Left / Right, Ctrl+B / Ctrl+F | Edit the search text using native Rofi cursor bindings |
| Escape / Ctrl+G | Cancel and close the picker |

Search includes host, provider, environment label and current title. Rows escape
markup/control characters. Callback state carries typed target/instance IDs,
not row text or search input. Redraw keeps the filter, action and selected
identity when possible. Each new dialog opens with empty search and Open
selected, restoring the last successful instance/target if still present.

The first frame uses the launcher's supported `-selected-row` option and renders
the saved catalog and current local windows immediately. Missing or stale
catalog data starts one dialog-owned background refresh. Rofi reads published
results only while that refresh is pending, preserving search, selected
identity and action. Refresh progress is quiet whenever a selectable row is
available; failures remain visible. Checks stop at completion or timeout.
Alt+R refreshes explicitly, or joins the pending startup refresh.

The fleet deadline is 20 seconds, each host is bounded to 12 seconds, and at
most four host workers run concurrently. Host results publish and redraw as
they complete. Ordinary redraws read only local Niri, registry/process and
cache state; no watcher remains after discovery. Closing the dialog cancels
unfinished passive probes, while accepted native TUI windows outlive it.
Mosh, Ghostty and manual TUI adoption remain unsupported.

## Configuration and private state

The only active settings file is
`$XDG_CONFIG_HOME/rofi-agent-plus/direct.toml` (default
`~/.config/rofi-agent-plus/direct.toml`). Schema version 1 accepts
`[desktop]` with a bounded Kitty argument vector and `catalog_ttl_seconds`, and
up to 128 `[[environments]]` declarations with `provider`, `home`, optional
`label` and optional alias `id`. Unknown settings and schema versions are
rejected. The supported catalog TTL is 1–300 seconds, default 30. See
[`examples/direct.toml`](../examples/direct.toml).

New state is isolated from the retired conversation runtime:

| Purpose | Location |
| --- | --- |
| Target catalog | `$XDG_CACHE_HOME/rofi-agent-plus/direct-v1/catalog.json` |
| Last successful target/instance hint | `$XDG_STATE_HOME/rofi-agent-plus/direct-v1/view.json` |
| Desktop registry and per-target active/tombstone records | `$XDG_RUNTIME_DIR/rofi-agent-plus/direct-v1/<desktop-generation>/` |
| Per-instance short Kitty control socket | `$XDG_RUNTIME_DIR/rofi-agent-plus-sockets/<instance-id>.sock` |

The cache is display/performance state, never action authority. Catalog refresh
uses a private file lock and atomic publication; per-host success, partial and
failure results update independently. Registry transitions hold a short
per-target lock and perform no provider or SSH probes under it. Terminal
request tombstones preserve replay identity without consuming the 512-active-
instance per-target cap. Bad catalog data cannot erase a valid local window
record.

## Resource and wire bounds

| Work or record | Bound |
| --- | --- |
| Native provider capability/status probe | 3 seconds each; capability cache TTL 300 seconds |
| One host / entire fleet inventory | 12 / 20 seconds; at most 4 concurrent host workers |
| SSH | Current Host Mesh connect timeout and attempts; remote inventory output 1 MiB and stderr 64 KiB |
| Host inventory wire | 1 MiB; at most 128 targets, 2 provider-coverage records and 256 diagnostics; 16 KiB string fields |
| Entry reference | 64 KiB |
| Cached catalog | 8 MiB and at most 256 host records |
| Config file / declarations | 256 KiB / 128 entries; selector path 4096 characters; label 256 characters |
| Active runtime records | 512 per target; request and tombstone lookup is exact and bounded |
| Rofi callback state / initial frame | 16 KiB / 16 MiB |
| Catalog refresh / compositor request | 20 seconds / 1 second; startup refresh is asynchronous, Alt+R waits unless joining startup |

These bounds limit work and parsing; an interactive native manager or SSH
authentication prompt is intentionally not given an inventory timeout.

## Replacement coverage for retired responsibilities

The former conversation picker, Observer write client and Tmux viewer runtime
are retired from this artifact. The remaining concern is either obsolete for a
direct environment picker or has direct-window coverage:

| Retired responsibility | Current disposition and coverage |
| --- | --- |
| Conversation/session identity, saved-history selection and work-phase display | Obsolete. Native provider managers own conversations; `test_direct_host.py` covers canonical target/spec references and `test_direct_ui.py` covers target/window rows. |
| Observer snapshot, provider-private readers, native action plans and write execution | Obsolete. `host.py` exposes passive capability/status inventory plus fresh native entry; `test_direct_host.py` and `test_direct_transport.py` cover host, self-identity, validation, route and shell-quoting boundaries. |
| Observer/session runtime identity and conversation-to-window association | Replaced by `TargetKey`, `EntryReference`, random instance ID, desktop generation and process-birth/window validation; `test_direct_host.py`, `test_direct_desktop_registry.py` and `test_direct_actions.py` cover these identities and stale/replay cases. Conversation binding remains unsupported. |
| Tmux ownership, deferred attach, viewer launch and cleanup | Obsolete. `desktop.py`, `viewer.py`, `actions.py` and `registry.py` own one direct Kitty client and retire it when its foreground provider client exits. `test_direct_desktop_registry.py` and `test_direct_actions.py` cover lifetime, dispatch gaps and exact focus. |
| Batch open/resume/close and batch workers | Obsolete. Each explicit New window is an independent user request; no batch command or worker remains. Rofi dispatch/replay and row behavior are covered in `test_direct_ui.py` and `test_direct_actions.py`. |
| Old cache schemas, Viewer preferences and asynchronous session refresh workers | Not migrated. New bounded direct-v1 cache/preferences and finite dialog-owned refresh are covered by `test_direct_ui.py`; `test_direct_bounds.py` covers oversized, timed-out and descendant-held process output. |

`test_direct_host.py`, `test_direct_transport.py`,
`test_direct_desktop_registry.py`, `test_direct_actions.py`,
`test_direct_bounds.py` and `test_direct_ui.py` are the active focused suites.
Old Observer/Tmux/session/batch tests and modules are intentionally not kept as
a compatibility runtime or source gate.

## Source, wheel and per-case native gates

| Gate | Driver | What it can establish |
| --- | --- | --- |
| Source | `./scripts/check` | Unit tests, Ruff, shell/text guards and released Host Mesh bundle validation; no native acceptance |
| Public bundle sync | `./scripts/check-contract-sync /absolute/path/to/rofi-ssh-plus` | Exact public Host Mesh bundle/source alignment |
| Installed wheel | `scripts/check-installed-artifact --candidate-prefix PREFIX --wheel WHEEL --output PRIVATE_DIR` | Isolated imports, package byte parity, entry points, no retired runtime dependency and controlled headless inventory |
| Niri/Kitty mechanics | `candidate-prefix/bin/python -I -B scripts/direct-window-proof run PRIVATE_DIR` | Marked dedicated windows, real foreground TTY, exact title/focus identity, private Kitty layout refusal, interrupt and independent client lifetime |
| Installed Rofi callback | `candidate-prefix/bin/python -I -B scripts/direct-rofi-proof PRIVATE_DIR` | Real Rofi first-frame selection, Tab mode changes and filter retention with inert rows, no periodic timer and zero provider actions |
| Provider/host case | `candidate-prefix/bin/python -I -B scripts/check-native-entry ...` | Actual installed artifact launch against one selected logical host/home/provider and its exact native fixture |
| Picker actions | `candidate-prefix/bin/python -I -B scripts/direct-picker-proof --native-root FIXTURE_ROOT --output PRIVATE_DIR` | Actual launcher, Rofi callbacks and Actions create two native windows, focus a selected row and refuse a stale row; controlled local-only Mesh |

The per-case native entry driver accepts `--host`, `--home`, `--provider`,
`--config`, optional remote `--helper`, repeated `--expected`, optional
`--exercise-client-state`, optional `--fixture-script` and
`--disconnect-first`, and a private `--output` directory. Run one explicit
case per fixture. `--config` is resolved on the selected host; do not forward a
desktop-local config path as the remote peer's path. `--helper` must be an
absolute candidate helper path on that remote host. The proof-only injection
does not make inventory-supplied commands trusted. `--exercise-client-state`
checks independent manager state where supported and submits no agent turn.
`--fixture-script` observes the owned daemon on the fixture's host while its
clients are alive. `--disconnect-first` interrupts only the registered SSH
client using a revalidated process handle and checks that entry is not replayed.

The selector shape for an installed case is:

```sh
"$CANDIDATE_PREFIX/bin/python" -I -B scripts/check-native-entry \
  --host "$LOGICAL_HOST" \
  --home "$HOST_NATIVE_HOME" \
  --config "$HOST_DIRECT_TOML" \
  --provider "$PROVIDER" \
  --helper "$HOST_CANDIDATE_PREFIX/bin/rofi-agent-plus-host" \
  --expected "$OWNED_FIXTURE_TEXT_A" \
  --expected "$OWNED_FIXTURE_TEXT_B" \
  --exercise-client-state \
  --fixture-script "$HOST_FIXTURE_SCRIPT" \
  --output "$LOCAL_RUN_ROOT/provider-case"
```

Omit `--helper` for the local host. For a remote case, the config and helper
paths are absolute paths on the named peer, while the output directory is
private to the machine running the proof. On the fixture-owning host, prepare,
observe and clean up the private native home with:

```sh
./scripts/native-fixture prepare "$FIXTURE_ROOT" \
  --provider "$PROVIDER" --label "$LABEL" \
  --helper "$CANDIDATE_PREFIX/bin/rofi-agent-plus-host"
./scripts/native-fixture observe "$FIXTURE_ROOT"
./scripts/native-fixture cleanup "$FIXTURE_ROOT"
```

The required provider/host matrix is local Codex on Starship, Codex default/OSS
and Work homes on Snap, Claude on Snap, and explicit Starship-to-Snap entry
through the candidate helper. Each case checks fresh identity/spec validation,
two clients to one target, additional-request replay, exact focus, independent
exit, cleanup and preservation of ordinary windows. Provider-specific fixture
text and selection/draft/resize checks are recorded per case. A missing provider
or authentication prerequisite is a recorded gap, not a source-test pass.
The private `scripts/native-fixture` helper owns prepare/observe/cleanup for a
case and records exact owned process generations before cleanup.

The actual picker driver is interactive: select the owned local launcher,
reopen with Tab for the additional action, then select the first window row.
In a fourth dialog select that same row, run the driver's `--close-first` mode,
and press Enter. While the stale notice remains open, run
`--confirm-stale-open` before Escape. Both modes use the same `--native-root`
and `--output`. The confirmation validates the exact live Rofi process and sole
remaining native client. These proofs temporarily take desktop focus and should
run only in an agreed idle/test window.

The candidate report records the actual artifact digest, host/helper paths,
per-case receipts, cleanup, and remaining gates. Final human keyboard/visual
judgment, managed desktop selection, publication and rollout remain separate.

Historical Observer/Tmux operational proofs and experiment records have been
retired from the current tree. [Legacy retirement](legacy-retirement.md) records
their Git checkpoint and how to retrieve them. They do not qualify this
direct-window implementation. The [immutable release ledger](https://github.com/byebyebryan/dotfiles/blob/5ff8b412a45b69cac64b8eb356444e6cf0705012/docs/rofi-plus-status.md#agent-plus-121-viewer-timer-across-view-changes)
is historical as well.
