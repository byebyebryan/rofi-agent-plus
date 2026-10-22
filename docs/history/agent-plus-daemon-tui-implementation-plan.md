# Agent Plus direct TUI implementation plan

> Historical record from the October 2026 direct-TUI transition. Commands,
> labels, refresh behavior and completion status below describe that stage.
> Use the [current guides](../README.md) for present behavior.

Date: 2026-10-21

Status: execution plan for an unattended implementation run. Runtime
implementation and native client launches have not started. The
[design](agent-plus-daemon-tui-design.md) defines the product and
the [review](agent-plus-daemon-tui-design-review.md) records established discovery
behavior and the remaining native proofs.

Implement an environment picker with one row per direct TUI window, a launcher
row for an environment with no window, and Open / Open another window actions.
The first supported desktop is Niri with dedicated Kitty windows. Native Codex
and Claude managers own conversations; SSH Plus owns host identity and routing.

## Unattended completion and scope

The agreed unattended deliverable is a reviewable source candidate, a
verified wheel installed into private candidate prefixes, and reproducible
owned native proofs on Starship and Snap. This completion boundary was confirmed
by the user. Final human keyboard/input acceptance, publication and managed
rollout are separate. The implementation run has not started.

The implementation run may revise its internal module layout, fixtures and
small algorithm choices while preserving the product and public boundaries.
It should continue through independent work when one external capability is
unavailable, record that gap, and return to the affected gate when possible.
An external failure is neither an empty inventory nor a passed test.

| Area | Planned implementation scope |
| --- | --- |
| Source | This checkout's package, tests, scripts, CI, examples and current documentation; preserve the existing draft files and unrelated edits |
| Candidate installation | New private immutable prefixes on Starship and Snap; invoke them explicitly without changing normal executable selection |
| Owned fixtures | Private configuration/runtime directories, disposable windows and synthetic conversations belonging to the run; no model response is needed for the required tests |
| Ordinary native environments | Passive discovery and existing configuration/command inspection; use ordinary managers only for an explicitly bounded smoke test with no existing-conversation input or setting changes |
| Repository delivery | Leave a reviewable diff and artifact/evidence paths; commits, push, publication and PR creation follow the actual run instruction rather than being prerequisites for local proof |
| Managed delivery | Prepare reviewable changes only when requested; selecting the candidate, applying chezmoi, restoring Mod+A and changing fleet launcher configuration remain a separate rollout |

Before editing, record the baseline Git revision/status and inventory every
dirty or untracked input. Existing design files are inputs, not disposable
scratch files. If source work is concurrent, use an isolated worktree containing
the intended design snapshot; do not move or clean someone else's work. Recheck
the candidate diff at every completed gate.

Maintain a small progress record under a run-specific evidence directory with
the candidate revision or source digest, completed gates, current failure,
next action and remaining external acceptance. Update it after each gate so a
resumed run can continue without repeating native setup or losing cleanup
ownership. This record is repository/run evidence, not an update to Codex memory.

## Implementation decisions

Build the new path inside this package, using small modules with explicit host
and desktop boundaries. Reuse the current Rofi script-mode launcher and its
one-second timeout mechanism, provider icons, strict JSON helpers and the public
Host Mesh integration. Replace the conversation-specific renderer/model instead
of extending its roughly 4,000-line Rofi module with another operating mode.

The final package has no Observer or Tmux dependency and no legacy runtime
fallback. Preserve the parked installed artifact and its configuration as
rollback inputs. A new release selects the direct-TUI path as its implementation.
Historical documents remain historical records.

| Component | Proposed source responsibility |
| --- | --- |
| Target and row types | New `model.py`; shared errors/constants remain small in `engine.py` |
| Direct configuration | Rewrite `config.py`, add `examples/direct.toml` |
| Native adapters | New `providers/codex.py`, `providers/claude.py` and their common pure records |
| Headless host commands | New `host.py`; installed `rofi-agent-plus-host` entry point and checkout wrapper |
| Host Mesh and bounded subprocesses | Extract reusable pure logic from `contract_backend.py` into `host_mesh.py` and a small process helper |
| Niri and Kitty | New `desktop.py`, implementing only the supported first desktop/terminal pair |
| Registration and action requests | New `registry.py`, scoped by desktop generation and target |
| Foreground TUI or SSH lifetime | New `viewer.py`, launched through the same immutable installed artifact |
| Open orchestration | New `actions.py`, using the registry, desktop and host-entry interfaces |
| Catalog cache and preferences | Rewrite `cache.py` and `view_preferences.py` for new identities and paths |
| Rofi presentation and dispatch | Replace session behavior in `rofi.py` and `app.py`; adapt `launcher.py` |
| Packaging and checks | `pyproject.toml`, CI, source/installed/native scripts and artifact selection metadata |

