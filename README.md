# Rofi Agent Plus

Rofi Agent Plus lists Codex and Claude Code environments across the local host
and SSH Plus Host Mesh. It opens or focuses each provider's native terminal
manager. The picker manages environments and local windows; Codex Agent Command
Center and Claude Agent View manage conversations and agent work.

This checkout implements the direct-window runtime: one selectable row per
registered TUI window, with a launcher row when a target has no window. The
[design](docs/agent-plus-daemon-tui-design.md),
[design review](docs/agent-plus-daemon-tui-design-review.md), and
[implementation plan](docs/agent-plus-daemon-tui-implementation-plan.md) record
the product decisions and proof requirements. They are design records, not
candidate acceptance receipts. Source tests, wheel installation, native cases,
human interaction and managed selection remain separate gates; see
[suite ownership and acceptance](docs/SUITE_INTEGRATION.md) and the
[candidate execution report](docs/direct-tui-candidate-20261021.md).

## Runtime boundary

| Component | Responsibility |
| --- | --- |
| Agent Plus | Headless environment inventory, cached flat-list presentation, local window registration/focus, and direct native entry |
| SSH Plus / Host Mesh | Logical host identity, aliases, routes, SSH executable and connection policy |
| Codex and Claude Code | Native manager UI, provider settings, authentication and agent work |
| Niri and Kitty | The initial supported compositor/terminal pair; dedicated Kitty windows are registered and focused through exact process/window identity |

The package has no runtime dependency on Agent Observer or Tmux Plus. It does
not list conversations, inspect provider databases, create agent work, or
terminate provider daemons. Generic Tmux management remains owned by Tmux Plus;
it is not part of this picker path.

The first supported desktop is Niri with dedicated Kitty windows. Each launch
uses a private Kitty control socket and socket-only remote control; a global
Kitty control socket is not enabled. Layout validation refuses ambiguous Kitty
tabs, panes or multiple OS windows before focus. Ghostty, Mosh, manual adoption
of arbitrary terminals, and picker-driven Close are unsupported.

## Run and inspect

Install the package and development checks in the selected Python environment,
then run the source checks and either the direct Rofi launcher or diagnostic:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
./scripts/check

./bin/rofi-agent-plus list --json
./bin/rofi-agent-plus-rofi
```

The source wrappers resolve this checkout from their own paths, independent of
the shell's current directory. `rofi-agent-plus-rofi` starts Rofi script mode;
it does not install a desktop entry or change a system key binding. The managed
desktop integration may bind the wrapper as needed.

`rofi-agent-plus list --json` emits a versioned diagnostic document containing
the catalog envelope and per-host/per-provider freshness and errors, Niri
availability, and registered window identity/title/presence. It performs a
bounded catalog refresh before reading the cached snapshot. A nonzero result
reports a refresh or cache failure; an unavailable peer is not an empty host.
This diagnostic is not launch authority. The old conversation-oriented `active`
and `refresh` commands, session-limit options, batch workers and private
viewer-refresh commands are unsupported; the direct artifact has no
compatibility mode for them.

The host helper is independently usable without Rofi, Kitty, Niri, Observer or
Tmux:

```sh
./bin/rofi-agent-plus-host inventory --json
./bin/rofi-agent-plus-host inventory --json --config /private/path/direct.toml
./bin/rofi-agent-plus-host enter --reference-json "$REFERENCE_JSON"
```

`inventory` emits strict wire version 1 and never starts a provider daemon,
authenticates interactively, creates a home, or opens a native manager. The
optional `--config PATH` is for a host-local configuration or an explicitly
controlled candidate fixture. `enter` accepts an exact bounded entry reference,
requires stdin/stdout/stderr to be a real TTY, freshly validates host, user,
target and launch specification, changes to the owning account's home, then
replaces itself with `codex agents` or `claude agents`. Entry failures are
structured and stable; native prompts remain native.

## Environments and configuration

The optional schema-versioned file is
`$XDG_CONFIG_HOME/rofi-agent-plus/direct.toml`, defaulting to
`~/.config/rofi-agent-plus/direct.toml` under the effective account's passwd
home. [The example](examples/direct.toml) labels the Codex default and
declares a separate Work home:

```toml
schema_version = 1

[desktop]
terminal = ["kitty"]
catalog_ttl_seconds = 30

[[environments]]
provider = "codex"
home = "~/.codex"
label = "Default"

