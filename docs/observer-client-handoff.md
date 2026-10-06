# Observer client handoff

Date: 2026-10-06. Scope: a separate Agent Plus agent owns client work after the
Observer review. No frontend implementation or deployment was performed in
this pass. Read this before the earlier
[a11 consumer execution plan](observer-a11-integration-plan.md); that plan's
healthy provider baseline predates the upgrades found below.

The subsequent user decision sets native support by required provider contracts
and observed capabilities. Observer's
`docs/provider-contract-compatibility-plan.md` governs replacement of its current
release/hash allowlists. Provider versions remain diagnostic/test provenance.
Plus must consume contract/capability outcomes without imposing its own provider
release pins or requiring daily release registration.

## Start with the current producer boundary

Observer's `docs/discovery-monitoring-contract-review.md`, in the adjacent
`../agent-observer` checkout,
records eight findings and their evidence. Its identity/state model is usable as
a pinned prerelease interface; stable contract publication is pending. Observer
owns producer repairs and current native acceptance. Do not edit its collectors,
schemas or write internals to make a Plus change pass. Return a producer defect
as an exact public-input/output reproducer for a separate Observer checkpoint.

| Component | Current pinned baseline |
| --- | --- |
| Observer producer | `0.2.0a11`; frozen source `06af32dc9f28aa5d356d6da6c0235ccc1056dac2`; SHA-256 `999d45171adfc4c4e62fe57b417614c0814de2a556693a6d23bb97b45a0a8b65` |
| Producer prefix on both hosts | `/home/bryan/.local/share/agent-observer/0.2.0a11-999d45171adfc4c4` |
| Selected Plus | `0.14.0a1`; source `90e5b4fa49bace6d4d1d09be833f88faadea0246`; SHA-256 `8ba19a0eba87944aec2848542fb3ddcb91f3ccc33790ba42565928e18daa497b` |
| Plus pure validator dependency | Observer `0.2.0a3`; SHA-256 `6aa793e6a0ba5ae37403f73999ea6e6d56bc8c21e5e4c7a10ac52cb946a45a3f`; all five bundled schemas match a11 |
| Wire/cache | Snapshot/watch 2; write request/plan/result 1; current Plus cache 7 |
| Host/provider scope | Snap: Codex and Claude; Starship: Codex. Codex leads delivery; Claude remains required. OpenCode stays removed. |

At review entry Plus source is `b57b6ab`. README and the a11 planning document
already contain uncommitted documentation from the previous planning pass;
preserve them and unrelated changes. Reinspect Git, selected links, receipts,
versions and hashes at execution. This note is a dated baseline, not a runtime
attestation or authorization for broad managed rollout.

## Current operational blocks

Installed Codex CLI is now 0.160.1 on both hosts; a11 accepts that exact image
as a managed peer, but only 0.160.0 as a CLI entry executable. Snap Claude CLI
is now 2.1.291; a11 accepts 2.1.289 and a bounded older-worker subset. Passive
New preparation currently rejects all three host/provider combinations with
`runtime_artifact_not_accepted`. This is an Observer native capability gate,
not a frontend condition to bypass.

Fresh comparison: Snap has 51 Codex rows, 6 loaded contexts and 3 known children;
Starship has 96 Codex rows, 8 loaded and 6 known children. Codex identities,
phases and conversation clocks match within documented limits. Snap Claude
still discovers 30 saved rows with ages, but runtime/phase are unavailable after
the installed image upgrade. Three accepted-image workers remain alive; an
additional new-image worker shares a UUID with one of them. That duplicate
context is unresolved. Unknown runtime must not appear inactive.

Observer watch has controlled age-loss, child-exclusion and retained-row bound
findings. Its parser rejects a valid 1,024-row fixture below the byte limit;
write-result validation also needs cross-object identity checks. The independent
native reference needs explicit unsupported-worker/duplicate-context diagnostics.
Read the linked review before interpreting a comparison script's exit code.

