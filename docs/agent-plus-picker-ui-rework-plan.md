# Agent Plus picker UI rework

Date: 2026-10-02

Status: proposed design, awaiting review before implementation. This is the
Rofi picker layout and styling pass. Agent Plus `0.11.3` remains the deployed
baseline; the navigation and colors below are planned behavior.

The existing [navigation](agent-plus-picker-navigation-plan.md),
[batch actions](agent-plus-batch-session-actions-plan.md), and
[local viewer observations](agent-plus-local-viewer-state-plan.md) provide
the foundation. This follow-up brings those pieces into one presentation.

## Page model

Add Open beside Active. They overlap: **Open is a subset of Active**.

| Page | Conversations shown | Leading group row |
| --- | --- | --- |
| Active | All currently running conversations across authoritative hosts, including those open here and the existing supported waiting cases | All active sessions (N) |
| Open | Active conversations with a fresh confirmed or qualified local viewer on this machine | All open sessions (N) |
| All | Existing mixed conversation history across hosts | All active sessions (N) |
| Local | Existing history owned by this machine | All active sessions (N) |
| Named host | Existing history owned by that host | All active sessions (N) |

Active continues to include dark sessions, Open, Open?, Active?, and supported
waiting rows under the existing effective activity predicate. It does not
mean only sessions without a window. Open describes the **viewing endpoint**,
not the owning host: a Snap-owned conversation opened on Starship appears in
Starship's Open page. Windows on another machine do not qualify.

Open accepts both confirmed Open and qualified Open? observations. Unknown,
failed, missing, ambiguous, and expired viewer observations do not qualify;
those conversations remain in Active when their activity is current. Viewer
presence alone cannot rescue stale or failed activity evidence.

An agent can exit while its tmux session and terminal remain open. Such a row
still reads Inactive · Open or Inactive · Open? in All/Local/host history. It
does not enter either Active or Open. This keeps the new Open page focused on
running agent conversations and preserves the subset relationship.

The wrapping page ring becomes:

```text
Active → Open → All → Local → <remote hosts in Host Mesh order> → Active
```

With no remotes, use Active → Open → Local. Both Active and Open remain
present when empty. Keep the first-launch default: All when available,
otherwise Local. Left/Right preserves the search, selects the first eligible
matching conversation, and uses only the cached snapshot plus the existing
best-effort preference write.

Remember Open using the same endpoint-local page preference and single
last-successfully-used conversation identity. Accept existing preferences;
do not add per-page bookmarks, groups, or workspace records. A rollback to a
release that cannot read Open uses its normal invalid-preference fallback.

## Row layout and state hints

Keep two lines per conversation and the existing provider icon:

```text
<provider icon> Conversation title
               host · directory · age · Open
```

The title remains the strongest text. Host, shortened directory, and age
remain quieter context. Add a small background tint behind the short state
label so the running and open cases can be scanned quickly. Keep the state
text readable and searchable; color is an additional hint.

| State label | Proposed treatment |
| --- | --- |
| Inactive | Muted text, no state fill |
| Active / Active? | Dark neutral fill with readable light text |
| Open | Soft green fill with brighter text |
| Open? | Muted green fill with the same tight question-mark suffix |
| Waiting / Waiting? | Neutral running treatment; keep any Open qualification visible |
| Inactive · Open/Open? | Muted Inactive plus the appropriate green Open qualification |
| Unavailable or retained activity | Existing unknown/last-known warning; no positive running fill inferred from stale evidence |

