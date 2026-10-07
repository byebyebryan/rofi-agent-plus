# Rofi Agent Plus

`rofi-agent-plus` meshes Codex and Claude Code sessions across Host Mesh hosts.
Agent Observer owns passive host-local discovery and work-state observation;
Agent Plus owns the picker, presentation and host/selection validation, and
calls Observer's separate write client for native entry. Tmux Plus owns terminal and viewer lifecycle. OpenCode is
no longer a supported provider in this migration candidate.

Version `0.14.0a1` is the unpublished provisional pilot selected on Snap and
Starship, for Python 3.11+ with the pinned
`agent-observer==0.2.0a3` public contract library. This checkout replaces the earlier foreground
process-correlation implementation. Historical study and acceptance documents
remain evidence for their stated versions; they do not certify this runtime.
The [operational proof](docs/native-operational-proof.md) records independent
Observer acceptance, installed real Tmux/SSH entry, selected headless gates and
the remaining desktop/release checks.
Observer now selects a7/API v1 wire 3 and a shared user service on both hosts.
This Plus pilot still binds wire 2 and requires a separate reader/cache/transport
migration; its prior installed acceptance does not establish current compatibility.
The [client handoff](docs/observer-client-handoff.md) records the current Observer
review, provider upgrade blocks, pinned interface and separate client ownership.
The earlier [a11 consumer plan](docs/observer-a11-integration-plan.md) supplies
presentation/cache/action checkpoints within its recorded baseline bounds.

## Runtime boundary

Each observed host needs independently installed `agent-observer` and
`agent-observer-write` commands from an independently accepted producer artifact;
the current Snap/Starship pilot selects `0.2.0a11` separately from Plus's frozen
`0.2.0a3` pure validator dependency. Plus invokes
public snapshot v2 and write request/plan/result v1 contracts locally or through
SSH Plus routes. Its pinned Python dependency provides only pure public contract
validation; the frontend does not import collectors or provider action internals.
No provider databases, registries, transcripts or process command lines are read
by Plus. Observation never starts a missing daemon or provider session.

`rofi-tmux-plus` is mandatory. SSH Plus supplies Host Mesh identity, routes and
SSH policy; an absent SSH Plus permits the existing local-only identity. Invalid
installed contracts fail visibly. The released Host Mesh v1 and Tmux Session v1
bundles retain their independent validation and provenance.

Observer owns native artifact/topology acceptance. Its selected a11 matrix accepts
Codex 0.160.0 entry and exact 0.160.1 managed images on Snap/Starship, and Claude
2.1.289 on Snap, with separately bounded surviving 2.1.287 worker support.
The new consumer pass revalidates end-to-end actions against that producer;
prior consumer evidence retains its recorded image bounds. Missing or unsupported
sources remain explicit, preserving healthy sibling providers. Identity contains
logical host, provider, store namespace, native ID kind and full native ID.
Runtime, worker, phase, outcome, attachment and saved history stay separate.
Titles, paths and project metadata never join native identities.

The next Observer checkpoint replaces provider release/hash allowlists with
required protocol/metadata/CLI contract compatibility. Plus consumes that
capability boundary without provider version pins. Observed versions/hashes
remain diagnostics and write-context guards; routine compatible upgrades should
not require frontend changes or a new cache/schema version.

## Run

```sh
./bin/rofi-agent-plus list --limit 100
./bin/rofi-agent-plus active
./bin/rofi-agent-plus-rofi -show agent-plus \
  -modes "agent-plus:$(pwd)/bin/rofi-agent-plus" \
  -kb-custom-1 Alt+r -kb-custom-2 Right -kb-custom-3 Left \
  -kb-custom-4 Alt+a -kb-custom-7 Tab -kb-custom-8 ISO_Left_Tab \
  -kb-element-next "" -kb-element-prev "" \
  -kb-accept-custom "" -kb-delete-entry "" \
  -kb-cancel Escape,Control+g -eh 2
```

The normal managed desktop entry remains `Mod+A`. Up and Down select rows;
Left and Right cycle Active, Open, All, Local and Host Mesh remote pages.
Tab and Shift+Tab cycle Resume, Close and New. Enter applies the displayed
action. Alt+R requests a finite refresh. Escape and Ctrl+G close through Rofi's
native cancel path. Page changes read the cache and preserve the filter.