These are module boundaries, not a plugin framework. Two provider adapters and
one desktop backend are sufficient for the first implementation.

Freeze the following choices before the long implementation run:

| Choice | First implementation decision |
| --- | --- |
| Discovery coverage | Installed defaults plus explicitly declared additional homes; no process census or arbitrary native-home scanner |
| Provider entry | Resolve the installed native executable and enter `codex agents` or `claude agents`; do not execute a discovered wrapper or inventory-supplied command |
| Native overlay profiles | Preserve settings already selected within the target's home; no separate rows or picker actions for `--profile` overlays |
| Local working directory | The owning user's home, independently of the repository from which the picker opened |
| Target specification | Hash canonical launch-relevant fields; labels, titles, status observations, version diagnostics and daemon generations do not invalidate a reference |
| Process ownership | Foreground wrapper/client generations only; daemon and worker descendants have no registration or cleanup authority |
| Failure visibility | New-entry errors remain readable in the owned terminal until dismissal; a completed client is not retained as a live TUI merely to hold its error text |
| Runtime services | Finite picker refresh work plus one lightweight wrapper per TUI; no persistent discovery or reconnect service |
| Catalog refresh | Demand-driven on opening with an expired cache, Alt+R, or entry validation; TTL expiry alone does not start an always-running host poll |
| View refresh | Try the existing Rofi timer; correctness of search/caret/selection takes priority. If that mechanism loses input, use opening/Alt+R snapshots for the first candidate and record the limitation |
| Unsupported layouts | Refuse ambiguous reuse with an explanation; do not grow a tabs/splits/window-management subsystem |
| Native absence | Keep supported stopped targets launchable; show missing/unsupported providers and unknown status distinctly |
| Extra features | Mosh, Ghostty, manual adoption, Close/batch actions and conversation monitoring stay deferred |

The snapshot fallback changes presentation freshness only. Current window
identity and existence are always revalidated before focus; a stale title cannot
select or launch a different client. Record any fallback in the review result
instead of silently weakening a test.

Choose one tested refresh strategy for the candidate rather than shipping a
second configurable mode. If the snapshot fallback is needed, opening/Alt+R
takes a finite bounded inventory snapshot before rendering, retaining partial
results and last-known rows. It does not promise asynchronous catalog redraw
while typing; new facts appear at the next explicit refresh/callback. Record
cold-opening latency and this freshness limit. The preferred timer path keeps
the existing cached first frame and finite asynchronous catalog completion.

## Configuration and references

The new artifact reads `$XDG_CONFIG_HOME/rofi-agent-plus/direct.toml` exclusively,
defaulting to `~/.config/rofi-agent-plus/direct.toml`. Proposed schema 1:

```toml
schema_version = 1

[desktop]
terminal = ["kitty"]
catalog_ttl_seconds = 30

[[environments]]
provider = "codex"
home = "~/.codex"
label = "OSS"

[[environments]]
provider = "codex"
home = "~/.codex-work"
label = "Work"
```

The terminal value is an argument vector for the supported Kitty backend, not a
shell command or arbitrary terminal adapter. Reject options that override the
instance marker, share an existing terminal process, attach to another instance
or replace the intended foreground command. Qualify dedicated-window behavior
against the actual Kitty startup configuration in Step 0.

Installed providers contribute their default environment without configuration.
A declaration for the same canonical default changes its label rather than
adding a duplicate. Additional homes require declarations and must already be
prepared. Resolve `~` using the owning host/user; do not evaluate shell variables
or source shell startup files. Reject conflicting declarations for one scope.
Authentication and ordinary default-environment first-use prompts stay native.

For an additional Codex home, prepared includes a readable native `config.toml`,
matching the current fleet Work/OSS wrapper precondition. An additional Claude
configuration directory must already exist and pass the adapter's native scope
validation; directory presence alone does not assert authentication readiness.

The host helper reads the environment portion without importing desktop code.
Chezmoi can render these records from the metadata already used to install
Work/OSS launchers. Initial source implementation uses the example or explicit
test configuration; installing fleet declarations is a later managed step.

Use fresh cache, UI-state and runtime schemas under `direct-v1`. The runtime
layout has separate desktop and target directories, with private instance and
request records and a target lock. A malformed record must have a bounded
target-specific effect. Leave the old `config.toml`, cache, preferences and
installed artifact usable by the old release.

