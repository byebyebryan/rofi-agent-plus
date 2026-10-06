# Observer a11 consumer integration and usability pass

Read the later [client handoff](observer-client-handoff.md) first. The subsequent
Observer review found provider upgrades and producer watch/contract gaps after
this plan's healthy baseline. Fixture-based client work can proceed separately;
current native action acceptance, repaired watch integration and rollout wait
for the relevant independently accepted Observer repair. The evidence below
retains its original sampling time and image bounds.

The later support decision uses required native contracts and observed
capabilities instead of provider release/hash allowlists. The handoff and
Observer's `docs/provider-contract-compatibility-plan.md` define this change;
the provider versions in this plan are evidence provenance, not ongoing pins.

Date: 2026-10-06. Status: planning and read-only baseline validated; implementation
and rollout remain separate checkpoints. This continues the existing
[v2 integration](observer-v2-execution.md) and
[operational proof](native-operational-proof.md).

The outcome is a usable ordinary picker across Snap and Starship: trustworthy
names, ages and ordering; understandable runtime/phase labels; and reliable
single New/Resume with visible failures. Agent Observer stays a frozen producer
during this consumer pass. Any producer defect gets its own Observer checkpoint
and independent acceptance before frontend work resumes against a new artifact.

## Baseline and boundaries

Observer checkout `7e0cbbe` is clean. Its independently accepted producer is
`0.2.0a11`, frozen source `06af32dc9f28aa5d356d6da6c0235ccc1056dac2`, wheel SHA-256
`999d45171adfc4c4e62fe57b417614c0814de2a556693a6d23bb97b45a0a8b65`.
Normal read/write links select that wheel on Snap and Starship. Observer's
`docs/evidence/2026-10-05-stopped-runtime/REPORT.md` records its native gates.

Plus checkout `b57b6ab` is clean at entry. Selected Plus is `0.14.0a1`, frozen
source `90e5b4fa49bace6d4d1d09be833f88faadea0246`, wheel SHA-256
`8ba19a0eba87944aec2848542fb3ddcb91f3ccc33790ba42565928e18daa497b`.
Its pure contract dependency remains Observer `0.2.0a3`. Read/watch wire version
is 2, write version is 1 and Plus cache version is 7. Those public schemas already
accept a11. Keep the reader pin unless an independently accepted contract change
requires a different validator; collector upgrades alone do not require it.

Reinspect installed artifacts at execution. The planning read verified Snap's
Codex CLI 0.160.0 and Claude 2.1.289 hashes against the accepted manifest.
Independent CLI/native evaluation identified the exact accepted Codex 0.160.1
managed images on both hosts. Codex is the leading delivery target on both;
Claude is required on Snap, including entry from Starship. OpenCode stays removed.

The first-party write client owns native route preparation, validation and entry.
Plus owns presentation, Host Mesh routing and selected-reference validation;
Tmux Plus owns terminal/viewer lifecycle. Current conversation-to-viewer binding
is unsupported. Title, cwd, PID and historical tmux options grant no focus,
Close or batch authority. Networking stays outside Observer core; retain existing
Host Mesh composition. Native push/hooks are not prerequisites for this pass.

## Fresh evidence and gaps

Planning ran no provider actions. Installed Observer CLI/native comparisons used
ordinary sessions; installed Plus refresh used private cache and UI-state roots.
The sampled inventories are observations at different times, not atomic joins.

| Evidence | Observed result | Implication |
| --- | --- | --- |
| Independent Observer CLI/native reference | Snap: 51 Codex and 31 Claude rows; Starship: 96 Codex rows. Source health current; no errors or proved mismatches. | The accepted producer is suitable for a separate consumer pass. |
| Native limitations during sampling | One known Codex child has systemError/unknown phase. A Claude timestamp changed during sampling; one transient live Claude row had no proved conversation age and later disappeared. | Retain explicit unknowns and distinguish sampling changes from defects. |
| Ordinary Plus refresh | No errors on either host. Snap: 79 rows, Starship: 90 after known-child filtering; flattened display has the configured 40-row cap. | Basic transport/contract ingestion works with selected a11. |
| Plus versus subsequent Observer snapshot | No missing rows. Starship identities/metadata/state/age match; Snap differs only by the transient row and a changing conversation timestamp. | Revalidate dynamic cases with bracketed samples rather than assuming every difference is a bug. |
| Age projection and renderer | Known age reaches 78 Snap rows and 90 Starship rows. The stopped recap rows render 1d; a distinct newer f2 identity renders 15h. | The earlier missing-age issue is no longer reproduced in this private fresh refresh. Ordinary desktop/cache acceptance is still needed. |
| Runtime presentation | Current parked Claude rows render Phase unknown because the UI reads phase without first considering runtime. | Confirmed consumer defect: display inactive runtime clearly. |
| Cache classification risk | Backend filters confirmed children before cache merge. Controlled partial-inventory input retains a previously visible row when its fresh row has been filtered. | Add explicit unknown-to-child regression and ensure fresh exclusion cannot be undone by partial retention. This is controlled evidence, not a proved native child transition today. |
| Silent Codex Resume | User previously observed nothing visibly happening. Read-only planning did not dispatch an action. | Root cause remains open; reproduce through the complete consumer/terminal path. |

