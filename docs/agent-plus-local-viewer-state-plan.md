# Local viewer state design and implementation plan

Date: 2026-10-02

Status: implemented for Agent Plus 0.11.0 and Tmux Plus 0.5.0 after acceptance
on 2026-10-02 with worker-goal-loop.
The operator approved the recommended Open? wording, both-picker scope,
bulk inventory enrichment, and retained action guards. Implementation and
deployment acceptance are recorded separately from this design approval.

The [picker UI rework](agent-plus-picker-ui-rework-plan.md), implemented in `0.12.0`, uses this
foundation for shaded state labels and an Open page in Agent Plus. Those
presentation changes build on the unchanged Tmux Plus viewer contract.

The pickers should distinguish a running session with a viewer on the current
machine from a running session without one. Tmux Plus owns detection and its
public result. Agent Plus combines that result with provider activity. Both
pickers use short status text while continuing to manage ordinary tmux sessions
through explicit user actions.

## Baseline behavior and evidence

The selected baseline is Agent Plus `0.10.3` and Tmux Plus `0.4.0`. Current
deployment evidence remains in the managed `docs/rofi-plus-status.md`.

- Agent activity comes from provider process observations, with `waiting`
  added for a pending tmux launch. Local viewer presence is absent from ordinary
  rows. Existing `idle` means no observed running provider, not necessarily
  that its tmux shell has disappeared.
- Tmux Plus already owns public `viewers` inspection for complete session
  references. Its result is `verified`, `none`, `unverified`, `ambiguous`, or
  `unsupported`. Its verified handles serve guarded window operations.
- Tmux Plus also has a separate title-based `open here` display check. That
  check currently handles locally owned sessions only and otherwise falls
  back to global attached-client counts.
- The reported Starship/Snap preview observed `esp32-349` as already open and
  excluded three windows as unverified. Source tests explicitly demonstrate
  that an unmarked remote SSH viewer can be visible while failing verified
  inspection. This is a supported explanation for the gap; the exact cause of
  each reported window has not been established.
- The ordinary Agent background refresh already obtains one public Tmux
  inventory with panes and correlation options. Tmux inventory has bounded
  host concurrency and operation deadlines. Repeating the full guarded
  `viewers` command for every ordinary row would repeat remote validation and
  desktop scanning.