Target identity is host ID, owning user scope, provider and canonical native
home/configuration directory. Its launch-specification revision captures entry
selection and environment routing, excluding display labels, observation clocks
and the current daemon PID/socket. Window references add a desktop generation
and random instance ID. Actions resolve those references rather than row text.

The specification covers the provider/adapter entry contract, resolved executable
path, native environment selector and routing-override policy. Do not hash the
entire native configuration or its credentials: ordinary model, hook or native
settings edits are applied by the provider in the same environment. An entry
contract or selector change still invalidates an old launch reference.

Use structured canonical serialization for identity/specification hashes, not
delimiter-joined strings or Python's process-dependent `hash()`. A configuration
label or optional declaration ID is an alias, not another target identity.
Resolve existing homes through host-local canonicalization; treat a missing
default path consistently before and after native first-use creates it.

The owning user scope includes the effective OS user ID and account name. The
helper obtains both locally. On configured hosts, resolve the logical host ID
from the host's own public SSH Plus mesh; a caller cannot override it. Retain
the existing local-only fallback when SSH Plus is absent, and require that its
identity match the desktop's expectation. A configured SSH Plus command that
fails is not permission to substitute a hostname. Snap's differing OS and
logical names are a required regression case.

Freeze the host wire as fixtures in Step 1, including at least these fields:

| Record | Required information |
| --- | --- |
| Inventory envelope | Protocol version, self host/user scope, observation time, provider coverage, bounded target list and structured errors |
| Target | Canonical key fields, display label, launch specification revision, capability/result reason and independent daemon observation |
| Entry reference | Protocol version, expected host/user, target key and launch specification revision; no display text, arbitrary command, credentials or daemon PID |
| Action request | Random request ID, Open/additional action, exact target or instance reference and initiating desktop generation |
| Runtime instance | Random instance ID, stable ordinal, target/specification, request ID, lifecycle state, process generations and marked terminal/window association |

Permit bounded additive diagnostic fields within wire version 1; reject unknown
versions, missing required identity fields, duplicates, wrong types and
oversized payloads. Local runtime state is private to this artifact schema;
malformed files are quarantined within their target scope rather than accepted
as an empty registry. A cache is a display/performance input, never launch
authority. Live window rows remain recoverable independently of its catalog.

Initial budgets are a one-second local refresh cadence, a one-second compositor
request limit, three seconds per passive provider probe, twelve seconds per
host inventory, and twenty seconds per fleet refresh with at most four host
workers. The catalog TTL starts at thirty seconds. Respect tighter SSH Plus
connect policy and propagate the enclosing monotonic deadline instead of
restarting it for every subprocess. Byte/count limits are frozen with the wire
fixtures. Measurements may justify tuning these implementation constants; keep
the bounded/partial-result behavior. An interactive TUI or authentication prompt
has no inventory timeout.

## Step 0 Prove the native and desktop mechanics

Create a bounded proof helper before building the full picker. Use uniquely
marked disposable windows, native environments and synthetic sessions. Keep
ordinary provider homes, daemons and windows outside cleanup ownership. Record
the exact artifact versions and owned references needed for cleanup.

First build the smallest marked terminal/wrapper proof using a controlled
foreground program. This establishes the compositor/process mechanism without
depending on provider authentication. Follow it with real native managers in
separate owned environments wherever setup permits. The proof harness may use
a PTY to exercise native terminal semantics; the shipped viewer adds no extra
PTY renderer. A PTY proof and a real Kitty/Niri proof remain distinct evidence.

Prove these mechanics:

- A dedicated Kitty process starts a lightweight wrapper and one foreground
  client. Niri's live window/application/process identity binds it to the launch
  marker without global Kitty remote control.
- A dynamic title can change while exact focus continues to select that window.
  Test a second window, picker-like process restart, window disappearance and
  an ambiguous extra-window/split layout.
- The wrapper preserves native terminal input, resize, signals and job control
  without adding another PTY renderer. Ctrl+C reaches the native client without
  accidentally dropping registration while that client remains alive.
- Two native manager clients to one disposable environment can select different
  conversations, keep independent sizes/draft input and close independently.
  Check Codex and Claude on hosts where each is installed.
- Native manager entry with no conversation request does not create a new
  conversation. Confirm the selected environment, including Work/OSS separation.

The earlier status checks already establish installed commands, namespace
selection and missing-daemon probe behavior for the recorded versions. Reuse
those findings; repeat only the portions affected by a new artifact or method.
Seeing two Claude managers on one ordinary supervisor is useful baseline
evidence, but the size/draft/exit proof is still needed.

Deliver a concise sanitized proof record. Mechanical compositor observations
and operator input/focus judgment are separate results. A failure of the marked
window or wrapper mechanism must be resolved before building actions on it.