[[environments]]
provider = "codex"
home = "~/.codex-work"
label = "Work"
```

The owning account's `~/.codex` and `~/.claude` defaults are discovered when
their provider CLIs are installed, whether or not those directories exist. Host
inventory ignores inherited `CODEX_HOME` and `CLAUDE_CONFIG_DIR`, so the picker
process's selected profile cannot hide a default or create an undeclared row.
Declarations add extra environments or label an existing canonical home;
aliases deduplicate to the same host/user/provider/home identity.

An additional Codex home must already exist and have a readable native
`config.toml`. An additional Claude configuration directory must already exist
and be usable. A missing default can remain a target for native first-use setup
on explicit Open. Inventory does not create either kind of home or authenticate.
On entry, Codex sets the selected `CODEX_HOME` and clears
`CODEX_PLUS_SOCKET`; Claude sets `CLAUDE_CONFIG_DIR`. Native settings and
authentication remain provider-owned.

Only Codex and Claude Code are supported. `desktop.terminal` is a bounded Kitty
argument vector, not a shell command or terminal plug-in point. The supported
catalog TTL is 1–300 seconds; the default is 30 seconds. Invalid whole-file
configuration is reported rather than interpreted as an empty catalog; an
invalid individual declaration has a bounded provider/environment diagnostic.

Remote hosts use the public SSH Plus Host Mesh identity and routes. Install the
same Agent Plus artifact's `rofi-agent-plus-host` helper on each remote host
under a noninteractive `PATH`. The desktop sends fixed helper operations and
bounded references; inventory cannot provide commands. Passive SSH inventory
uses BatchMode and strict host-key checking, can try current eligible routes,
and does not write route-use history. Explicit entry selects one fresh route,
uses a TTY, permits native SSH authentication, and never retries an uncertain
provider launch. A configured Host Mesh failure cannot fall back to an OS
hostname. If the Host Mesh CLI is absent, the documented local-only identity
fallback applies.

## Picker behavior

The picker shows a flat list across configured hosts. Host Mesh order is kept;
targets sort by provider and canonical home; window rows for one target stay
adjacent in ordinal order. A target with one or more registered windows has one
row per window and no extra launcher row. A target with no registered window
has one launcher row. Provider and host status rows are not selectable.

| Key | Behavior |
| --- | --- |
| Up / Down | Select a row |
| Enter | Open the selected target or focus the exact selected window |
| Tab / Shift+Tab | Cycle between **Open** and **New window** |
| Alt+R | Refresh the bounded host catalog before redraw |
| Left / Right, Ctrl+B / Ctrl+F | Edit the search field using Rofi's native cursor bindings |
| Escape / Ctrl+G | Close the picker |

The action bar lists Open and New window, highlights the active action
in blue and shows Enter/Tab hints, matching SSH Plus and Tmux Plus. Notices appear
below it; quiet refreshes leave the action bar in place.

Search covers host, provider, environment label and current window title. Titles
are sanitized and escaped for display; no title or typed search text enters an
executable action. A title is not conversation identity. A missing/changed
instance on Open produces a stale-selection notice and refresh; it never turns
into a launch of another target. Focusing an existing row checks local Niri,
process-generation and Kitty layout evidence and does not probe SSH or provider
status. New window is the explicit action that creates another native
client for that target. Normal terminal controls close it; the provider daemon
and worker lifetime are independent.

Opening the dialog immediately uses the saved catalog and current local windows.
If the catalog is missing or stale, the launcher starts one background refresh
after launching Rofi. The dialog reads published results while that refresh is
pending, preserving the filter, selected row identity and chosen action. Redraw
checks stop when the refresh finishes or its deadline expires. Refreshing is
silent whenever the list has a selectable target or window; progress appears
only for an empty list, and failures remain visible. Native search retains the
flat host/environment/window order. Window titles are sampled once per open
picker, including their search text; reopening samples them again. Animated
native titles therefore cannot start repeated selection corrections during
discovery. Window presence and action validation still use current identities.
Selection correction runs only when rows
through the selected row change, so later additions need no extra callback.
Once a correction starts, changing titles cannot restart it; another change
to row positions or eligibility can. Discovery checks run at most twice per
second while pending, then stop.
Fresh catalogs need no
background work. Alt+R explicitly refreshes before redrawing, or joins an already
pending startup refresh. The fleet deadline is 20 seconds, with
12 seconds per host and at most four concurrent host workers. Each host result
publishes to the cache as it arrives, retaining valid siblings and last-known
targets with their original observation times after partial failure; the
picker can show each result without waiting for slower peers. A complete current inventory
may remove a missing target; registered windows remain listed from the private
desktop registry. Local redraws do not probe providers. The startup timeout only
reads published state while one refresh is pending; there is no ongoing polling
after completion. Unfinished startup probes are cancelled when the dialog exits,
while accepted native windows outlive the picker.

## Identity and bounded resources

A target key is the canonical tuple of logical Host Mesh ID, effective UID and
account name, provider, and native home/configuration directory. Its
specification digest covers launch-relevant adapter/executable/environment
selection; display labels, current status, titles, daemon process/PID/socket and
native model/settings do not change that digest. Entry references carry the
host, user, target key and specification revision, and the host validates them
again before exec.

Each window registration is scoped by desktop generation and random instance
ID, with a reusable display ordinal and process-birth handles. Dispatch intent
is persisted before launching. Request replay resolves the same instance;
dispatch uncertainty never automatically starts another client. The private
registry lives under `$XDG_RUNTIME_DIR/rofi-agent-plus/direct-v1/` and retains
terminal request tombstones. Catalog cache and selection hint live under
`$XDG_CACHE_HOME/rofi-agent-plus/direct-v1/` and
`$XDG_STATE_HOME/rofi-agent-plus/direct-v1/`, respectively. Files are bounded,
private and versioned; cache data is never launch authority.

| Operation/data | Bound |
| --- | --- |
| Provider capability/status probe | At most 3 seconds per probe; capability results are cached separately from daemon status |
| One host inventory / whole fleet | 12 / 20 seconds; at most 4 concurrent host workers |
| SSH policy | Uses current Host Mesh connect timeout (1–60 seconds) and route-attempt policy |
| Host wire | 1 MiB JSON, 128 targets, 256 diagnostics, bounded 16 KiB fields |
| Entry reference | 64 KiB |
| Local catalog | 8 MiB, at most 256 host records |
| Active registrations | 512 per target; terminal tombstones do not consume active capacity |
| Picker callback state / first frame | 16 KiB / 16 MiB |
| Startup/Alt+R catalog refresh / Niri request | 20 seconds / 1 second; startup is asynchronous, Alt+R waits unless joining startup |

## Source, artifact and native checks

The source gate is:

```sh
./scripts/check
./scripts/check-contract-sync /absolute/path/to/rofi-ssh-plus
```

`scripts/check` runs the repository's unit tests, Ruff lint/format checks,
shell and text-file guards, and released public Host Mesh bundle validation.
`check-contract-sync` separately compares the vendored public Host Mesh bundle
to the explicitly supplied SSH Plus checkout. Source tests use controlled
provider/SSH/process/desktop stubs and do not establish installed or native
behavior.

Build a wheel into a private run directory, then give that exact wheel and
private prefix to the artifact gate:

```sh
mkdir -m 700 /absolute/private/run/wheels
python -m pip wheel --no-deps --wheel-dir /absolute/private/run/wheels .