Relevant sources are
[Tmux viewer_service.py](https://github.com/byebyebryan/rofi-tmux-plus/blob/0b056e387fa07e3a76d98785d94421bc46ed797e/rofi_tmux_plus/viewer_service.py),
[Tmux rofi.py](https://github.com/byebyebryan/rofi-tmux-plus/blob/0b056e387fa07e3a76d98785d94421bc46ed797e/rofi_tmux_plus/rofi.py),
[Tmux inventory_service.py](https://github.com/byebyebryan/rofi-tmux-plus/blob/0b056e387fa07e3a76d98785d94421bc46ed797e/rofi_tmux_plus/inventory_service.py),
[Agent contract_backend.py](../rofi_agent_plus/contract_backend.py),
[Agent rofi.py](../rofi_agent_plus/rofi.py), and the published
[Tmux Session contract](../contracts/tmux-session-v1/contract.md).

## Meaning and presentation

There are two independent observations: whether the agent is running and
whether a session viewer is present at this endpoint. The owning host and the
viewing machine can differ. A Snap-owned session can be Open on Starship.
Clients attached on another machine do not establish a local open window.

Agent Plus uses this display mapping:

| Provider observation | Local viewer observation | Short display |
| --- | --- | --- |
| Current and inactive | No viewer | Inactive |
| Current and running | No viewer | Active |
| Current and running | Confirmed viewer | Open |
| Current and running | Plausible legacy match | Open? |
| Current and running | Unknown, failed, or expired | Active? |
| Current and inactive | Confirmed or plausible viewer | Inactive · Open or Open? |
| Current and inactive | Unknown, failed, or expired | Inactive? |
| Waiting for deferred launch | Any viewer result | Waiting, Waiting · Open/Open?, or Waiting? |
| Provider activity unavailable or retained after failure | Any viewer result | Preserve the existing unknown or last-known indication; do not assert Inactive |

The inactive-but-open case can occur when an agent exits while its terminal
and tmux session remain. Showing both facts avoids pretending that a visible
window proves a running agent. Missing or ambiguous tmux correlation gives an
unknown viewer observation, never proof of absence.

`Open?` is the accepted wording for legacy matches. A confirmed Open label
describes current attachment evidence; Open?
describes a plausible matching window with incomplete attachment identity.

Replace the existing activity word in the secondary row text; add no extra row
or action-bar paragraph. Waiting and existing observation warnings remain
visible. Reuse current active styling for both Active and Open initially, so
status text carries the distinction without adding another color meaning.
The blue title tint continues to describe frozen batch operation membership.
Search metadata receives the same status terms as the displayed row.

The Active page and All active sessions count continue to include both Active
and Open, plus waiting sessions, using the existing activity predicate. Viewer
state does not reorder rows, change pages, reset selection, alter the action
cycle, or become a batch eligibility shortcut.

Tmux Plus uses the same observation for `open here`, `open here?`, or an
unknown local-viewer indication. Its existing attached/detached information
continues to describe global client attachment; it must not be relabeled as
local viewer absence or presence.

## Detection in Tmux Plus

Consolidate the current display check and shared viewer inspection primitives
into one generic observation service. Scan the local compositor inventory once
and reuse a bounded process and metadata index across the selected sessions.
Use the same service for public inventory enrichment and Tmux picker display.

The service reports presence independently from permission to close a viewer:

| Result | Required evidence |
| --- | --- |
| Open with confirmed confidence | A local compositor window and a live attachment associated with the complete session reference, using launch metadata and live attachment checks or an exact local tmux client join. |
| Open with matched confidence | A unique plausible legacy window match against current tmux name and native owner hostname, supported by attachment/process evidence where available, with no contradictory launch metadata. |
| None | Successful inspection of the supported desktop found no relevant viewer or unresolved candidate for this session. |
| Unknown | Inspection is unavailable or incomplete, a match is ambiguous, the source session is stale, or contradictory evidence prevents an association. |

Presence requires a compositor window. A pending marked launch without a live
attachment is Unknown until registration finishes. The display observation is
not a guarantee against an internal session switch after observation; those
internal transitions retain the existing known limitation.

Confirmed presence does not require `destroy-unattached=off` or a dedicated
single-window process layout. Those remain requirements of the close operation.
A confirmed presence observation supplies no close handle or authorization.

A title match alone never becomes confirmed attachment proof. Conflicting or
different launch references suppress legacy fallback. Remote legacy matches
remain qualified because a title and SSH argv do not prove the complete remote
server generation and creation time. Do not relabel an unverified remote viewer
as safely closable. Multiple confirmed local viewers still yield one Open
status for the session.

The first supported desktop remains the managed Kitty/Niri setup. Unsupported
terminals, compositor failures, unreadable process state, and unresolved
layouts produce Unknown. Other terminal or compositor support is a separate
follow-up.

## Public contract proposal

Prefer an optional enrichment of the existing bulk inventory command:

```text
rofi-tmux-plus inventory --json --with-viewers [existing inventory options]
```

The proposed response adds an endpoint context and a small observation to each
current session. Final field names and bounds are settled in the producer
contract before consumer implementation. The intended shape is:

```json
{
  "viewerEndpoint": {"hostId": "alpha", "observedAt": 100000},
  "hosts": [
    {
      "sessions": [
        {
          "hostId": "beta",
          "serverGeneration": "tmux-v1:example",
          "sessionId": "$7",
          "createdAt": 30,
          "localViewer": {"state": "open", "confidence": "matched"}
        }
      ]
    }
  ]
}
```

This is a field-shape illustration, not a complete valid inventory document.
Session identity is inherited from the complete containing inventory row.
The endpoint is the machine invoking the producer. Remote inventory helpers
collect owner-side tmux facts; viewer enrichment runs at the caller after those
rows return. It must never report the remote owner's desktop as the caller's
desktop. `observedAt` uses the contract's Unix millisecond convention.

Keep state values `open`, `none`, and `unknown`; confidence is meaningful only
for Open and is `confirmed` or `matched`. Unknown may include a bounded typed
reason. Viewer observation failures preserve authoritative tmux inventory and
produce Unknown observations rather than changing a host into an inventory
failure. Omitted fields from an older snapshot map to Unknown in consumers.

Plain inventory and existing lifecycle commands retain their current behavior.
This is an additive v1 contract extension requiring producer schemas, fixtures,
limits, checksums, and a released artifact before consumers adopt it. Consumers
use the public executable through PATH and the managed exact release tuple.

## Refresh and cache behavior

The first picker frame uses its private presentation cache or an unknown initial
frame. It runs no synchronous viewer scan and no per-row remote request.
View changes and Tab remain cache-only operations.

Collect viewer observations in ordinary background inventory refreshes. On a
new picker launch, request a finite background Tmux inventory enrichment when
viewer data is missing or due for renewal,
even if provider history remains fresh. Reuse an in-flight compatible inventory
refresh rather than starting another. If only viewer data needs updating, use
the same Tmux bulk command without re-running provider discovery. The helper
updates viewer observations only for exact references still associated with
the current provider snapshot. Merge under the private viewer cache's lock without
replacing provider observations or their timestamps from an older helper snapshot.

While the dialog remains open, existing timed callbacks display completion and
renew known observations after seven seconds, leaving three seconds for the
finite helper and completion callback before the strict ten-second expiry.
The still-current observation remains visible while renewal runs. All-Unknown
and failed observations retry after ten seconds. Slow or failed renewals still
become Unknown; renewal never extends an old observation's validity. Callbacks
do not run a synchronous window scan on cursor movement. These are finite
refresh processes with existing owner/deadline controls; closing the picker leaves no recurring
poller. A running read-only refresh can finish without viewer effects.

Agent Plus `0.12.1` uses a fixed one-second native Rofi tick while watching
viewer state. Rofi 2.0 rearms its timeout before the script callback, using the
previous frame's delay; a variable countdown could expire a valid observation
after a page change. The viewer cache still starts helpers only when the
seven/ten-second renewal interval is due. Navigation remains cache-only.

Key private observations by endpoint desktop context, Mesh revision, and the
full tmux identity. Changes to endpoint desktop, session generation, creation
time, or Mesh invalidate a positive association. Expired observations display
an uncertainty marker until refreshed. A failed scan publishes Unknown rather
than retaining a fresh-looking Open. Late results cannot overwrite a newer
request or a different session association.

Inventory enrichment reuses owner facts already collected for the bulk call.
It adds no per-session SSH connection and respects existing inventory, desktop,
process-scan, response-size, and whole-operation bounds. Tmux picker integration
reuses its existing model and refresh machinery; private callbacks must retain
their current navigation responsiveness. Its viewer refresh must also run in
local-only mode and when retained remote owner rows are fresh, so ordinary
remote-cache expiry cannot become the viewer refresh trigger.

## Relationship to actions

This phase changes observation and presentation. Cached indicators and legacy
matches do not enter the existing batch target or viewer-handle records.

Resume and Close previews keep current fresh public `viewers` checks. Confirm
retains its exact frozen target list, and worker operations retain their
reference and viewer revalidation. Ordinary individual Resume keeps its current
focus compatibility. An Open? row can therefore remain excluded by a batch's
stricter viewer checks. Explain that limit in docs; keep product status text
short. Improving verified adoption for legacy windows is a separate action
change that needs its own evidence and review.

A normal viewer refresh may update a row's observation without changing frozen
batch counts, operation tint, confirmation identity, or the shared action bar.
The `0.10.3` rule that already satisfied viewers do not enter a job remains in
force. Provider hooks, provider-internal session switching, session managers,
automatic attaching/closing, saved workspace groups, and Kill all remain outside
this refinement.

## Implementation checkpoints

1. **Confirm detection cases.** Reproduce marked local and remote viewers,
   legacy remote viewers, no viewer, and conflicting metadata with controlled
   fixtures. Inspect the reported production windows read-only if still
   available. Record which cases can be confirmed and which remain matched or
   unknown. Do not promise that every existing window can become confirmed.
2. **Publish the Tmux result.** Implement the shared bulk observation service,
   optional inventory enrichment, and Tmux picker consumption. Add canonical
   schemas, fixtures, semantic tests, limits, and release documentation. Keep
   action tests proving that weak matches never become close handles.
3. **Integrate Agent presentation.** Sync the released producer artifact,
   validate and correlate observations, add bounded background refresh and
   cache freshness, and render the short states. Update search metadata and
   cache migration behavior. Preserve waiting, activity membership, selection,
   action text, and frozen preview records.
4. **Validate the coordinated candidate.** Run meaningful producer and consumer
   tests, each source gate, contract-sync checks, exact-tuple candidate gate,
   and complete managed checks. Review performance and non-mutation evidence.
   Prepare exact archive pins and SHA-256 values together.
5. **Deploy after accepted implementation.** Commit source and managed changes
   in reviewable steps, push, perform scoped chezmoi deployment to Snap and
   Starship with the producer installed before the consumer on each host, and
   prove installed parity and live gate results. Update the
   authoritative status ledger and obtain native UI acceptance when a host is
   available for nondisruptive testing.

The operator authorized implementation after reviewing this plan. Current
operator use excludes disruptive GUI tests on Starship and Snap; read-only
inspection and isolated headless traces can validate the initial work. Arrange
a quiet host before opening any fixture viewer or picker.

## Acceptance and design validation

| Case | Required result |
| --- | --- |
| Running agent with a managed local viewer | Open, with no focus/attach side effect. |
| Running agent with a managed remote-session viewer here | Open regardless of the session-owner page. |
| Running agent with no viewer here, but clients elsewhere | Active; global client count does not imply Open. |
| Unique legacy remote window match | Open? under the recommended wording; batch Close remains excluded. |
| Missing compositor, unreadable processes, ambiguous or contradictory identity | Unknown qualification; never a false detached assertion. |
| Agent exits but tmux/window remains | Inactive with an Open qualification. |
| Waiting launch with or without a viewer | Waiting remains visible and active membership is preserved. |
| Provider or owner inventory failure | Existing stale/unknown warnings remain; old cached data does not assert current Inactive or Open. |
| Window opens or closes while picker is open | Next bounded viewer refresh updates status without moving selection or changing the action. |
| Session ID reused, server restarted, Mesh changed, desktop restarted | Prior positive viewer observations are invalidated. |
| Batch preview is already frozen | Viewer status refresh cannot expand or mutate its authorized target list. |
| Cold/expired cache | Immediate cached/unknown frame, followed by background readiness. |
| Many sessions or partial scan | One desktop inventory/index per scan, no per-row SSH, bounded completion; unresolved observations are Unknown. |
| Observation-only refresh | No viewer open, close, focus, detach, or provider lifecycle command is dispatched. |

Source review confirms the ownership boundary, the existing local-title display
gap, and the stronger action-proof requirements. The bulk enrichment, labels,
ten-second freshness window, and both-picker rollout are accepted choices.
Performance and native appearance remain implementation acceptance work.

Completion requires the tests and deployment checks above while retaining
stricter batch action guards.