Bound each fixture and record cleanup ownership before starting it. Use the
currently installed provider binary; do not pin or copy an older daemon release
to make a fixture pass. Rebuild the old `scripts/native-fixture` rather than
running it unchanged: it hardcodes prior artifacts, tmux adapters and provider
credential/setup copying. New fixtures must isolate their writes, keep native
authentication material out of reports and use only supported native setup.
An auth/setup prompt is a recorded setup gap, not proof of manager entry. Do not
submit prompts for paid model responses or type into an existing conversation.

Automate focus assertions using the compositor's focused window ID and process
binding, and automate controlled input/resize assertions where the owned
fixture supports them. Leave physical keyboard feel, visual focus and native
notification appearance to the final operator checklist. Their absence does
not prevent implementing the source path. A failed mechanical identity proof
does block the dependent window actions; host/row-model work can still proceed.

## Step 1 Implement the host catalog and native entry

Add the pure target/configuration types, provider adapters and headless command.
Freeze a small versioned host wire and fixtures before the desktop consumes it.

```text
rofi-agent-plus-host inventory --json
rofi-agent-plus-host enter --reference-json REFERENCE
```

The desktop diagnostic command becomes `rofi-agent-plus list --json`, with an
explicit versioned environment/window shape. Retired `active`, session-limit
options and old private worker commands produce clear unsupported-command/input
errors; they do not translate conversations into new target identities.

Inventory emits one bounded JSON envelope with protocol version, the host's own
logical ID and user scope, per-provider coverage, target identities/specification
revisions, launch capabilities and independently observed daemon status. It
returns no arbitrary provider command for the desktop to run. Partial provider
failure preserves valid sibling targets. Missing executables, unsupported
commands and an unavailable daemon remain distinct conditions.

Separate provider installation/version/capability probes from per-environment
status probes. Cache capability results by resolved executable identity and
relevant adapter/configuration revision; never run the same help probe once
per row or on the one-second timer. A complete empty provider result may remove
its old targets; a timeout or partial failure preserves the affected last-known
targets with their original observation time. One bad declaration affects that
environment; a malformed whole configuration gets a host/configuration error.

Discover the default plus declared native homes. Deduplicate aliases; native
`--profile` overlay files do not add daemon targets. Default/declared discovery
is the first guarantee, including with every daemon stopped. Do not add a broad
process/directory census to infer arbitrary custom homes.

Codex status uses the selected home's native daemon-version query. Claude uses
its selected configuration directory's daemon-status query. Normalize the
established successful and missing-daemon paths; unrelated nonzero exits remain
unknown. Daemon-status failure alone does not remove a valid target or prevent
its explicit native launch.

The entry reference contains the expected host/user, target key and launch
specification revision. The helper resolves its own identity and environment
again, then executes `codex agents` or `claude agents` with a real TTY. A native
daemon restart within that environment is allowed. A changed native home,
missing extra environment, wrong host/user, unsupported contract or changed
entry specification refuses before native client startup.

Set the intended native home/configuration directory, resolve the provider
executable and use the owning user's home as the working directory. Remove
conflicting inherited provider endpoint/session overrides using an explicit policy; preserve
ordinary authentication, provider settings and hooks. Test entry from a parent
using the other Codex environment. Do not change native feature flags or install
services to make entry succeed.

Codex entry sets `CODEX_HOME` and clears `CODEX_PLUS_SOCKET`, matching the
inspected fleet launchers. Claude entry sets `CLAUDE_CONFIG_DIR`. Qualify other
native endpoint/session overrides against the installed contract before adding
them to this policy. Do not blanket-remove all provider-prefixed variables and
accidentally drop native terminal notifications, hooks or supported credentials.

Define strict field/byte/count limits and stable host-helper errors with the
fixtures. Inventory stdout remains JSON only. Interactive entry reserves stdout
for the native terminal and reports pre-entry errors through bounded diagnostics
and documented failure outcomes. A newer unsupported helper protocol fails for
that host without invalidating others.

Interactive `enter` need not run another daemon-status query. It validates the
current host/configuration/provider entry contract, sets the intended native
environment and replaces itself with the native manager. Keep errors before
native entry distinguishable from the manager's exit; do not infer remote
connection success from a conventional numeric exit code alone. Fixed command
selection and shell quoting are exercised at the actual SSH boundary, not only
through a mocked argument list.

Acceptance: a headless installed helper works without Rofi, Kitty, Observer or
Tmux; native inventory never starts daemons; the right native manager opens in
the selected environment; malformed or stale references cannot select another
environment. Source tests use provider stubs; native tests independently check
real provider behavior.