Duplicate titles are expected for distinct Claude saved-resume identities. Do not
merge them; distinguish them using age and a display-only short identity where
otherwise ambiguous. Unknown conversation classification remains visible until
positive provider evidence establishes a child.

## Execution sequence and ownership

| Checkpoint | Consumer work | Exit |
| --- | --- | --- |
| F0: exact baseline | Record current producer, reader, frontend, provider images, configured host/provider scopes, entry environments and managed links. Bracket native/Observer/Plus comparisons using private state. | Exact tuple and finite gaps recorded; no ordinary actions. Planning completed the initial baseline; execution rechecks drift. |
| F1: discovery and presentation | Verify full-reference/title/age projection, attention ordering, display caps and pages; fix runtime labels and classification/cache exclusions. | Public-fixture tests and installed comparisons pass; active sessions survive caps, healthy sibling feeds survive outages, and current child exclusions survive partial refreshes. |
| F2: single New/Resume | Reproduce silent Resume, trace the public read/write/Tmux/TTY stages, then fix consumer defects and failure feedback. | Controlled failures and the owned native matrix below pass with one provider dispatch and preserved identities/effects. |
| F3: frozen consumer candidate | Review, commit scoped checkpoints, build a new Plus wheel, install unselected on both hosts, verify wheel/module/schema/dependency bytes outside checkout. | Packaged headless and native consumer gates pass against unchanged a11; owned test contexts and borrowed auth removed. |
| F4: ordinary pilot acceptance | Review the compatible managed tuple and scoped selector/config changes; retain old artifacts and guarded rollback. Select the accepted Plus candidate, then validate the normal launcher and desktop workflow. | Selected CLI/cache/launcher results match; actual terminal placement, trust handling and failure UX are accepted on the user's desktop. |

F1 owns `observer_contract.py`, `cache.py`, provider-event handling in
`contract_backend.py`, picker rendering in `rofi.py` and their tests. F2 owns
`observer_write.py`, `contract_lifecycle.py`, the write path in
`contract_backend.py`, selection callbacks and native consumer proof helpers.
These shared modules are edited sequentially. F3 owns packaging, public fixtures,
consumer evidence and docs. F4's managed configuration lives in chezmoi and has
its own review/apply/rollback gate. No Observer implementation is edited here.

For development and installed proofs, invoke Plus with its explicit interpreter
or executable while preserving the separately selected Observer CLI resolution.
The pure reader dependency also installs Observer console scripts into Plus's
environment: blindly putting that environment first in PATH can select a3
collectors/writers. Record resolved read/write paths and versions in local,
SSH and deferred TTY environments. This is a required environment check, not a
diagnosed cause of the user's earlier failure.

## F1 behavior and meaningful regressions

- Current parked runtime displays Inactive; running runtime uses the proved
  working/blocked/waiting phase, or Running with an explicit unknown phase.
  Unknown or stale runtime must not become inactive. Source health remains
  separate from agent status; completed outcome does not imply inactive runtime.
- Show real conversation age and retain attention order blocked, waiting,
  working, unknown, followed by newest activity and full identity. A liveness
  refresh, rename, creation or resume never supplies a replacement age. Preserve
  original evidence clocks during partial feeds and recovery.
- Keep Active uncapped for current running rows. Validate All/Local/host pages,
  current display caps, duplicate titles, filter and selection preservation.
  Caps or missing viewer bindings must not masquerade as missing discovery.
- Reconcile current child classification before presentation filtering can be
  undone by retained history. Test unknown-to-child and user-to-child under
  partial saved coverage, repeated refresh and recovery; unknown rows stay visible.