Producer repair, contract-based adapter/write G2 acceptance and compatible artifact
selection are separate checkpoints. Fixture-based Plus UI/cache/error work can
proceed against a11. Native action acceptance on current images, reliance on
repaired watch behavior, and rollout wait for the relevant Observer gate.

## Public inputs and semantics

Use Observer's `docs/public-contract-v2.md` and `docs/write-client-contract.md`
for snapshot/watch v2 and write v1. Export schemas
from the pinned executables. Plus may import pure `bounded_json`, `contract`
and `write_contract` validators as it does today; it must not import collectors,
private provider stores, native artifact registrations or write execution code.
Doctor/human output is diagnostic; consume the versioned snapshot for typed rows.

| Data | Client rule |
| --- | --- |
| `identity` | Preserve all five fields: host scope, provider, namespace, native ID kind, full native ID. Match caller authority to the reached host route. Never join by title, short ID, cwd or project key. |
| `nativeIds` | Observer preserves Codex thread/session distinction and Claude UUID/job short ID. Submit the public reference to the writer; do not construct provider argv. |
| `title`, `kind` | Display native title; duplicate titles are valid. Hide only `kind=child`; unknown stays visible. Apply fresh classification/exclusions before cache retention can restore older rows. |
| `runtime`, `phase` | Current parked may display Inactive; current running displays its proved working/blocked/waiting phase, or Running with unknown phase. Stale/unavailable/unsupported/ambiguous runtime remains explicit. |
| `worker`, `attachment`, `outcome` | Independent evidence, not alternate runtime/phase values. A completed turn can remain running/waiting. Attachment does not bind the current viewer to a conversation. |
| `activity.at` | Unix milliseconds of last native conversation activity; nullable. Age is derived from it, never creation, file mtime, collection, rename or resume. Retained historical age must be labeled separately from current public evidence. |
| Ordering | Blocked, Waiting, Working, Unknown; then known conversation activity descending; then complete identity. The CLI's created ordering is an explicit alternate. Keep active rows separate from history display caps. |
| `cwd`, `workspace` | Recorded provider launch/session context with provenance; not latest tool cwd or action authority. Root key/relative path and Git context describe local project context. Only explicit project mappings join projects across hosts. |
| Source health and coverage | Inspect each provider, saved/runtime scope and each row dimension. Current source is not exhaustive coverage. A failed provider/host must preserve healthy siblings. Missing partial rows cannot establish stopped/deleted/parked. |
| Reasons/capabilities | Keep unknown finite codes visible as diagnostics. Do not map an unrecognized reason to idle, add private fallback discovery or assume a capability supplies a known fact for every row. |

Public v2 validation rejects a nonnull activity timestamp marked stale. Plus's
internal retained display state may keep historical recency, but it is not a
fresh public snapshot and must not be sent back as action evidence. Observer
watch currently clears that timestamp after a gap; do not silently invent it.
If the producer repair changes a schema/domain, update reader/cache compatibility
only after the separately accepted versioned transition.

Current Claude source capabilities still advertise adapter phases/running when
the installed image is unaccepted. Check coverage and per-dimension health;
capability lists alone are not current-image or native action readiness proof.
Claude runtime-only helper ancestry also remains unknown, so the producer does
not guarantee exclusion of every Claude helper context.

After the Observer compatibility repair, a changed provider version/hash with
unchanged required contracts must remain usable. A genuinely incompatible
required method, field or semantic predicate affects the relevant capability,
with a visible reason and healthy sibling preservation. Executable replacement
between write prepare and enter still invalidates the old plan; a fresh compatible
plan is allowed. Native provider release changes do not themselves require a
Plus cache/schema reset. Observer wire changes retain their versioned reader gate.

## Read transport and environment

Observer is host-local. Plus retains Host Mesh identity/routes, SSH policy,
deadlines, partial-host cache and UI; optional Observer networking remains
deferred. Call the public CLI on the owning host with explicit scope/provider
selection. Start with snapshot refresh for this consumer pass.