## Step 2 Implement local window lifecycle and requests

Implement `desktop.py`, `registry.py`, `viewer.py` and the local portion of
`actions.py` against the mechanism accepted in Step 0.

The durable-for-this-desktop request is written before terminal dispatch. Give
it a random request/instance ID and process-generation ownership. It must live
under the runtime registry, not the dialog's temporary first-frame directory.
The detached dedicated terminal starts the matching artifact's viewer wrapper;
the wrapper records its direct foreground client and waits for that client to
exit. Closing the picker must not own that process's lifetime.

Derive desktop generation from the live compositor/socket and OS process/boot
generation established in Step 0, rather than a reusable display name or picker
PID. Keep this logic in the desktop adapter and test unavailable process evidence
as unknown, not a trusted weak match.

Represent reserved/opening, live and uncertain instances. Remove an instance
after confirmed foreground-client exit, even if a terminal is held open, and
reconcile stale files on later reads. Daemon/worker descendants never keep the
instance alive. Cleanup handles owned files and direct clients only; it does
not terminate a provider process tree.

Serialize first Open per desktop/target and recheck under the lock. If an
instance appeared, focus that sole instance or show the updated individual rows.
Replayed request IDs resolve to their original outcome. Explicit additional
actions use new IDs and may create more windows. A pending launch that is slow
to map prevents accidental duplicate first launches.

Acceptance must cover crashes before spawn, between spawn and registration,
and after registration; immediate picker exit; slow window mapping; repeated
callbacks; wrapper/client exit; PID/window-ID reuse; compositor restart; and
per-target corruption. Missing desktop evidence stays unknown. Release an
uncertain reservation only when its exact processes and marked window are
confirmed absent. An affected record cannot freeze unrelated healthy targets.

Treat dispatch as a state transition around a nontransactional process spawn.
Persist dispatch intent before launching and have the exact viewer acknowledge
its process identity. Request IDs and locks prevent ordinary replay; they do
not make process spawning or remote entry an exactly-once transaction. If a
crash leaves dispatch uncertain, reconcile the unique marker/process/window
evidence and refuse an automatic replacement while it remains unknown. A
separate deliberate additional-window request is still a distinct user intent
when its target and desktop are otherwise valid.

Locks cover only short local record transitions, never SSH/provider probes or
waiting for a window. Bound lock acquisition and surface busy/unknown instead
of hanging a Rofi callback. Fixture crash injection must cover both sides of
every persisted transition. A new desktop generation invalidates old handles;
it does not terminate any surviving native work.

## Step 3 Add fleet catalogs and SSH entry

Extract the public Host Mesh parsing, local fallback identity and bounded
read-only route-selection logic from the existing backend. Retain the canonical
Host Mesh bundle and its provenance. Keep the new modules independent of the
old Observer and tmux inventory/write paths and sibling implementation imports.

Run the host inventory locally or through eligible SSH Plus routes. Validate
the remote helper's self-resolved host/user identity. The Snap check already
showed why the operating-system hostname cannot replace the logical host ID.
Keep last-known target catalogs on partial failures with unchanged observation
age; one failed peer must not delay healthy publication indefinitely.

Before interactive entry, resolve a current Host Mesh route and carry the
bounded target reference to the fixed helper command with correct remote-shell
quoting. Use an SSH TTY for native entry. Noninteractive discovery must not
prompt; an explicit entry may complete SSH authentication in its terminal and
validate the target there. Preserve SSH Plus policy and keep successful-use
history unchanged by background discovery.

For native proofs, stage the candidate host helper at an explicit run-owned
absolute path on Snap and invoke that path through the same transport builder.
Check its package/source digest before entry. This exercises the candidate wire
without replacing the normal helper or changing SSH Plus routes. Ordinary
production entry uses the configured installed helper location; if noninteractive
PATH cannot resolve it, report helper unavailable rather than sourcing arbitrary
shell startup files. Candidate-path injection belongs to the proof harness,
not to untrusted inventory fields.

Pin one interactive dispatch. After it begins, transport uncertainty never
replays the request on another route. A created window is not proof of remote
connection. Track the local SSH client, retire the instance after that client
exits, and keep a stalled but valid window focusable. Agent Plus does not create
an automatic reconnect supervisor.

Acceptance: route fallback for bounded read-only inventory, self-identity
mismatch, absent/incompatible helper, cold/offline host, stale catalog, auth
prompts, shell metacharacters in presentation data, failure before entry and
controlled SSH loss after entry. Verify that uncertainty produces no second
native client launch. Do not reinterpret a command failure as an empty host.