- Test provider and host outages independently, healthy sibling preservation,
  stale-to-current recovery and visible errors. Clear an obsolete error after a
  successful corresponding source refresh without renewing stale facts.

Use existing bounded snapshot refresh for the first usable candidate. Measure
convergence while the picker stays open and state its actual refresh bound.
Integrating sampled watch can follow as a separate consumer optimization; it
does not need new provider hooks or Observer networking.

## F2 action proof and visibility

Trace selection revalidation, read refresh, write prepare/execute, viewer create,
terminal open, native TTY entry and early exit separately. Keep bounded stage,
identity, reason, timing and effect metadata; no prompts, responses, tool output,
terminal captures, credentials or raw provider payloads become evidence.

A returned viewer-creation or terminal-launch request is not proof of successful
native attachment. Diagnose the existing public Tmux result/deferred-entry
boundary and reproduce early entry failure before choosing a fix. When the
current public interface cannot expose a needed launch result, open a separate
Tmux producer checkpoint; do not inspect private terminal internals from Plus.
Likewise an Observer defect reopens Observer independently, then freezes a new
accepted artifact before this consumer resumes.

The picker must visibly report pre-dispatch rejection, transport uncertainty,
confirmed provider effect with failed presentation, and terminal/native entry
failure to the extent public evidence establishes them. Preserve the selected
row and a useful error; do not silently close as though an attachment completed.
After a possible dispatch, no route fallback or automatic New/Resume retry occurs.
Native trust and permission dialogs remain interactive provider decisions.

| Owning host/provider | Entry location | Required owned native cases |
| --- | --- | --- |
| Snap Codex | Snap local and Starship remote | New Here, exact Resume of loaded and saved threads |
| Starship Codex | Starship local and Snap remote | New Here, exact Resume of loaded and saved threads |
| Snap Claude | Snap local and Starship remote | New Here, live job attach, parked/saved Resume with requested/resulting UUIDs preserved |

Use disposable isolated stores/endpoints and independently verified native
identity/state for these actions. Exercise real public SSH/Tmux delegation;
controlled transport/PTY adapters remain labeled and cannot certify ordinary
desktop focus or trust UX. Never resume the current coordinator in parallel.
Test stale references, changed cwd/config, unavailable executable, denied or
unavailable terminal launch, delayed/failed entry, timeout/marker loss and typed
name collision. Confirmed provider work survives failed viewer presentation.
Only owned test clients, wrappers and provider contexts are cleaned up.

## Review, packaging and rollout gates

Run Plus's `./scripts/check` with the accepted reader installed, released public
bundle verification, focused cache/renderer/callback/action regressions and
installed public fixture conformance. Update the runtime documentation to keep
selected a11 producer, pinned a3 validator and new Plus artifact distinct.
Keep cache v7 unless an actual persisted-shape/meaning change requires a deliberate
cutover; test warm existing cache and cold private cache in either case.

Freeze exact source revision, package version and wheel hash. Test the installed
candidate outside both source trees, configured host/provider scopes and actual
read/write resolution. Compare native truth to Observer first, then Observer to
Plus full per-host rows, then rendered picker frames. A pure schema check does
not certify field projection, terminal entry or graphical acceptance.

Selection is a distinct checkpoint after F3. Preserve a11 Observer and previous
Plus artifacts. Render/diff/dry-run only reviewed managed selectors/config;
preserve Starship's unrelated source changes and Snap's existing unrelated
external-archive failure. Normal refresh, warm cache and launcher checks follow.
Do not publish packages, broaden fleet rollout or restart ordinary agents as
an inferred consequence of passing consumer source tests.

Desktop acceptance checks age/name/state, pages/filter/selection, one local and
remote Codex New/Resume and Snap Claude New/live/saved Resume, native trust prompts
and visible failures. It proves actual TUI entry and placement; current viewer
binding/focus reuse/Close/batch actions retain their separate proof requirements.
Physical sleep/wake, public release, hosted CI and optional watch/network work
remain separate gates.

Planning validation passed Plus's full existing gate: 312 tests, Ruff lint and
format, shell/text checks and released public bundle verification. The README
and plan's nine local links and fenced blocks pass. These checks validate the
planning checkpoint and current baseline, not the proposed implementation or
new native action acceptance. Only consumer documentation changed in this pass.