```sh
cd /tmp
/home/bryan/.local/bin/agent-observer snapshot --host-scope snap --provider codex --provider claude
/home/bryan/.local/bin/agent-observer list --host-scope snap --json
/home/bryan/.local/bin/agent-observer doctor --host-scope snap
ssh starship /home/bryan/.local/bin/agent-observer snapshot --host-scope starship --provider codex
```

Resolve local, remote and deferred-TTY read/write executables separately. Plus's
Python environment also installs a3 Observer console scripts: putting its `bin`
first in PATH can accidentally choose the old collector/writer. Invoke Plus
explicitly while preserving selected Observer resolution. Record versions and
resolved paths in each entry environment. Schema equality alone does not make
a3's collectors equivalent to a11.

Watch integration may follow after its producer repair. Consume initial/full
change/resync snapshots, retain state on heartbeat, detect revision discontinuity
and new stream epochs, expose gaps, and reconnect with a full snapshot. It is
sampled push; short native transitions can be missed. No hooks, replay guarantee
or shared polling runtime is accepted. Native watch and client transport have
independent limits and failure paths.

## Write transport and failure feedback

Submit exact v1 request JSON to owning-host `agent-observer-write prepare`.
New has provider/config/cwd and a null reference; Resume has the exact refreshed
reference. Preparation is passive and binds executable, settings, directories
and applicable runtime context. Execute and deferred TTY entry are separate
deliberate stages, each revalidating context.

Interpret status/effect/identities/handoff together. Prepared is not a completed
provider action. Confirmed creation survives a later presentation failure.
Uncertain effect must never redispatch New/Resume, retry another route or stop
provider work. Claude saved Resume may produce a different UUID; retain requested
and resulting identities separately. A pending new identity is not inferred from
title, cwd or the last row seen.

Tmux Plus owns viewer creation/opening and terminal lifecycle. Its deferred
command remains the public writer's `enter --plan-json`. Make failures visible
across refresh, prepare, execute, terminal creation/open and delayed TTY entry;
an early successful viewer handoff does not prove the eventual provider entry.
The user's earlier silent Codex Resume remains unreproduced. New image rejection
is a current independent block, not an established explanation of that incident.

Current viewer binding, conversation-specific focus/Close and group actions
remain disabled. Historical tmux names/options and attached-client counts grant
no selected-conversation authority. Generic Tmux Plus management stays separate.

## Suggested client checkpoints and acceptance

1. Recheck the exact producer/reader/frontend/provider tuple and separate public
   read transport from native action readiness. Use private cache/UI-state roots
   for side-by-side checks; do not replace normal artifacts during development.
2. Fix parked/running/unknown labels, conversation age/order, duplicate-title
   distinction and child/cache reconciliation. Exercise partial provider and host
   failures, stale recovery, selection retention and active/history caps with
   metadata-only public fixtures. Reuse the earlier plan's bounded F1 ownership.
3. After contract-based Observer acceptance, reproduce single New/Resume through
   the complete local/remote public writer/Tmux/deferred-TTY path. Cover native
   trust, changed handoff guards, uncertain effects and visible terminal failures.
   No double dispatch and no cleanup of provider work after viewer failure.
4. Review/commit scoped client checkpoints, build an unselected Plus candidate,
   verify its pure reader and installed bytes outside the checkout, then run
   client native acceptance against the pinned independently accepted producer.
5. Use a separate compatible managed-tuple selection/rollback gate. Desktop
   picker readability, terminal placement, focus and ordinary user workflow need
   actual graphical acceptance. Source/unit or headless success does not supply it.

Follow Observer's `docs/observation-validation-workflow.md`:
bracket installed public CLI results with independent native metadata, compare
full identity/state/age, and distinguish races, unsupported evidence and proved
mismatches. Another Observer result or Plus cache is not independent native truth.
Preserve ordinary sessions/hooks/settings; establish owned isolated stores,
endpoints and cleanup before native test actions. Retain bounded metadata only.

The producer's next gate and the client checkpoint stay separate. Return new
Observer findings to its review/repair queue; no simultaneous edits across the
two repositories are needed to complete this handoff.