## Step 4 Replace the picker model and renderer

Build the pure row model before replacing presentation. Inputs are target
catalogs, their coverage/freshness and current desktop registrations. Outputs
are stable target/instance rows with human labels and typed action references.

Implement the agreed flat list: stable host/target order, adjacent ordinal
window rows, dynamic titles or `Window N`, and a launcher row only when no
instance exists. Keep registered windows visible when their target is offline
or removed, with focus based on local evidence and new entry validated separately.

Adapt `app.py`, `rofi.py`, `launcher.py` and new preference state. Keep the
launcher's single first frame and supported `-selected-row` mechanism. Replace
the old Resume/Close/New cycle, host-page bindings and batch callbacks with
Open / Open another window, native search-cursor bindings and Alt+R. Status
rows are nonselectable, typed text only filters, and titles never enter actions.

Use the existing one-second script-mode timeout for bounded local window and
registry refresh. It performs no provider or SSH queries. Startup/Alt+R/new
entry use separate finite catalog work. Preserve search, action and selection
by identity when rows change, including during title filtering. Picker-owned
read helpers stop when the dialog exits; launched viewers continue independently.

An accepted action consumes its request ID once. Refreshes and repeated
callbacks cannot issue another launch. A new deliberate additional action gets
a fresh ID. A stale selected window causes a refresh/notice, not a new window.

Acceptance: all row transitions, multiple independent instances, stable numbers,
search and selection preservation, initial selection, title changes while
typing, native cancel, action cycling, refresh completion and errors. Confirm
the installed Rofi's timeout behavior and bounded update latency visually; a
synthetic script render is not that proof.

Implement the snapshot fallback fixed above if the installed timeout callback cannot
preserve typing/caret behavior. Do not add a native Rofi plugin, X11 keyboard
injection dependency or persistent UI service to rescue automatic title updates.
Measure one open/idle/typing/closed cycle using the same candidate artifact.
At idle, provider/SSH call counts stay flat; closing the dialog ends refresh
children while all accepted windows remain usable.

## Step 5 Complete the package and acceptance

Remove the old Observer dependency from `pyproject.toml` and the artifact-download
step from CI/bootstrap tooling. Install this package and its development tools
normally in a clean environment. Add the headless helper and private viewer
entry wiring to the same artifact, with no GUI import at helper startup.

Retire runtime modules `observer_contract.py`, `observer_write.py`,
`session_identity.py`, `contract_lifecycle.py`, `contract_viewers.py`,
`viewer_state.py` and `batch.py`, plus the unused session portions of the old
backend/cache/renderer. Replace their obsolete required tests and script gates
with the new contracts' meaningful checks. Retain historical evidence and any
referenced contract bundles as documentation, without shipping or testing them
as active tmux/provider requirements.

Update `scripts/check`, contract-sync/public-contract checks, installed-artifact
verification and native fixture tooling. Retain generic private-file/text/shell
guards. Prove the final wheel in an environment with no Observer/Tmux packages
or commands, rather than relying solely on import-name searches. All provider
calls in ordinary CI use controlled stubs; native fixtures are a distinct gate.

Package acceptance requires installed entry points to resolve sibling helpers
from the same immutable artifact, source/installed byte provenance, headless
host operation, rejected old config/cache shapes and no legacy fallback. Prepare
new selection/rollback metadata instead of reinterpreting the Observer-native
selection record.

Run the complete native matrix on the candidate: local Codex on Starship, both
Codex homes and Claude on Snap, Starship-to-Snap entry, and independent desktop
windows to the same target. Check resize, modified keys, paste, scroll, terminal
notifications/hooks, native auth/trust and client closure. Record ordinary
provider observations, owned fixture results and human visual/input acceptance
separately. Review picker-open resource cost and ensure its local timer work
ceases when closed.

Rewrite the current README and suite ownership around the accepted new behavior;
leave historical studies and prior release evidence in their original scope.
A source gate or wheel install does not establish managed desktop acceptance.

## Implementation checkpoints

Use these dependency boundaries to organize the work, without creating a PR or
public release for every checkpoint:

| Checkpoint | Depends on | Reviewable result |
| --- | --- | --- |
| Mechanics | Current desktop access | Owned marked-window identity/focus/lifetime proof; record provider setup separately |
| Host contract | Pure types/configuration and controlled provider probes | Frozen wire fixtures, working headless entry point and native routing policy |
| Window actions | Accepted mechanical identity proof and host contract | Local registry/viewer/actions with race/crash recovery checks |
| Fleet entry | Host contract and window actions | Candidate helper on Snap, public SSH Plus integration and remote entry/failure cases |
| Picker cutover | Working window actions and fleet catalogs | New rows/actions/refresh, replaced entry points and updated current documentation |
| Candidate acceptance | Coherent new source path | Retired dependencies/tooling removed, full source gate and clean installed wheel, required native cases and cleanup |

