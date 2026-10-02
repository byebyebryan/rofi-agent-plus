# Agent Plus batch session actions design and implementation plan

The [picker UI rework](agent-plus-picker-ui-rework-plan.md), implemented in `0.12.0`, retains
this fixed-preview model and adds a distinct Open group scope. The behavior
below records the All active implementation that remains on the other pages.

Date: 2026-10-01

Status: the original menu-based batch design shipped in Agent Plus `0.8.0`
with Tmux Plus `0.4.0`. On 2026-10-01 the operator approved an inline group
target to keep the conversation list visible while choosing batch actions.
Agent Plus `0.9.0` implemented in-place group activation. The operator found
that requiring Enter before showing group actions left a selection feedback
gap and approved one shared Resume / Close / New cycle on 2026-10-01.
Agent Plus `0.10.0` implemented that shared cycle. Agent Plus `0.10.1`
retains explicit confirmation and prepares group previews asynchronously,
with immediate progress and four concurrent read-only viewer checks.
Agent Plus `0.10.2` keeps the action bar visible throughout and confines short
preparation/confirmation/progress text to the control row. Verified targets
use title color without added conversation text.
Agent Plus `0.10.3` counts, colors, and submits only windows that need opening
or closing. Already open/closed observations cannot submit an empty job or
focus an existing window; changed viewer state requires a new preview.
Existing public viewer
operations and finite execution guards remain its foundation. The managed fleet
status records the selected source and deployment evidence.

Close all and Resume all should make switching between Snap and Starship
convenient using the existing Active page and ordinary tmux sessions. Close
removes this endpoint's viewing windows while the agents keep running. Resume
attaches windows here to those existing sessions. Kill all has a separate,
specialized purpose and belongs in a later implementation.

Use one-shot public operations and the picker’s existing provider observations.
Keep provider hooks, daemon/session management, automatic restarts, and saved
workspace groups outside this implementation.

The design fits the suite's ownership model. Tmux Plus `0.4.0` supplies the
verified viewer foundation through its public contract. This UI refinement
uses that released capability and changes no provider lifecycle contract.

## Actions and scope

| Action | Intended result |
| --- | --- |
| Close all windows | Close every verified viewing window here for eligible sessions in the chosen page scope. Preserve the tmux sessions, agent processes, and viewers on other machines. |
| Resume all active sessions | Ensure one viewing window here for each eligible existing tmux session in scope. Reuse an existing verified viewer; otherwise attach a new one. |
| Kill all sessions | Later specialized action: end the selected tmux sessions on their owning hosts, affecting their attached clients everywhere. |

The proposed batch scope follows the page's host coverage:

| Page | Host coverage |
| --- | --- |
| Active or All | Every authoritative host, including local |
| Local | Only the current machine as session owner |
| Named host | Only that host as session owner |

Both initial actions target observed active conversations with an unambiguous
existing tmux association. A local viewing window can attach to a session on
either machine; the page's host describes the session owner. Close always
acts on windows at the endpoint where the picker was opened.