Use Pango spans for the label. Rofi already supports row markup, and Pango
supports text foreground/background attributes; see the official
[Rofi script API](https://davatorium.github.io/rofi/2.0.0/rofi-script.5/) and
[Pango markup reference](https://docs.gtk.org/Pango/pango_markup.html).
Give the label explicit colors and foreground/background alpha while keeping
the surrounding context muted. Escape all provider-derived text as today.

Keep the established visual meanings distinct:

- The selected row keeps the existing cursor background and blue border.
- Blue title text continues to mark exact frozen batch operation targets.
- Amber remains the observation-warning treatment.
- Green labels describe observed local viewer presence, including Open?.

The label remains readable on normal, selected, warning, and batch-target
rows. A details warning can coexist with a fresh state hint, but failed
activity evidence must retain its uncertainty. Use semantic observations for
formatting and filtering; never parse the displayed label to decide an action.
Choose final shades during native visual review. Keep the current two-line
height, provider icons, and shared theme selection/warning behavior.

## Shared action bar and group row

Keep one action bar for both individual conversations and the group row:

```text
Enter: [Resume] · Close · New  │  Tab: Cycle  │  Alt+A: All
```

Tab and Shift+Tab cycle the same three actions. Alt+A clears search and
selects the current page's group row. The group row stays first; initial
selection still restores a real conversation or selects the first real row
when one is available. The group is never a remembered conversation.

Batch preparation, confirmation, and progress change only the group label:

```text
All open sessions (N)
All open · Preparing…
All open · Confirm Close (N)
All open · No windows to close
```

Use the equivalent All active wording on other pages. Keep the action bar
stable when the cursor moves between the group and conversations. New on
either group gives a short Select a conversation notice. Keep the explicit
second Enter to confirm a nonempty batch; the first Enter starts finite
read-only preparation. Ordinary cursor movement requires no script callback.

## Open-page batch scope

**The Open group must never expand to all Active sessions.** Current batch
scope selects owner hosts. Open additionally filters membership by fresh
viewer presence at this endpoint; treating it as an alias for Active would
operate on hidden dark sessions.

| Selected row | Resume | Close | New |
| --- | --- | --- | --- |
| A conversation | Existing focus/resume behavior | Existing guarded window close | Existing new conversation in that directory |
| All active sessions | Existing fixed Resume preview | Existing fixed Close preview | Select a conversation |
| All open sessions | No windows to open; no bulk focus or launch | Fixed preview of safely closable windows in the Open subset | Select a conversation |

Active remains the machine-switching page: Close there leaves running agents
in Active, and Resume there opens missing windows on another endpoint. Open
is useful for reviewing and closing what is already visible here. A single
Resume still focuses its selected open conversation.

For an Open Close preview:

1. Refresh authoritative owner/activity observations through the existing
   finite preparation path. Obtain fresh bulk viewer observations through
   Tmux Plus's public inventory enrichment, using the current endpoint.
2. Build the Open subset from current per-host rows before the All history
   cap, with complete matching tmux references. Deduplicate conversations
   even when several local viewers exist.
3. Inspect those candidates with the existing strict public viewers command.
   Only verified, safely closable viewers become operation targets. An Open?
   display observation supplies no close handle and may remain excluded.
4. Freeze complete references, verified viewer handles, exclusions, counts,
   and target membership. Require explicit confirmation of that fixed set.

The initial group count describes cached page membership; the confirmation
count describes verified work. Preparation can change membership as fresh
evidence arrives. The exact target and exclusion rows remain visible for
review. Search does not narrow the batch. A failed or expired viewer check
cannot widen the Open scope. A window that disappears during preparation is
not reopened by the Open group.

Extend private navigation, continuation, preparation, and preview records to
carry the Open scope explicitly, including its endpoint context. Page or
action changes cancel preparation and invalidate its preview. Late results
cannot publish into another scope. Existing full-reference and authority
guards remain required at confirmation and execution.

During confirmation and job progress, retain the frozen target/exclusion
cards even when closing windows removes them from the live Open subset.
These retained cards remain preview/result evidence, not new page members or
new operation targets. Display-only cards remain non-actionable. Timer
refreshes cannot change the frozen count, title tint, or submitted handles.
After dismissal or context change, return to the live page membership.

## Refresh and empty states

Reuse the existing finite viewer helper and timed callbacks. Known viewer
observations renew after seven seconds and expire after ten; all-unknown or
failed observations retain the existing ten-second retry behavior. Keep the
strict expiry and full endpoint/reference join. No new polling daemon or
selection watcher is needed.

Open can be empty on a cold launch while viewer inspection is pending. Show
a short non-actionable Checking windows… row, followed by No open sessions
observed after a successful empty check. Failed or partial checks retain a
short Windows unknown notice; an empty list is not proof that every window
is closed. Preserve search, page, and action as observations arrive.

Preserve the selected conversation if it still belongs to Open. If it leaves,
select the first surviving eligible matching conversation; prefer real rows
over the group when any are available. Do not automatically jump to Active.
Use the same deterministic recency order and bounded per-host source as
Active; do not add a separate window inventory or a row per terminal.

## Implementation sequence and acceptance

1. **State presentation.** Introduce semantic label formatting and compact
   shaded labels; shorten the shared hints. Verify uncertainty suffixes,
   markup escaping, and coexistence with selection, warnings, and batch tint.
2. **Open scope end to end.** Add page membership, ring/prompt, preferences,
   empty/refresh behavior, and the explicit private Open batch scope together.
   Do not expose a group that silently falls through to all-owner Active.
3. **Batch integration.** Validate fixed Open Close previews, Open-group
   Resume's no-work behavior, retained preview/result cards, cancellation,
   stale authority rejection, and late-result isolation.
4. **Release.** Commit validated checkpoints, publish the Agent release,
   update its exact managed archive/checksum, and apply scoped paths on Snap
   and Starship. Use existing source, candidate, managed, installed, parity,
   and scoped verification gates. Record native visual acceptance separately.

Focused acceptance cases:

- Active includes dark, confirmed Open, Open?, and viewer-unknown running
  rows; Open includes only its fresh positive subset. Waiting follows the
  existing Active predicate. Inactive-but-open stays in history views.
- Snap-owned windows on Starship appear in Starship Open; windows visible
  only on Snap do not. Multiple windows yield one conversation row.
- Open is uncapped by the flattened All history limit; duplicate identities
  appear once. Empty Open stays navigable and can be remembered.
- Left/Right and action cycling remain cache-only. Cold readiness, renewal,
  expiry, failure, changed endpoint, and stale-reference cases retain their
  meaning without resetting the current page or action.
- Open Close excludes dark and unknown rows, and cannot use qualified display
  evidence as authorization. Search does not change scope. Resume on its
  group creates no job; single Resume still focuses an existing window.
- Confirmation targets remain visible after membership changes. Counts and
  tint remain frozen; cancelled helpers cannot replace a newer preview.
- Native review checks long titles/directories, narrow layouts, selection,
  warning contrast, batch tint, and readable state labels on both hosts.
  While either host is in active use, use headless/isolated checks and defer
  disruptive GUI acceptance until the operator provides an idle endpoint.

## Ownership and limits

Agent Plus owns this picker presentation and private navigation state. Tmux
Plus continues to own local viewer detection and guarded window operations.
The existing public contracts supply the required facts; no contract change
is proposed by this plan. Shared desktop-theme changes require their own
scoped review if label markup proves insufficient.

Continue explicit external tmux session management. Keep the documented
in-TUI conversation-switching limitation, provider discovery limits, and
qualified viewer exclusions. Internal agent lifecycle hooks, daemon/session
managers, Kill all, saved workspaces, and the persistent session client remain
outside this picker pass.
