# Rofi Agent Plus

`rofi-agent-plus` meshes Codex and Claude Code sessions across Host Mesh hosts.
Agent Observer owns passive host-local discovery and work-state observation;
Agent Plus owns the picker, presentation, fresh action validation, and native
attachment requests. Tmux Plus owns terminal and viewer lifecycle. OpenCode is
no longer a supported provider in this migration candidate.

Version `0.13.0a3` is an unreleased migration candidate for Python 3.11+ with no
runtime package dependencies. This checkout replaces the earlier foreground
process-correlation implementation. Historical study and acceptance documents
remain evidence for their stated versions; they do not certify this runtime.

## Runtime boundary

Each observed host needs the independently installed `agent-observer` command.
Agent Plus invokes its public `snapshot --host-scope HOST_ID` JSON contract,
locally or through the routes supplied by SSH Plus. It never imports Observer
or reads provider databases, registries, transcripts, or process command lines.
Observation never launches a provider or starts a missing daemon.

`rofi-tmux-plus` is mandatory. SSH Plus supplies Host Mesh identity, routes and
SSH policy when installed; an absent SSH Plus permits the existing local-only
identity. A malformed installed contract is a visible failure. The exact
released Host Mesh v1 and Tmux Session v1 bundles are independently validated
in this repository; their provenance is unchanged by this migration.

The initial native artifacts are Codex daemon `0.160.0` and Claude Code
`2.1.287`, checked by exact binary hashes and topology. A newer provider or an
unavailable source remains unsupported until independently accepted. Codex is
the primary multi-host target. Claude support is initially exercised on Snap.
A missing Claude installation on another host does not hide healthy Codex rows.

Identity includes the logical host, provider, native namespace, native ID kind,
and native ID. Saved history, loaded server threads, worker processes, and
terminal clients remain separate. Provider-owned titles and working directories
are display metadata, never identity joins.

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

The mixed history list orders rows using explicit native history timestamps
when available. Work and presence have independent evidence clocks. The
Active page uses uncapped per-host rows with current provider presence: Codex
means a loaded server thread; Claude means a verified registered worker.
Active does not mean currently doing work. Rows separately show Working,
Waiting, Settled, Interrupted, Error or Work unknown when supported. Missing,
stale or unsupported work evidence is never displayed as idle.

## Native actions and current limits

Resume re-observes the selected host and requires the complete same identity,
a current accepted source and an exact native attachment route. Codex launches
`env CODEX_HOME=... codex resume --all SESSION_ID`, using the explicit native
session ID independently of the thread ID. Claude attaches a current supervised
background worker with `claude attach JOB_ID`. Default configuration clears
`CLAUDE_CONFIG_DIR` to preserve the native preference location; explicit roots
supply that variable only when Observer identifies the explicit context.
Saved Claude rows with fresh Observer history metadata have a separate Resume
route: `claude --resume FULL_UUID --bg` with closed stdin, then an exact native
job cue and public Observer lookup before attaching. If Claude creates a copy,
the viewer uses its actual new session UUID. Post-command uncertainty never
retries the provider action. Active foreground sessions keep attachment
unsupported. Saved history does not imply an absent or idle worker.

Observer retains native child classification; Agent Plus excludes confirmed
Codex children from ordinary picker rows and rejects child or conflicting
identity actions. Unknown older classifications remain visible. Claude history
comes from Observer's bounded official SDK list, which excludes nested subagent
transcripts. History warnings remain separate from healthy live observations.

Tmux wraps the individual attachment client. The native provider runtime owns
work. Historical provider options on old tmux wrappers do not prove which
conversation a client displays after native `/new` or `/resume` navigation.
Current client-to-conversation binding is unsupported for both providers.
Resume therefore creates a fresh validated attachment rather than focusing a
historical wrapper. Provider-native permission and trust dialogs still apply.

New revalidates the source identity and exact provider-reported directory.
Codex starts a fresh individual TUI in the same native configuration. Claude's
accepted supervised entry is an empty `claude --bg` followed by attach to the
new exact job, with stdin closed and without inventing a task or prompt. A
per-launch `worktree.bgIsolation=none` overlay preserves the selected directory. The implementation and
installed callback acceptance of that path are tracked in the execution
ledger. Merely enabling Agent View does not supervise a bare foreground TUI.

Conversation-specific Open, Close and group Resume/Close require a proved
current viewer binding. Native Observer rows are excluded while that binding
is unsupported. The generic Tmux Plus picker remains available for explicit
viewer management. Closing a viewer is distinct from ending provider work;
Agent Plus does not rename, kill, approve, deny or interrupt native sessions.

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
or history timestamps. The migration cache version rejects old correlations.

Picker preferences at `$XDG_STATE_HOME/rofi-agent-plus/view.json` remember the
page and a successfully used complete native identity. Search begins empty and
action begins at Resume. Old preferences without a namespace are discarded.
Unknown or ambiguous identity never selects a session by title or directory.

## Validation and delivery

```sh
./scripts/check
./scripts/check-contract-sync /path/to/rofi-ssh-plus /path/to/rofi-tmux-plus
```

The main gate runs meaningful contract, identity, cache, UI and action checks,
Ruff, shell and text checks, and released bundle verification. Synthetic tests
do not establish native or visual acceptance. Package installation, source/hash
matching, ordinary hooks, cross-host attachment and rendered picker acceptance
are separate delivery gates.

See [suite ownership](docs/SUITE_INTEGRATION.md),
[configuration](examples/config.toml) and [icon provenance](ASSETS.md).
The implementation and native evidence are coordinated in
[Agent Observer's execution ledger](../agent-observer/docs/execution-status.md).