Selected scope is the whole page, independent of the current search.
Installed Rofi is `2.0.0-dirty`; its script callback does not export the search
text. The [2.0.0 script documentation](https://davatorium.github.io/rofi/2.0.0/rofi-script.5/)
and [tagged callback implementation](https://raw.githubusercontent.com/davatorium/rofi/2.0.0/source/modes/script.c)
confirm the available environment. Supporting exactly the filtered rows would
need a tested Rofi integration that preserves native matching and row metadata.
Reimplementing fuzzy matching in Agent Plus would add a second search policy.

Prepare targets from the validated per-host rows used by Active, before the
flattened All list's `max_sessions` cap. On All, the preview must explicitly
say that the batch covers active sessions across the page's hosts and may
include rows beyond the ordinary history list's cap.

## Machine switching workflow

1. On Snap, open Active and choose Close all windows. The preview lists the
   session owners and the windows that will close on Snap.
2. Confirm the batch. The same tmux sessions and agent processes continue on
   Snap and Starship. They remain eligible for Active after refresh.
3. At home, open Active on Starship and choose Resume all active sessions.
4. Confirm the batch. Starship opens missing viewers for local and remote
   sessions and reuses viewers already present there.

This gives a current working set from running agents. The set can change
between the two actions as agents exit or new sessions start. Resuming the
exact earlier set would require a saved group, which is outside this design.

## Eligibility and preview

Before showing the preview, refresh observations for the selected host scope
once. Use the existing effective activity predicate: an agent waiting for input
counts as active. Require a current, unique tmux association and the full
`hostId`, `serverGeneration`, `sessionId`, `createdAt` reference. Provider options
remain Agent Plus's correlation evidence and become operation guards where
available. Deduplicate by the complete tmux reference so two catalog rows
cannot operate on the same runtime twice.

The preview lists the exact fixed targets with conversation, provider, and
owning host. Close also shows the number of verified windows here; Resume
distinguishes already open from missing viewers. Show exclusions with short
reasons, such as no tmux association, ambiguous association, stale observation,
unreachable host, or unsupported viewer. A zero-target preview performs no
operation. Already open/closed observations also perform no operation: only
missing Resume viewers or present Close viewers enter the confirmed job.

Confirmation authorizes only that frozen list. Immediately revalidate the
relevant session or viewer before each operation. A disappeared, replaced, or
newly ambiguous target is skipped and reported. Refresh cannot expand a batch
after confirmation. Close freezes individual viewer handles as well as session
references, so a window opened after preview cannot join the confirmed batch.

Resume uses the public Tmux Plus `open` path for an existing reference. It
must never call ordinary Agent Resume's create/reconcile fallback. A missing
session or current evidence that its agent has ended makes the target a skip.
The action never starts a replacement provider process. Native Resume remains
available individually.

Close needs proof that detaching the viewer preserves its session. A session
configured to disappear when its last client detaches must be excluded. tmux
normally preserves detached sessions with `destroy-unattached=off`, its default;
the [tmux manual](https://man.openbsd.org/tmux) describes that behavior.

## Picker interaction

### Shared action cycle (accepted 2026-10-01)

Replace the leading `Batch actions…` row with **All active sessions (N)**.
This is a typed group target in the main picker. Keep the conversation list
visible while choosing the operation, reviewing the fixed batch, and reading
progress/results; remove the separate Close/Resume action submenu.

Use one persistent action bar for every selectable target:

`Enter: [Resume] · Close · New  │  Tab: Cycle actions`

| Action | Conversation selected | All active sessions selected |
| --- | --- | --- |
| Resume | Run the existing guarded individual Resume lifecycle. | Prepare a fixed Resume all preview for the whole page's owner-host scope. |
| Close | Prepare a fixed Close preview for only this conversation's verified viewing windows here. | Prepare a fixed Close all preview for the whole page's owner-host scope. |
| New | Run the existing guarded New session here lifecycle in the conversation's directory. | Show "Select a conversation to create a new session." and perform no operation. |

Tab and Shift+Tab cycle Resume / Close / New, starting with Resume on launch.
Moving between a conversation and the group needs no action-bar redraw: the
same selected action applies to either target. Enter dispatches from the typed
highlighted row, without a group activation step. Moving the highlight and
changing pages preserve the chosen action. Alt+A clears search and selects the
group; it does not toggle a context, change the action, or prepare a preview.
The group warning and Alt+A are cache-only and must not query hosts, providers,
tmux, or viewers. A group row never becomes the remembered conversation.
Opening restores the remembered conversation or first real conversation,
falling back to the group only on an empty page.

The native Rofi 2.0.0 probe on 2026-10-01 confirmed that the selection-change
command launches separately and its output is not parsed as a script response.
Moving the cursor fired the hook but did not call the script executor again.
The shared cycle avoids needing that redraw. Use only dialog-local finite UI
state; no Rofi fork, native plugin, persistent helper, keyboard injection,
provider hook, or perpetual polling for cursor tracking. Keep native Escape
and Ctrl+G cancellation.

Preparing a preview clears search so affected rows remain visible. Group
scope remains the whole page's authoritative owner-host coverage. In preview
and results, render the uncapped scoped active set alongside ordinary page
rows, without duplicate catalog identities. Display frozen targets absent
from that list as target cards. Preserve the normal history cap outside these
states. Keep the group in normal filtering and avoid provider aliases
matching its display label.

The cached count N describes observed running conversations in this scope;
it is not an operation guarantee. Mark operation membership only after the
preview has verified exact targets. Do not perform provider, host, or viewer
queries just to move the cursor or cycle an action. Existing discovery
uncertainty and exclusion reasons remain visible; the cursor highlight stays
distinct from verified target tint.

Enter on the group with Resume or Close immediately changes its row to
**All active · Preparing…** and starts a finite read-only helper. The shared
Resume / Close / New action bar stays visible and unchanged during preparation,
confirmation, and job progress, even when the cursor moves to a conversation.
Remove batch instruction paragraphs from the message area. Preparation begins
only on that Enter, not on opening or automatic provider refresh. Refresh the
selected scope once, then inspect at most four references concurrently while
preserving catalog order and exact option guards. Protocol or authority failure
stops subsequent chunks and prevents confirmation. Existing timed callbacks
display the completed fixed preview. Enter during preparation only redraws;
it cannot queue a future confirmation.

Keep that preview inline: its control becomes **All active · Confirm Resume
(N)** or **All active · Confirm Close (N)**. The conversation list shows
exact included targets through title color and keeps exclusions visible.
Display frozen targets missing from the current ordinary list too. Only
verified exact targets receive the operation membership tint. An operation
change or page/context change invalidates the displayed confirmation; any
new operation requires a newly prepared preview. Tab changes the same shared
cycle and returns to the main list with the relevant target selected. Alt+A
returns to the main list with the group selected. Enter on a conversation
inside a preview discards it and returns to ordinary selection without opening
the provider. Preview target cards cannot submit a batch or invoke lifecycle.

Single-conversation Close uses the same fixed preview and finite job guards.
Require the selected complete tmux reference, verified provider-option guard,
and current Host Mesh authority. Inspect only that exact reference through the
public Tmux viewer command; it verifies the current session and option before
returning handles. Closing a viewer requires no provider discovery or process
activity refresh. Never widen to another conversation or rebind to a
replacement session. An idle conversation may close when its existing
association is verified; activity is required for All active, not for one
verified viewer. Missing, ambiguous, stale, or unverifiable associations show
a no-operation reason. Label the preview as a selected conversation, not All
active sessions. Freeze the exact viewer handles and preserve tmux, provider
processes, and other clients.

A second Enter on the typed confirmation consumes the fixed private preview
once and starts the existing finite job. Keep the page scope in the prompt;
show concise progress and skipped/failed counts in the control row with the
conversation list visible. Per-target records stay in the private job. Poll only
while preparation, the finite job, or the picker's existing bounded refresh requires it.
Escape before confirmation has no session effects; afterward it hides the
picker while the confirmed job finishes. One batch runs at a time per endpoint.

The refinement preserves stable row identity and the leading-row offset in
initial launch, refresh, and page transitions. Native acceptance must cover
conversation-first opening, direct group dispatch, the shared Tab/Shift+Tab
cycle, group New warning, target visibility, ordinary search, page changes,
single Close, inline preview cancellation, and unchanged
preferences. Use the managed invocation and observer callbacks that refuse
provider Enter and any unowned confirmation.

### Earlier UI releases

Agent Plus `0.9.0` removed the operation submenu but required Enter or Alt+A
to activate a separate group action cycle. The operator rejected that
activation gap. The shared cycle above supersedes its presentation while
preserving inline fixed confirmation and results.

Agent Plus `0.8.0` shipped the leading Batch actions row and Alt+A, a separate
Close/Resume/Back action menu, and fixed preview/result screens. The accepted
2026-10-01 refinement above supersedes that presentation. Its whole-page scope,
full-reference viewer guards, fixed private previews, single-job execution,
and preference rules carry forward.

## Viewer identity and closure

Read-only inspection on 2026-09-30 found Niri window IDs and PIDs on both
endpoints. The existing Kitty viewers had a direct tmux attach or SSH child
alongside normal kitten helpers. Niri's CLI can close a specified window ID.
These observations support feasibility, but no existing user window was
closed, detached, or otherwise changed during design validation.

The original viewer investigation found ordinary open using session-name/host
titles. Tmux Plus `0.4.0` now supplies full-reference metadata lookup for batch
reuse and close, with uncertainty surfaced through the public viewer contract.
Ordinary open retains its compatible fallback behavior.

The preferred starting approach is immutable launch metadata carrying the
complete tmux reference, bound to the terminal process and its start generation.
Niri supplies the local window ID and PID. Inspect this evidence on demand;
there is no continuously maintained viewer registry. First try metadata in
the dedicated launch process environment so Kitty keeps its normal app ID.
A custom app ID would also require adjusting the current exact Kitty window
rule and checking desktop icon behavior.

Support dedicated windows with one tmux/SSH attachment. Detect and exclude
ambiguous layouts containing other terminal work. Normal kitten helper children
are expected and must not invalidate a dedicated window. Launch metadata records
the intended attachment; manually retargeting a managed viewer is outside the
supported workflow. Add live tmux client checks where available without adding
provider lifecycle hooks.

Prefer ending the exact verified viewer attachment and allowing its dedicated
Kitty window to exit naturally. Validate this on owned local and remote fixtures.
Compositor close can trigger Kitty's [window confirmation](https://sw.kovidgoyal.net/kitty/conf/#opt-kitty.confirm_os_window_close),
as the earlier [Kitty study](agent-plus-session-client-kitty-results.md) observed.
An accepted close request alone therefore cannot count as a closed window.
Do not automate keystrokes into confirmation dialogs or change global terminal
confirmation policy. Verify window disappearance and session survival before
reporting closure as successful.

Existing unmarked windows need an explicit transition. Adopt one only when
live evidence proves its association; otherwise report it for manual closure.
New attachments receive the metadata. The first checkpoint must record which
existing windows can be supported safely and the resulting limitation.

## Component ownership

Tmux Plus owns generic viewer discovery, validated reuse/focus, and closure,
alongside its existing session inventory and lifecycle. Agent Plus owns active
conversation eligibility, host scope, previews, and batch orchestration. SSH
Plus continues to own approved routes and host authority.

Extend Tmux Plus's public process surface for local viewer inspection and
closure. Publish bounded typed responses and fixtures for exact viewer identity,
session association, stale handles, unsupported environments, and ambiguity.
Reuse `open` for attachment and strengthen its viewer lookup. Publish final
command names and wire fields with the producer checkpoint after the fixture
proof.

Agent Plus must consume the released producer bundle and public commands.
Preserve the independent consumer boundary in
[Suite Integration](SUITE_INTEGRATION.md): no sibling Python imports or reads
of private Tmux Plus state. Contract extensions need a released producer commit,
new bundle provenance, consumer repin, and a coordinated deployment tuple.

## Implementation checkpoints

1. **Prove viewer reuse and clean closure in Tmux Plus.** Use disposable local
   and remote sessions and windows. Establish metadata, exact matching, process
   generation guards, closure completion, and the legacy-window transition.
   Repeat Resume must reuse the same viewer. Close must leave the original
   session reference and fixture process alive, with other clients unaffected.
   Choose the smallest proven closure method before implementing batch UI.
2. **Publish the generic viewer capability.** Add the producer CLI, strict wire
   contract, fixtures, focused tests, and docs. Preserve session-reference guards
   and bounded process behavior. Verify refusal for ambiguous windows and for
   detachment that would destroy a session. Release and sync the consumer's
   contract bundle.
3. **Add Agent Plus batch preparation and UI.** Build fixed eligible targets,
   add the typed menu and previews, execute the finite batch, and display
   results. Keep page navigation and remembered conversation behavior intact.
   Prove that batch Resume issues existing-reference opens only and never
   creates or starts provider sessions.
4. **Validate and deploy the coordinated change.** Update README, integration
   docs, and the managed suite ledger. Run source and exact-tuple candidate
   gates, apply scoped chezmoi artifacts on Snap and Starship, then verify
   installed/live behavior and the real managed picker workflow on both.

Each checkpoint should produce a reviewable commit. Checkpoint 1 passed on
2026-09-30 with disposable local Snap and remote Starship sessions. Full-reference
environment metadata bound each dedicated Kitty window to its terminal and
attachment process generations. Normal non-PTY kitten helpers were harmless;
extra split/tab PTY groups were detectable without terminal remote control.
Signaling only the exact attachment through a pidfd closed the dedicated Kitty
window naturally. The original session references and fixture pane processes
survived, including the zero-client case with effective `destroy-unattached=off`.
Closing the local SSH viewer preserved a second client on Starship. Fixture-only
cleanup used guarded public kills and preserved every pre-cleanup session
reference outside the two owned fixtures.

Legacy local viewers can be adopted through a direct attachment PID joined to
the current tmux client inventory and complete session reference. Legacy remote
viewers lack that generic PID join and are excluded until closed manually and
reopened through a marked attachment. The selected public extension is `viewers`,
`close-viewer`, and strict `open --verified-viewer`, with the ordinary open path
remaining compatible. The production public commands subsequently passed the same local and remote
owned fixtures: strict open registered a viewer, repeated open reused it, exact
close and repeated close preserved the session reference and pane PID. A second
local fixture verified two dedicated windows, preservation of another tmux
client, rejection of close with a session-level `destroy-unattached=on`, and
fixed-handle closure that left a window opened after preview untouched. Cleanup
reported no errors. A real spawned Close job consumed one private preview once,
completed through the public viewer contract, and preserved its owned session
and process. Native Rofi Alt+A with no search matches opened the menu, Close
built a read-only preview, and native Ctrl+G cancelled with unchanged isolated
preferences and no job. Source gates passed 191 Tmux tests and 259 Agent tests.
The current coordinated deployment and installed acceptance are recorded in
the [managed fleet status](https://github.com/byebyebryan/dotfiles/blob/main/docs/rofi-plus-status.md).

## Acceptance and limits

Required checks include mixed local/remote batches, duplicate catalog rows,
multiple viewers of one session, another endpoint's client, session rename or
replacement, stale/reused process and window IDs, unreachable hosts, and
partial failure. Test empty scope, no search matches, menu cancellation, fixed
preview targets, and preference preservation using the managed Rofi invocation.
Check the leading menu row with no bookmark, a restored conversation far down
the list, page changes, filtered refresh, and selection fallback. Real session
selection must account for the row offset and avoid an extra Down on opening.
Audit fixture identity and process survival after Close, then attach the same
fixtures from the other endpoint. Repeated Resume must create no extra viewer.

Initially support the managed Kitty/Niri setup and report unsupported terminals
or ambiguous windows. Active discovery retains the current provider correlation
limits, including native in-TUI session switching and fresh uncorrelated
OpenCode sessions. An unreachable host can be excluded from the active batch
even if a broken SSH window remains here; that window may need manual closure.
These limits should be visible in the preview/results and documented.

## Specialized Kill all follow-up

Keep Kill all separate from the machine-switching release. Its later design
needs an explicit destructive preview naming the owning hosts and affected
sessions. Invoke guarded session-specific Tmux Plus kills for a frozen list;
never use a blanket tmux server kill. Ending a session affects its clients on
other endpoints as well. Conversations remain in provider history and can be
resumed individually. Automatic restart, saved groups, and provider lifecycle
management require separate scope decisions.