The controlled window portion of Step 0 gates dependent desktop work. Provider
multi-client/input cases can finish alongside later integration where setup or
automation requires it; keep them pending until actually proved. Host and pure
row-model work do not depend on a working compositor.

The old source gates assert the retired architecture. During construction, run
targeted new checks and record their scope. Cut over source entry points, remove
obsolete dependencies/tests and replace the required check scripts as one
coherent candidate before claiming the full gate passed. Do not add permanent
dual-mode compatibility solely to keep intermediate legacy tests green. The
parked installed artifact provides rollback throughout source development.

## Candidate gates and test entry points

Implement these entry points as part of the replacement, keeping their default
behavior finite and their fixture ownership explicit. The arguments below are
the proposed new interfaces, not commands supported by the parked release.

```text
scripts/check
scripts/check-installed-artifact --candidate-prefix PATH --wheel PATH --output DIR
scripts/check-native-entry --candidate-prefix PATH --peer snap --peer-candidate-prefix PATH --output DIR
```

The source gate is self-contained: unit/integration tests, Ruff, shell/text
guards and checksums/provenance for the public Host Mesh bundle. It needs no
live provider, sibling checkout, GUI, SSH server or Observer artifact. Keep an
explicit producer-sync check separate from this gate; an unrelated dirty
sibling must not force source edits or invalidate the vendored released wire.

The artifact gate builds/installs a real wheel, then runs outside the checkout
with isolated Python imports. Verify installed entry points, package metadata,
wheel/file digests, assets and matching helper/viewer resolution. Exercise the
headless helper with a minimal command environment and controlled providers;
make accidental Observer/Tmux access fail explicitly. An editable install or
checkout import does not satisfy this gate.

The native entry gate supports selecting/resuming individual owned cases from
its manifest, with deadlines and cleanup on failure. It records command/artifact
versions and distinguishes real compositor observations, controlled PTY input
and operator judgment. It never relies on hardcoded prior release prefixes.

| Gate | Required candidate evidence | External acceptance |
| --- | --- | --- |
| Host/configuration | Canonical aliases, default/extra-home behavior, label-only changes, partial failures, zero conversations, all daemons stopped, no daemon start on inventory, strict wire and stale-reference refusal | Real installed provider probes supplement controlled cases |
| Local lifecycle | Real marked Kitty/Niri focus and disappearance; controlled duplicate/race/crash/PID-window-generation cases; exit cleanup and picker-independent lifetime | Physical keyboard/focus judgment remains pending |
| Remote transport | Installed candidate helper on Snap, self-identity and both Codex environments, real SSH entry, shell-boundary cases and owned disconnect with no replay | Interactive account setup requiring the user is recorded separately |
| Multi-client | Two owned native clients per supported provider, independent selection/resize/draft/exit where controllable; no implicit conversation creation | Any unautomated native input behavior has an explicit pending checklist item |
| Picker | Pure row/action transitions, actual installed Rofi startup/callback behavior, local-only refresh call counts, title/fallback behavior and closed-dialog cleanup | Human search/caret/key/paste/scroll and visual presentation |
| Artifact | Clean wheel install with no Observer/Tmux runtime dependency, headless imports, matching immutable helpers and source/file provenance | Managed selection is outside candidate acceptance |

Keep a mapping from retired active tests/gates to their replacement coverage or
obsolete product responsibility. Removing session lifecycle tests is expected;
removing a generic quoting, byte-limit, private-file or launch-lifetime guarantee
requires replacement coverage. Do not satisfy completion with blanket skips,
weakened assertions or a source check that simply stops exercising the new path.

Start a new full pass only after a coherent candidate or a relevant fix. During
implementation, run affected gates; after the complete source/artifact pass,
repeat only the native cases invalidated by changed code or observed failures.
Changing the wire, wrapper or transport invalidates its downstream receipts.
Changing documentation alone does not require reopening native clients.

## Recovery and handoff

Use the following responses to common unattended failures:

| Failure | Required response |
| --- | --- |
| One provider or host is unavailable | Preserve its last-known targets/coverage, finish independent source work and record the missing native case; do not substitute a sibling provider's evidence |
| Native login or trust requires user input | Leave a bounded setup result and stop that fixture; continue other work without inventing credentials or bypassing native prompts |
| Niri/window association is ambiguous | Diagnose the owned proof, preserve unknown presence and block dependent focus; do not select a window by its title |
| Rofi timer changes or drops search input | Use the declared snapshot/Alt+R fallback, then verify current existence at action time; document the freshness limitation |
| Dispatch/SSH outcome is uncertain | Reconcile the original request, never issue another automatic interactive dispatch |
| Test cleanup cannot prove ownership | Retain the private fixture/manifest and report the exact remainder; do not broaden cleanup to ordinary clients or daemons |
| Existing source/tool failure repeats | Capture the smallest reproducer and change the diagnosis before rerunning; do independent work rather than looping on identical expensive commands |
| Required native gate cannot run | Leave it unavailable and the native qualification incomplete; never replace it with a mock result labeled native |

Cleanup checks the run ID, exact owned path and recorded process birth identity.
For isolated provider daemons created by a fixture, use native scoped shutdown
only after clients are gone and ownership is revalidated. Preserve the manifest
until cleanup is confirmed. Ordinary daemon generations, native configuration
and existing windows must remain outside the fixture's cleanup set.

The final handoff includes the source diff/revision or digest, candidate wheel
and installation paths/digests, gate commands/results, sanitized native case
receipts, preserved ordinary-state observations, cleanup status and the short
human acceptance checklist. Store large/raw terminal captures privately only
when needed for a failing owned case; committed evidence contains no transcripts,
credentials or ordinary window titles. Gate outcomes are pass, fail, unavailable,
pending human or not run; keep the latter three distinct.

The unattended objective is complete only when the required source/artifact
gates pass, the new path is usable from the candidate artifact, and mandatory
owned native mechanical/entry cases on Starship and Snap pass. A source-only
candidate is an intermediate milestone. Unavailable required cases leave an
explicit incomplete objective, even if a useful candidate is ready for review.
Human-only acceptance and managed rollout are excluded from that objective
rather than implicitly counted as successful.

## Step 6 Prepare managed delivery separately

Prepare an immutable prerelease and scoped chezmoi changes for the helper/UI
installation, `direct.toml` declarations, selection metadata and final desktop
entry. Use the existing per-host environment metadata for Work/OSS labels and
homes. Remote hosts receive the headless helper; graphical hosts also receive
the launcher configuration.

Keep publication, push and managed apply within their separately authorized
scope. Mod+A remains parked until the approved rollout selects the accepted
artifact and binding. Review the exact managed status/diff/dry-run and preserve
unrelated drift. No provider daemon, startup policy or existing native client
needs to be restarted as part of installing this helper.

Rollback restores the prior picker selection/binding and its preserved config.
Existing direct TUI windows continue through their own wrappers; retain their
immutable artifact while those wrappers are alive. Removing a picker selection
does not close terminals or terminate provider work. Installed host-helper,
desktop, graphical/input and rollback acceptance remain separate receipts.

## Completion boundaries

| Boundary | Completion means |
| --- | --- |
| Mechanics | Marked windows and native concurrent-manager behavior are established in bounded owned proofs |
| Host helper | Exact installed environments are discovered and entered through an accepted headless contract |
| Window lifecycle | Existing windows are focused correctly, first opens are coalesced and explicit additional clients remain independent |
| Remote path | Host/profile identity and SSH failure behavior pass independently of local success |
| Picker | Every window is selectable directly with the two agreed actions and stable refresh behavior |
| Package | Clean artifact installs need no Observer/Tmux dependency and pass the new source/installed/native checks |
| Delivery | The explicitly selected hosts and desktop entry pass managed, visual/input and rollback acceptance |

Start implementation with Step 0 and follow the checkpoint dependencies above;
reopen only affected boundaries when new evidence requires a change. Mosh,
Ghostty, arbitrary manual-TUI adoption, picker
Close/batch actions and conversation monitoring remain deferred.

## Suggested unattended goal instruction

Implement the reviewed direct-TUI Agent Plus design using this plan. Deliver a
reviewable source candidate and a verified immutable wheel, including the
headless host helper, Niri/Kitty window lifecycle, SSH Plus remote entry and the
per-window Rofi picker. Replace the retired Observer/tmux/session path and its
active tooling. Preserve current dirty design inputs and unrelated work.

Include bounded owned native proofs on Starship and Snap, using explicit private
candidate prefixes and disposable windows/environments. Finish all available
automated acceptance and cleanup; record human-only acceptance separately.
Keep the parked installation, normal executable selection, provider settings,
ordinary work and desktop binding intact. Return the candidate, evidence,
remaining required gaps and rollout preparation notes. Do not push, publish or
apply managed changes as part of this goal.
