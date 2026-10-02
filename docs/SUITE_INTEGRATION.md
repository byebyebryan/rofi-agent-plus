# Rofi Agent Plus Suite Integration

Status: the P7 contract-only cutover and guarded-open performance follow-up are
complete in version `0.3.0`; the P8 flat-scope navigation implementation is
complete in version `0.4.0`. The coordinated P8 cutover was published and
accepted on Snap and Starship. P9 locked CLI contracts are implemented here as
an independent consumer with exact released producer provenance. Current
deployment targets and acceptance are recorded in the managed dotfiles
[Rofi Plus status ledger](https://github.com/byebyebryan/dotfiles/blob/main/docs/rofi-plus-status.md);
suite deployment is coordinated through chezmoi.
Agent Plus consumes Tmux Session v1 through its public process contract on every
product path; Tmux Plus is mandatory. It consumes
Host Mesh v1 when SSH Plus is available and otherwise uses the shared
local-only identity with a `null` Mesh revision. A present malformed or
unsupported companion is visible and never enables a fallback. Tmux Plus
retains generic rename/kill ownership; Agent Plus has no rename/kill action.

## Target ownership

The three Rofi pickers form an acyclic stack:

```text
rofi-ssh-plus
  owns logical hosts, aliases, routes, SSH policy, and route health
          |
          +-----------------------------+
          |                             |
          v                             v
rofi-tmux-plus                  rofi-agent-plus
  owns generic tmux               owns provider-native
  inventory and lifecycle         discovery and resume policy
          |                             |
          +-----------------------------+
              Agent Plus consumes
              generic tmux operations
```

The integration boundaries are versioned process contracts:

- [Host Mesh Contract v1](https://github.com/byebyebryan/rofi-ssh-plus/blob/main/docs/HOST_MESH_V1.md)
- [Tmux Session Contract v1](https://github.com/byebyebryan/rofi-tmux-plus/blob/main/docs/TMUX_SESSION_V1.md)

Agent Plus does not import another repository's Python modules or read its
private configuration, history, health, or cache files.

## P8 flat-scope navigation

P8 removed Agent Plus's `Hosts` and `Providers` browsing roots and all group
rows. The navigation follow-up adds Active to that flat model. The normal
picker contains only agent-session leaves in this page ring:

```text
Agents › Active
Agents › All
Agents › Local
Agents › <remote host in Host Mesh order>
```

`Active` shows observed running conversations across hosts using the existing
effective activity predicate. It collects validated cached per-host rows
before the flattened All list's cap; failed activity evidence cannot retain
an old active marker. `All` is the mixed newest-first list across hosts and
providers. `Local`
follows, then every authoritative remote in stable Host Mesh order. Empty or
unavailable hosts retain their scope and may render a non-actionable empty-state
row; activity never moves a view. When no remote exists, the redundant `All`
and `Local` scopes collapse into `Local`, leaving Active and Local. The first
launch starts in All when available, otherwise Local. Subsequent launches
restore the saved page and last successfully used conversation. Search starts
empty and the action starts at Resume. Active remains a page even when empty.

Provider becomes a searchable row attribute rather than a navigation axis.
Provider icons remain visible, and provider names and aliases remain in filter
metadata, so `codex`, `claude`, and `opencode` continue to isolate sessions
without mixing host and provider dimensions in one view ring.

Left and Right wrap through pages using the current immutable snapshot;
they never trigger provider discovery, tmux inventory, or SSH work. They
preserve the filter and reset selection to the first eligible matching row.

P8 established the flat host-scope ring. Its release retained Rofi's native
Tab row navigation; the later P10 action cycle replaces that binding while
preserving P8's cached Left and Right scope behavior.

The [navigation design](agent-plus-picker-navigation-plan.md) specifies page
restoration and selection fallbacks. Explicit page changes and successful
Enter actions persist a private, versioned preference in
`$XDG_STATE_HOME/rofi-agent-plus/view.json`; these IDs remain navigation hints.
Native cancellation does not report arrow-only selection changes. Automatic
refresh and diagnostic CLI calls do not write preferences. The managed
Mod+A invocation uses the small `rofi-agent-plus-rofi` launcher so the saved
selection is supplied before Rofi's first display. The launcher prepares one
initial frame and serves that same frame to the first script callback, avoiding
another discovery pass or a race between index calculation and row display.

## P10 shared action cycle and batch controls

Up and Down navigate rows. Tab (`custom-7`, return value 16) selects the next
action and Shift+Tab (`custom-8`, return value 17) selects the previous one,
wrapping `Resume`, `Close`, and `New`. The selected action is versioned state in
per-dialog `ROFI_DATA`, so it survives page navigation, filters, Alt+R, and
background refresh callbacks. Left/Right changes page without changing the
action. Enter applies the shared action to the highlighted conversation or
typed All active control. Escape plus Ctrl+G always close through Rofi's native
cancel path; neither cancellation key is a script callback.

The leading typed `All active sessions (N)` control is not a conversation or
preference target. It shows a cache-backed count of current-page active or
waiting rows before the ordinary history cap. A real conversation remains the
default selection when one exists; an empty page selects All active. `Alt+A`
(`custom-4`, return value 13) clears search and selects All active without
changing the shared action. The root allows custom-key callbacks when filtering
leaves no matching row, while custom-input and deletion callbacks remain
read-only. On All active, `New` displays a select-a-conversation notice without
provider, host, viewer, or preview work.

On a selected conversation, `Close` prepares an inline preview only for the
selected typed host/provider/session and exact current Tmux reference. A
currently verified provider option and public Tmux viewer inspection must
confirm the current association; this also permits closing verified viewers
for an idle conversation. It does not refresh or rebind provider sessions.
Missing, stale, ambiguous, unsupported, or unverified associations are shown
as exclusions.

On All active, Enter with `Resume` or `Close` immediately changes the control
label to `All active · Preparing…`. The shared Resume / Close / New action
bar remains unchanged during preparation, confirmation, and job progress;
selection movement requires no redraw. A finite `_batch-prepare` helper refreshes the
selected page's owner-host coverage once, independently of Rofi search, and
checks at most four exact references concurrently. Timed callbacks read its
private request and display the completed fixed preview; they never confirm
it. Preparation starts only on Enter, and an early Enter only redraws.
Active and All cover every
authoritative host; All reads validated per-host active candidates before the
flattened list cap. Local and named-host pages cover only their respective
owner. The preview freezes complete Tmux references, and Close freezes the
viewer IDs found on this endpoint. Only exact provider identity plus complete
reference matches receive a colored title without changing conversation text.
The ready control reads `All active · Confirm Resume (N)` or
`All active · Confirm Close (N)`; it carries the exact typed preview ID. The
count, colored titles, and confirmed job include only `open` or `close` targets.
Already open/closed viewers are observations, not operations; a preview with
no remaining work cannot be confirmed or submitted, including through a stale
control. Its row reads `All active · No windows to open` or `close`. Resume
does not focus already open viewers. If a viewer closes after preparation,
prepare a new preview to include it.
Changed references, exclusions, and
missing targets remain visible without eligible tint. Preview target rows are
display-only. Changing the action or leaving through a page or conversation
callback discards only the matching unconfirmed preview or preparation request.
A random request ID guards publication under the private state lock; cancelled
or superseded helpers cannot overwrite a newer preview. Pending preparation
expires after 120 seconds, checked before each inspection chunk and publication.
Already-running read-only calls finish under their public command timeout.
Native Escape/Ctrl+G has no script callback; preparation may finish without
any viewer effects, and an unconsumed private preview retains its bounded expiry.

Confirm consumes the private preview once and starts one background worker for
the endpoint. A second request reports the existing job. Resume revalidates
each selected host under a newly prepared current authority and uses only Tmux
Plus's existing-reference viewer-open path; it never calls Agent Plus create
or reconcile lifecycle. Close uses the public guarded viewer closure operation
for only the frozen viewer IDs. Protocol or authority failures stop the
remaining list, individual target failures continue, and ambiguous outcomes
are not retried. Escape or Ctrl+G before Confirm is native cancellation; after
Confirm it hides the UI while the fixed job continues. The result reports done,
already open/closed, skipped, and failed counts.

## New session here lifecycle

`New session here` is separate from the existing Resume fast path and its
`_create` wrapper. It first performs a fresh selected-host provider refresh
under the current Host Mesh authority. The current result must contain exactly
one row for the selected host, provider kind, and provider ID; it must have
`sourceObservation: current` and the same provider-reported absolute cwd.
Retained, activity-only, ambiguous, changed-directory, or stale-authority rows
are visible no-action failures.

Before Tmux creation, Agent Plus asks its selected Host Mesh backend to prove
that the exact cwd is still a directory and the bare provider executable
(`codex`, `claude`, or `opencode`) resolves on that host. It then calls only
the public Tmux Session create command with `--host`, the current
`--mesh-revision` when present, a sanitized cwd-basename/provider name, exact
`--cwd`, `--defer-until-attached`, `--open`, and `-- <bare-provider>`.
It supplies neither a provider resume identifier nor provider `@` options.

Only typed pre-action `session_exists` gets bounded name-suffix retries. A
typed `stale_mesh` gets one whole-Mesh refresh and a repeat of the same source
checks before one new create request. Timeout, transport, malformed-response,
and post-action ambiguity failures do not retry. The source provider row and
its pre-existing tmux session are never opened, reconciled, or mutated by this
lifecycle.

## P9 locked CLI contracts

P9 implements Agent Plus as an independent consumer of two local process
contracts. Tmux Session v1 remains required. Host Mesh v1 remains optional,
with a missing SSH Plus selecting local-only identity and any present contract
failure remaining visible rather than triggering fallback.

Agent Plus vendors each complete canonical producer bundle with one exact
`SOURCE.json` provenance record, validates it offline, and retains its own
strict parsing and bounded subprocess implementation. It does not import
either sibling package, read sibling configuration or state, add a shared
virtual environment, or publish its private provider refresh/cache format as
another suite contract.

Strict UTF-8 JSON, single-document stdout, duplicate-key rejection, published
command-specific byte and field caps, generic handling for unknown typed
errors, and exact Mesh/session identity are explicit conformance cases.
Documented pre-action errors retain their bounded refresh or modified-request
recovery; ambiguous process or response failures never repeat a lifecycle
action. Stderr text and process exit numbers never authorize fallback, route
health, or mutation.

The consumer's `SOURCE.json` records the exact released producer commit and
checksum-manifest digest for each vendored bundle. `scripts/check-contract-sync`
also accepts a later producer `HEAD` when the recorded commit resolves and is
an ancestor, the manifest at that commit is byte-identical to the clean
current producer manifest, and the current bundle is byte-identical to the
vendored bundle. A missing, non-ancestor, dirty, or contract-changing
producer checkout fails the gate; contract changes therefore still require a
new released tuple and consumer repin.

P9 changes no provider discovery, correlation, resume command, cache, Rofi row,
or P8 navigation behavior. The coordinated suite design and rollout boundary
live in the managed `rofi-plus-p9-cli-contracts.md` document.

Agent Plus's private v4 observation metadata remains outside both public
process contracts.  Cache-age checks and background-check failures may retain
usable per-host or provider rows and label them as last known, but never change
Host Mesh or Tmux identity authority.  A background check is authority-scoped
and commits one complete snapshot, so rows publish together; Rofi polls the
private worker marker at roughly one-second intervals and has no resident push
channel or progressive row publication.  Observation labels are presentation
only and never lifecycle or selection authority; selected rows continue through
the existing Tmux Session revalidation path.

P9 itself did not change picker presentation. A subsequent post-P9 SSH-only
refinement makes SSH recent-only and restores its native filter arrows; it
leaves Agent/Tmux behavior, Host Mesh v1, and both P9 wire contracts unchanged.
That SSH refinement is published separately; chezmoi owns its managed
deployment and acceptance status.

The canonical `rofi-ssh-plus`, `rofi-tmux-plus`, and `rofi-agent-plus`
commands are resolved through `PATH`. A suite deployment installs public
entry-point symlinks under `~/.local/bin` or an equivalent user executable
directory in addition to the separately named Rofi script modes. Cross-project
calls never depend on checkout paths or `~/.config/rofi/scripts`.

## Agent Plus retains

Agent Plus remains authoritative for:

- Codex, Claude Code, and OpenCode native session discovery;
- provider IDs, titles, working directories, timestamps, and icons;
- provider-specific resume commands and validation;
- active provider processes, including agents running outside tmux;
- correlation between native provider sessions and tmux panes/options;
- waiting-session reuse and title-derived, collision-free provider wrapper
  names; and
- the P8 flat host-scope presentation.

SSH Plus must not know provider commands. Tmux Plus may carry generic pane
metadata and requested tmux `@` options but must not assign meaning to them.

The managed-session correlation expects foreground provider TUIs in ordinary
tmux sessions. Codex's shared app-server daemon and Claude Code's background
agent view are outside this contract. Agent Plus's short-lived Codex
`app-server --stdio` query lists saved threads; it is not a shared daemon.

Agent Plus owns launch-time provider identity and current process evidence, not
provider-internal conversation transitions. A tmux provider option records the
conversation assigned when its wrapper was created; it does not prove which
conversation the TUI currently displays after an in-TUI `/resume`, `/new`,
`/clear`, or OpenCode `/sessions` switch. Those transitions are unsupported.
The picker can focus the original wrapper for a row that is no longer displayed
there, or create another wrapper if it cannot match the new conversation to a
process. No provider lifecycle hook updates tmux options or wrapper names.

`New session here` also starts OpenCode without `--session`, so its process
cannot be matched to the newly saved ID by the current argument-based activity
probe. Until that TUI exits, its saved row may appear `Idle` and `Resume` may
create another wrapper. This is a known limit of the external tmux workflow.

## Removed responsibilities

After migration, Agent Plus no longer owns:

- `hosts`, `host_routes`, or host aliases;
- SSH executable, connection timeout, or route fallback policy;
- generic tmux inventory parsing;
- generic terminal construction and detached lifecycle;
- Niri terminal-title matching;
- generic local or SSH tmux attachment; or
- raw tmux session creation and option-setting mechanics.

P7 removed the Agent-owned host, SSH, generic tmux, Niri, terminal, and direct
lifecycle implementation. Agent Plus retains provider-native process probes
and correlation helpers only; no second cross-repository Python dependency was
introduced.

## Discovery flow

A full Agent Plus refresh follows this sequence:

1. When available, call `rofi-ssh-plus mesh list --json`, validate Host Mesh
   schema version 1, and retain its opaque `meshRevision` for the whole refresh.
   When the executable is absent, synthesize the same local-only host identity
   as Tmux Plus and use a `null` revision.
2. Use the local descriptor and configured remote descriptors as the provider
   discovery set.
3. Run provider-native probes against each logical host. For remote hosts, try
   the ordered route candidates with the advertised SSH policy.
4. Report only classified SSH transport results through
   `mesh report-route`, including the mesh revision and attempt completion
   time; never report a provider command failure as an unreachable route.
5. Concurrently call `rofi-tmux-plus inventory --json --panes [--mesh-revision REVISION]`
   with one repeated `--host` for each host in the retained discovery set and
   with the provider session options requested explicitly.
6. Correlate provider identities, processes, pane PIDs, and tmux user options
   inside Agent Plus.
7. Merge provider results into Agent Plus's own per-host snapshot and render
   its existing views.

Provider and tmux stages remain independently reportable. A reachable host may
have a successful tmux inventory and a failed provider probe, or the inverse.
One stage must not erase the other stage's last good data.

Every remote provider probe uses Host Mesh's nonce-bearing reached-host
protocol. The marker is removed before provider output is parsed. A valid
marker makes the route reachable even when the provider is missing or returns
an error; a missing marker never turns arbitrary exit code 255 into a provider
result. If Tmux Plus returns `stale_mesh`, the entire refresh is discarded and
retried from one new Host Mesh observation rather than merging generations.

Agent Plus retains its private cache because provider history is its domain.
The Tmux Session inventory command is live; Agent Plus decides whether to
retain last-known agent rows after a host or provider failure.  Cache-age alone
does not make a row warning; only failed observation evidence adds a retained or
limited presentation label.

## Correlation model

Agent Plus asks Tmux Plus for pane metadata and these session options:

```text
@codex_thread_id
@codex_name
@claude_session_id
@claude_name
@opencode_session_id
@opencode_name
@agent_picker_waiting
```

`@agent_picker_waiting` is a legacy correlation input. Newly created wrappers
use the provider-neutral `pending` field returned by Tmux Plus.

The provider ID remains the primary Agent Plus row identity. An associated
tmux reference is subordinate data:

```json
{
  "provider": "codex",
  "providerSessionId": "00000000-0000-0000-0000-000000000000",
  "hostId": "desktop-a",
  "tmux": {
    "meshRevision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "serverGeneration": "tmux-v1:1722741000:1234:/run/user/1000/tmux-1000/default",
    "sessionId": "$6",
    "createdAt": 1722742000,
    "observedName": "project"
  }
}
```

This prevents a tmux rename from changing provider identity and prevents a
provider title from becoming a tmux action target. Display labels remain
separate from both identities.

## Open and resume flow

When the selected provider session already has a compatible tmux reference:

1. If discovery proved the association with the exact provider tmux option,
   call `rofi-tmux-plus open` early with mesh revision, host ID, server
   generation, session ID, creation time, the observed name when present, and
   `--require-option OPTION=PROVIDER_ID`. Tmux Plus checks every precondition
   before focusing or launching.
2. On an explicit no-action `stale_session`, `session_not_found`,
   `stale_mesh`, or compatibility `invalid_input`, discard the fast attempt
   and continue once through the normal selected-host revalidation. A
   timeout, malformed response, ambiguous nonzero result, `operation_failed`,
   or `launch_failed` is rendered as an error and is never retried because it
   could follow a successful action.
3. Process-only associations, retained rows without option proof, incomplete
   or stale references, and rows without a tmux session use the existing full
   provider/host revalidation path. The option-backed marker is typed JSON and
   is not derived from visible row text or raw cached option metadata.
   Local-only selections with a `null` Mesh revision also remain on this full
   path because omitting `--mesh-revision` is unpinned at the Tmux boundary,
   not a local-only assertion. A non-null Mesh revision is required for the
   early guarded open.

When no compatible tmux session exists:

1. Apply provider-native checks, including the current protection against
   resuming an agent active outside tmux.
2. Derive a human-readable wrapper name from the provider title, choose a
   collision-free suffix when needed, and build the provider resume argv.
3. Call `rofi-tmux-plus create` with the mesh revision, logical host, working
   directory, provider argv, provider `@` options, `--defer-until-attached`,
   and `--open`.
4. If `session_exists` races with creation, choose another name or refresh;
   never adopt an unrelated session by name alone.
5. Retain the returned stable reference for subsequent refresh and open calls.

Provider commands cross the Tmux Session contract as argv following `--`;
Tmux Plus owns the wait wrapper and safe local and remote construction.

## Host identity and presentation

Every Agent Plus row uses the Host Mesh logical `id` for identity and its
`display` value for presentation. Native hostnames and aliases correlate
processes, terminal titles, and provider records but do not create duplicate
host groups.

When Host Mesh is present, the local logical host is supplied by it rather than
inferred independently. This prevents the same machine appearing under a
native hostname, route alias, and friendly name in different pickers. The
shared fallback rule below applies only while the executable is absent.

The implemented Rofi interaction contract is:

- Up and Down navigate rows;
- Tab and Shift+Tab cycle `Resume`, `Close`, and `New`;
- Left and Right switch `Active`, `All`, `Local`, and stable remote host pages;
- Enter applies the displayed action to a selected conversation or typed All active;
- Alt+A clears search and selects All active;
- Escape closes through Rofi's native cancel path; and
- Ctrl+G closes unconditionally through Rofi's native cancel path.

The callback boundary is fail closed. Configuration, capability/model, and
selection errors render bounded diagnostics without turning a callback failure
into a process crash. The retained custom-6 callback migration guard returns
immediately without rendering or loading the model; normal Escape and Ctrl+G
are native Rofi cancellation paths and are not assigned to a script callback.

## Clean product rename

The repository and source tree use `rofi-agent-plus` consistently across the
distribution, executable, Python package, Rofi script mode, configuration, and
cache paths. There is no old-name executable shim, dual package, or fallback
configuration lookup. `Mod+A` remains the desktop binding because it names the
domain rather than the implementation.

The installed product switches names in one coordinated chezmoi deployment:
the new release pin, `~/.local/bin` command, Rofi script symlink, configuration
source, and Niri command land together. The same deployment explicitly retires
the old Rofi symlink and external archive target rather than assuming an
unmanaged external directory will disappear. The old cache is discarded
because it is derived state; the managed configuration is rendered directly at
its new path rather than discovered or migrated by the application.

Existing tmux options retain their historical spelling, including
`@agent_picker_waiting`, `@codex_thread_id`, `@claude_session_id`, and
`@opencode_session_id`. Renaming those options would break correlation with
live sessions and DMS-era wrappers for no user-facing benefit. The new generic
Tmux Plus pending marker supersedes `@agent_picker_waiting` only for newly
created wrappers.

## Final configuration ownership

The standalone Agent Plus configuration keys move as follows when suite
integration lands:

| Former Agent key | Target owner |
| --- | --- |
| `hosts` | SSH Plus Host Mesh |
| `host_routes` | SSH Plus Host Mesh |
| `aliases` | SSH Plus Host Mesh |
| `ssh_connect_timeout` | SSH Plus SSH policy |
| `ssh_connection_attempts` | SSH Plus SSH policy |
| `terminal` | Tmux Plus for tmux-backed opening |
| `max_sessions` | Agent Plus |
| `refresh_seconds` | Agent Plus provider cache |

The coordinated deployment renders host and SSH policy into SSH Plus, terminal
argv into Tmux Plus, and only provider-owned keys into Agent Plus. Agent Plus
rejects the removed host, SSH, and terminal keys instead of supporting two
authorities. Chezmoi converts terminal configuration to Tmux Plus's argv-array
form; Agent Plus does not perform an old-path or old-schema migration.

The diagnostic CLI has no route, alias, host, SSH, terminal, or direct-open
compatibility overrides. `list` and `refresh` use the selected contract backend;
new integration tests exercise contract fixtures rather than importing owner
implementations.

## User-visible handoffs

The suite keeps independent desktop entry points:

```text
Mod+S  SSH hosts
Mod+A  agent sessions
Mod+T  Ghostty / raw terminal
Mod+Return  Ghostty / raw terminal
Mod+G  tmux sessions
Mod+Shift+G  tmux cheatsheet
```

The managed Niri source assigns `Mod+G` to the Rofi Tmux Plus script mode and
keeps `Mod+Shift+G` for the DMS tmux cheatsheet. The source-level binding is
validated by Chezmoi's materialization checks. P6 completed live focus,
attach, and remote acceptance for the exercised local and remote paths; these
remain host-specific rollout checks for later changes. `Mod+T` and
`Mod+Return` both launch Ghostty.

After the data and lifecycle contracts are stable, SSH Plus may add contextual
actions that launch Tmux Plus or Agent Plus already scoped to the selected
logical host. Those handoffs pass only a Host Mesh ID. Tmux Plus does not gain
provider-specific actions or icons merely to create a reverse dependency.

## Failure and fallback rules

- An absent SSH Plus permits local-only operation. Both Agent Plus and Tmux
  Plus synthesize the same fallback identity: the short hostname case-folded
  as ID only when it is a valid host ID (otherwise `localhost`), the validated
  raw short hostname as display (otherwise that ID), and the validated full
  plus short hostnames as aliases (otherwise that ID).
  Malformed or unsupported Host Mesh output is visible and is not silently
  treated as an empty mesh.
- An absent Tmux Plus is a visible, closed failure. Agent Plus has no
  standalone generic tmux engine.
- Partial host or provider failures retain valid per-host snapshots and do not
  overwrite them with empty data.
- Route-health reporting never increments SSH user connection history.
- Error strings crossing contracts are bounded, sanitized, and displayed as
  diagnostics rather than parsed for identity.

## Adoption and acceptance

1. Land Host Mesh v1 fixtures and implementation in SSH Plus. (Complete in
   current source.)
2. Land Tmux Session v1 fixtures and implementation in Tmux Plus. (Complete
   in current source.)
3. Add Agent Plus consumer adapters behind explicit capability detection.
   (Complete in current source.)
4. Run contract-backed discovery against deterministic fixtures and compare
   host/session identity, activity, and lifecycle decisions (open-existing,
   create, and active-outside-tmux refusal). (Complete in P6.)
5. Keep all three public commands and Rofi modes on the managed `PATH` and
   verify local plus remote discovery, focus, create, and resume on each
   intended host. (Complete in P6 through automated and operator acceptance.)
6. Retire the accepted legacy host-route and generic tmux rollback boundary.
   (Complete in P7; managed rollout is coordinated through chezmoi.)

Acceptance requires that the same logical machine has the same host ID and
display label in all three pickers, that a provider session maps to the same
stable tmux reference in both consumers, and that background refreshes do not
alter SSH usage ranking. It also requires that each public command resolves
from the Niri session's `PATH`, stale mesh observations are rejected rather
than merged, and an external tmux rename cannot redirect an open or destructive
action to a different session. P6 additionally requires that malformed
configuration/model/callback data leaves every picker closable: Escape and
Ctrl+G close unconditionally through Rofi's native cancel path. P10 uses Tab
and Shift+Tab for its action cycle while Left and Right remain cached host-scope
changes, action state survives callbacks, and forged group rows are rejected.

## P6 acceptance and P7 performance closure

P6 functional acceptance is complete. The unattended gate passed from the two
exercised managed host perspectives and covered Host Mesh identity, Agent
contract refresh, headless navigation and Escape behavior, exact-reference
lifecycle operations, stale guards, cleanup, and preservation of pre-existing
sessions and SSH usage history. Operator acceptance then covered the rendered
pickers, local and remote SSH/Tmux actions, Agent focus/resume, and a cold
provider resume. That last path exposed one launch-boundary defect: `systemd-run`
expanded a stable tmux ID such as `$3` before Ghostty received it. Tmux Plus
now disables that expansion, has regression coverage, and passed the repeated
cold-resume check.

Performance is recorded as a non-blocking follow-up rather than hidden inside
the functional sign-off. Representative warm-path profiling on one managed
host observed these pre-terminal costs:

| Path | Observed time | Dominant work |
| --- | ---: | --- |
| Agent Plus selection before Tmux action | about 2.0 s | authoritative all-host provider refresh followed by live Tmux inventory |
| Tmux Plus local open | about 0.24 s | process startup, model load, Mesh load, and exact-reference validation |
| Tmux Plus remote open | about 0.65 s | the local work plus one SSH revalidation |
| SSH Plus managed selection | about 0.62 s | detached-worker startup and the synchronous pre-launch reachability probe |

Terminal startup is excluded from those measurements. The Agent path was the
largest target: even a local selection waited for remote-host activity/provider
discovery and then a separate all-host Tmux inventory. P7 now scopes lifecycle
refresh to the selected host and starts provider discovery plus Tmux inventory
concurrently under one deadline, while retaining stage-level failures and
stable-reference checks. Option-backed rows can then take a guarded direct-open
path whose complete tmux reference, expected name, Mesh revision, and provider
option are revalidated before any action. Tmux Plus avoids the unnecessary
model reload on a successful exact-reference open, and SSH Plus's managed
prelaunch path is implemented without weakening route fallback or
successful-connection history semantics. The coordinated sources passed their
automated gates, were pinned and deployed through the managed configuration,
and passed operator acceptance for the exercised local and remote paths. The
table remains a diagnostic pre-P7 baseline rather than an API guarantee or
latency SLA; there is no open P7 performance gate.