./scripts/check-installed-artifact \
  --candidate-prefix /absolute/private/candidate-prefix \
  --wheel /absolute/path/rofi_agent_plus.whl \
  --output /absolute/private/run/artifact-receipt
```

It verifies wheel paths, installed-byte parity, entry points, isolated imports,
absence of retired Observer/Tmux runtime dependencies, and a controlled
headless inventory against provider stubs. It runs outside the checkout with
Python isolation; it does not prove native UI or provider entry.

Native candidate cases use private fixtures and the installed candidate
interpreter. `scripts/native-fixture prepare/observe/cleanup` owns a disposable
provider home and records exact cleanup references. `scripts/direct-window-proof`
tests Niri/Kitty marker, TTY, focus, private-layout and lifetime behavior with a
controlled foreground client. `scripts/direct-rofi-proof` uses inert rows and
real Rofi input to check first-frame selection, Tab mode changes and filter
retention without any provider action or periodic timer. `scripts/check-native-entry` selects a
single case by `--host`, `--home`, `--provider` and per-host `--config`; optional
`--helper` pins the explicitly installed candidate helper on a remote peer.
Run it as `candidate-prefix/bin/python -I -B scripts/check-native-entry ...` to
prove the installed artifact. Its provider-client checks use only owned
synthetic native sessions and never submit an agent turn. The required case
matrix and per-case receipts are in the
[candidate execution report](docs/direct-tui-candidate-20261021.md).
`scripts/direct-picker-proof` drives the actual candidate launcher and callbacks
through Open, an additional window, exact-row focus and stale-row refusal using
an owned local fixture and a controlled local Mesh.

Human visual/input judgment, managed desktop selection, publication and rollout
are separate gates. Historical evidence for the retired Observer/Tmux picker is
available from the Git checkpoint recorded in
[legacy retirement](docs/legacy-retirement.md). It does not qualify this
direct-window runtime.