The list orders current phase by attention: **Blocked, Waiting, Working**, then
unknown. Within each group it orders by actual conversation activity when that
source is available; unknown activity age is displayed as unknown. Creation,
provider metadata modification and liveness never become conversation age.
Configured workspace roots display a root key and relative path; raw cwd and
Git/project metadata remain available in diagnostic JSON.

The Active page uses uncapped per-host rows with current running runtime evidence:
loaded Codex threads or verified registered Claude workers. Running does not mean
working. A completed Claude background context with a current worker can be
Waiting with a separate Completed outcome. Saved-only rows retain unknown runtime;
missing observations do not imply parked. Confirmed children of either provider
are hidden; unknown classifications stay visible.

## Native actions and current limits

Resume refreshes the owning host, resolves the same complete store/native reference
and calls `agent-observer-write prepare`, then `execute`. Observer selects and
validates the exact native route, executable, config, cwd and runtime. Plus pins
the prepared remote route for execution and never repeats an uncertain write.
Results preserve requested and actual copied identities and confirmed effects
when presentation fails. The frontend does not construct provider argv or parse
Claude launch cues.

Tmux Plus receives only `agent-observer-write enter --plan-json PLAN` through its
public deferred create/open interface. Observer revalidates at native TTY entry.
Tmux owns the viewer; the provider owns work. A typed name collision can retry the
viewer with the same plan. Post-write authority, transport or presentation failure
never dispatches another New/Resume operation.

New uses the refreshed selected row only to choose provider, owning host,
configuration and an unchanged absolute directory. Its write request contains no
old session reference. Observer's independent New client also works with zero rows.
Codex resolves new identity after native entry. Claude can create a background
context before attachment; viewer failure preserves confirmed provider work.
Native permissions and trust prompts remain provider-owned.

Current client-to-conversation binding is unsupported. Resume creates a fresh
validated attachment; historical tmux launch options do not authorize focus,
Close, conversation-specific Open or group viewer actions. Generic Tmux Plus
viewer management remains available. Plus does not rename, kill, approve, deny
or interrupt native sessions.

## Configuration and retained state

Configuration is optional at
`$XDG_CONFIG_HOME/rofi-agent-plus/config.toml`, defaulting to
`~/.config/rofi-agent-plus/config.toml`:

```toml
max_sessions = 40
refresh_seconds = 30

[host_providers]
snap = ["codex", "claude"]
starship = ["codex"]
```

Only `max_sessions`, `refresh_seconds` and the optional `[host_providers]`
table are accepted. Each host table entry selects one or both supported
providers. Host IDs follow Host Mesh identifier rules and are matched without
case sensitivity. Hosts not listed here use both providers. An enabled
provider that is unavailable remains visible with its source error. Hosts,
routes and SSH policy belong to SSH Plus; terminal settings belong to Tmux
Plus. `list --limit` overrides only the history limit for that invocation.

The private cache is under `$XDG_CACHE_HOME/rofi-agent-plus/` with directory
mode 0700 and files mode 0600. A refresh publishes an authority-scoped snapshot
atomically. Provider failures retain last-known rows with bounded diagnostics;
healthy providers remain usable. A liveness refresh never renews old work-state
or history timestamps. Cache version 7 rejects prior identity/state interpretations. Partial inventories
retain missing rows with stale dimensions and original evidence clocks; current
rows keep their independently validated facts.

Picker preferences at `$XDG_STATE_HOME/rofi-agent-plus/view.json` remember the
page and a successfully used complete native identity. Search begins empty and
action begins at Resume. Old preferences without a namespace are discarded.
Unknown or ambiguous identity never selects a session by title or directory.

## Validation and delivery

```sh
./scripts/check
./scripts/check-contract-sync /path/to/rofi-ssh-plus /path/to/rofi-tmux-plus
```

Before development checks, install the accepted Observer wheel into the chosen
Python environment, then install `.[dev]`. The prerelease dependency is not yet
published; use the independently validated local artifact described in the
[execution record](docs/observer-v2-execution.md). Hosted CI needs that dependency
published before its normal package install can run.

The main gate runs meaningful contract, identity, cache, UI and action checks,
Ruff, shell and text checks, and released bundle verification. Synthetic tests
do not establish native or visual acceptance. Package installation, source/hash
matching, ordinary hooks, cross-host attachment and rendered picker acceptance
are separate delivery gates.

See [suite ownership](docs/SUITE_INTEGRATION.md),
[configuration](examples/config.toml) and [icon provenance](ASSETS.md).
The separate [consumer execution record](docs/observer-v2-execution.md) tracks
source, packaged and native gates after Observer's independent acceptance.
