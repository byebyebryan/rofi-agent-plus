# Rofi Agent Plus Suite Integration

Status: P6 discovery/correlation/cache, contract-backed open/create,
fail-closed Rofi callbacks, deployment, and operator acceptance are complete.
Agent Plus consumes Host Mesh v1 and Tmux Session v1 through public process
contracts when both capability executables are available; the complete legacy
backend remains the rollback path when zero or one is present. Tmux Plus
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
- the `Recent`, `Hosts`, and `Providers` presentation.

SSH Plus must not know provider commands. Tmux Plus may carry generic pane
metadata and requested tmux `@` options but must not assign meaning to them.

## Responsibilities to remove

After migration, Agent Plus no longer owns:

- `hosts`, `host_routes`, or host aliases;
- SSH executable, connection timeout, or route fallback policy;
- generic tmux inventory parsing;
- generic terminal construction and detached lifecycle;
- Niri terminal-title matching;
- generic local or SSH tmux attachment; or
- raw tmux session creation and option-setting mechanics.

The P6 implementation kept legacy lifecycle paths while contract open/create
deployed and received live acceptance. Removing that compatibility code is a
separate reviewed follow-up; acceptance does not make cleanup automatic.

## Discovery flow

A full Agent Plus refresh follows this sequence:

1. Call `rofi-ssh-plus mesh list --json`, validate Host Mesh schema version 1,
   and retain its opaque `meshRevision` for the whole refresh.
2. Use the local descriptor and configured remote descriptors as the provider
   discovery set.
3. Run provider-native probes against each logical host. For remote hosts, try
   the ordered route candidates with the advertised SSH policy.
4. Report only classified SSH transport results through
   `mesh report-route`, including the mesh revision and attempt completion
   time; never report a provider command failure as an unreachable route.
5. Call `rofi-tmux-plus inventory --json --panes --mesh-revision REVISION`
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
retain stale agent rows after a host or provider failure.

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
    "meshRevision": "sha256:0123456789abcdef",
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

1. Revalidate provider state as required by that provider.
2. Call `rofi-tmux-plus open` with mesh revision, host ID, server generation,
   session ID, and creation time. The observed name is not an open precondition.
3. On `stale_session`, refresh correlation and retry only if the same provider
   session maps unambiguously to a new reference.

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

The existing Rofi interaction contract remains unchanged:

- Tab and Shift+Tab navigate rows;
- Left and Right switch `Recent`, `Hosts`, and `Providers`;
- Enter drills into a group or opens a session;
- Escape returns one layer and exits at a root; and
- Ctrl+G exits unconditionally.

The callback boundary is fail closed. Configuration, capability/model, and
selection errors render bounded diagnostics without turning a callback failure
into a process crash. Root Escape returns no rows before configuration or
model setup, and nested Escape falls back to the enclosing view root when its
snapshot cannot be loaded. Ctrl+G remains Rofi's native cancel binding and is
not assigned to a script callback.

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

## Configuration migration

The standalone Agent Plus configuration keys move as follows when suite
integration lands:

| Current key | Target owner |
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
argv into Tmux Plus, and only provider-owned keys into Agent Plus. The new
Agent configuration rejects the removed host, SSH, and terminal keys instead
of supporting two authorities. Chezmoi converts the current terminal string to
Tmux Plus's argv-array form; the application does not perform an old-path or
old-schema migration.

The current CLI route and alias overrides remain diagnostic compatibility
tools during the migration. New integration tests exercise contract fixtures
rather than importing owner implementations.

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
  Plus synthesize the same fallback identity: the case-folded short system
  hostname as ID and display, with the full and short hostnames as aliases.
  Malformed or unsupported Host Mesh output is visible and is not silently
  treated as an empty mesh.
- An absent Tmux Plus retains the current standalone Agent Plus engine only
  during the migration window. The final suite does not maintain two generic
  tmux engines indefinitely.
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
4. Run legacy and contract-backed discovery side by side against deterministic
   fixtures and compare host/session identity, activity, and lifecycle
   decisions (open-existing, create, and active-outside-tmux refusal).
   (Complete in P6.)
5. Keep all three public commands and Rofi modes on the managed `PATH` and
   verify local plus remote discovery, focus, create, and resume on each
   intended host. (Complete in P6 through automated and operator acceptance.)
6. Remove legacy host-route and generic tmux code in a later reviewed change
   after the accepted rollback boundary is deliberately retired.

Acceptance requires that the same logical machine has the same host ID and
display label in all three pickers, that a provider session maps to the same
stable tmux reference in both consumers, and that background refreshes do not
alter SSH usage ranking. It also requires that each public command resolves
from the Niri session's `PATH`, stale mesh observations are rejected rather
than merged, and an external tmux rename cannot redirect an open or destructive
action to a different session. P6 additionally requires that malformed
configuration/model/callback data leaves every picker closable: root Escape and
Ctrl+G close unconditionally, while nested Escape returns to a safe root. The
Rofi bindings must preserve native Tab row navigation and must not capture
Ctrl+G as a script callback.

## P6 acceptance and performance follow-up

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

Terminal startup is excluded from those measurements. The Agent path is the
largest target: even a local selection currently waits for remote-host
activity/provider discovery and then a separate all-host Tmux inventory. The
follow-up order is to scope lifecycle refresh to the selected host and run
independent Agent/Tmux discovery concurrently; avoid Tmux Plus's successful
open-path model reload; then revisit SSH Plus preflight or connection reuse
without weakening route fallback or successful-connection history semantics.
These measurements are diagnostic baselines, not an API guarantee or latency
SLA.
