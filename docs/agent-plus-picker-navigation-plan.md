# Agent Plus picker navigation plan

Date: 2026-09-30

Status: accepted interaction direction; implementation and deployment pending.
This plan covers a global Active page and remembered picker context. It
supersedes the earlier Active-only toggle proposal. The current `0.6.5`
picker still has its existing host pages and starts without remembered context.

## Intended interaction

The picker uses one flat, wrapping page ring:

```text
Agents › Active
Agents › All
Agents › Local
Agents › <remote host in Host Mesh order>
```

Left and Right move through this ring. Active is always present, including
when no active conversations are observed. Host pages retain their authoritative
Host Mesh order and remain present when empty or unavailable. With no remote
hosts, the redundant All page still collapses into Local, leaving Active and
Local. With no saved preference, the initial page is All when available,
otherwise Local; adding Active does not change the first-launch default.

Page changes preserve the search and reset selection to the first eligible
matching row. They use the existing immutable cached snapshot, plus a small
local preference write; they perform no provider discovery, tmux inventory,
or SSH work. Alt+R retains its existing background-refresh behavior.

The prompt names the current page. Active needs no new shortcut or toggle
hint. Tab and Shift+Tab continue to cycle Resume and New session here.
Individual host pages show their existing session lists. To narrow Active to
a host or provider, use the existing searchable row metadata.

## Active page

Active is a mixed, newest-first list of conversations with observed running
provider processes across all authoritative hosts. A provider waiting for
input counts as active; this view does not distinguish thinking, streaming,
or waiting. A tmux wrapper alone does not establish activity.

Use the same effective activity predicate as active-row styling, currently
provided by `_row_observation`. A failed activity stage or failed refresh
cannot carry an old active marker into this page. Successful activity evidence
can still admit a row when provider details or tmux association are limited;
keep its existing observation label and selection checks.

Build Active from validated per-host cached rows for the current host catalog,
deduplicate by `(hostId, provider, session ID)`, filter by effective activity,
then use the existing deterministic recency sort. Do not filter only the
flattened All list: `_flatten_hosts` truncates that list to `max_sessions`,
which can hide older running conversations. Show all active rows available in
those bounded host snapshots without applying the All page's recency cap.
Discovery limits and process-probe limitations still apply; this page is not
an independent inventory of every possible provider process.

Refresh uses the existing committed snapshot and observation rules. Cached
rows remain visible while checking; the complete new snapshot updates the
list together. Preserve the currently selected conversation if exactly one
matching visible row survives. If it stops being active or disappears, select
the first eligible matching row. Refresh does not move the current page.

An empty Active page shows a non-actionable row such as
`No active sessions observed · Left/Right: change page`. Existing checking and
failure notices remain visible. Failed or unreachable hosts must not be
presented as proof that their conversations stopped. Keep the empty page in
the ring so navigation and saved preferences do not depend on activity.

## Remembered context

Remember two values per viewing endpoint:

| Value | When it changes | How it is restored |
| --- | --- | --- |
| Last page: Active, All, Local, or a logical host ID | On an explicit page change and a successful Enter action | Normalize against the current page ring and host catalog. |
| Last successfully used conversation: `(hostId, provider, session ID)` | After a successful Resume or New session here action | Select exactly one matching visible row on the initial render. |

Remembering the page also remembers the choice of Active. There is no separate
Active-only flag. Keep one last-used conversation identity, without a
per-page selection map. For New session here, remember the source row as the
navigation anchor: the new TUI's conversation ID is not yet available.
Both the guarded Resume fast path and the ordinary action path must update
the preference after success; failed actions do not replace the last-used ID.

Each new dialog starts with an empty search and Resume selected. Within an
open dialog, the existing search, action, and refresh continuation behavior
still applies. Explicit page changes reset selection; returning to another
page within that dialog does not restore a separate bookmark for that page.

If a saved host no longer exists, fall back to All when available, otherwise
Local. Active remains a valid saved page even when empty. If the saved
conversation is missing, outside the recent-list limit, or absent from the
chosen page, select the first eligible row. Do not expand discovery or switch
pages to find it. Keep the saved identity until a later successful action
replaces it. Apply the bookmark once on opening; later refresh callbacks
preserve the user's current selection rather than reapplying it.

Escape and Ctrl+G keep their native cancellation path. Merely moving through
rows and cancelling does not update the remembered conversation, because
there is no final selection callback. Explicit page changes have already
saved the page, so they survive cancellation. The feature remembers the last
successfully used conversation, not every highlighted row.

## Local preference storage

Use a small versioned JSON record at
`$XDG_STATE_HOME/rofi-agent-plus/view.json`, defaulting to
`~/.local/state/rofi-agent-plus/view.json`. Store only the page and last-used
conversation identity. This is local UI state, separate from provider
snapshots and refresh markers. No cross-endpoint synchronization is required.

Validate the record's version, page kind, logical host ID, provider, and native
session ID using the existing identity rules and bounded input sizes. Missing,
malformed, or unsupported records use the initial defaults. Normalize valid
preferences against current discovery before displaying them.

Write atomically with a private directory (0700) and file (0600). Writes are
best effort: an unwritable preference file must not prevent navigation,
Resume, or New session here. A write failure after a successful action must
not turn that action into a failure or cause another open/create attempt.
Automatic refreshes, timeout callbacks, initial rendering, and diagnostic CLI
commands do not write preferences. If two dialogs are used concurrently, the
last explicit preference update wins.

Saved IDs are navigation hints only. Resolve the selected row from the current
snapshot and keep the existing lifecycle revalidation. Do not persist tmux
references, Mesh revisions, provider-option proof, process evidence, or
terminal commands in this record.

## Ownership

Agent Plus owns these pages and preferences. Providers own conversation
history; tmux and Tmux Plus own running-session lifecycle; SSH Plus owns hosts
and routes. The design uses the current Host Mesh v1 and Tmux Session v1
operations. Its private presentation state does not become a public discovery
contract or alter the diagnostic CLI's list shape.

The [managed-session limits](SUITE_INTEGRATION.md#agent-plus-retains) apply:
one foreground provider TUI per ordinary tmux session, with provider-native
conversation switching inside that TUI outside the managed contract. Active
uses existing external process evidence. This feature adds no provider hooks,
daemon, internal session manager, or tracking of native lifecycle transitions.

## Implementation order

1. **Active page.** Extend the private navigation model and continuation
   encoding for Active while accepting existing All/Local/host state. Update
   canonicalization, the page ring, row collection, empty states, and every
   refresh/error render path. Reuse the activity predicate and existing sort.
2. **Remembered context.** Add a small preference module, load it only for an
   initial Rofi invocation, restore page and selection across all initial
   render paths, and save on explicit page changes and successful actions.
   Keep the diagnostic CLI and detached refresh worker independent of it.
3. **Documentation and release.** Update current-behavior sections in README
   and Suite Integration when the implementation lands, add meaningful
   regressions, and run the source and exact candidate gates. Publish a new
   Agent Plus release and update the managed pin through the normal suite
   process; the Active page requires no new managed keybinding. Record
   installed and manual acceptance separately in the suite status ledger.

## Acceptance

Implementation acceptance must cover:

- Active/All/Local/remote wraparound, and Active/Local on a local-only Mesh.
- Active remains navigable when empty or a host is unreachable; current
  failures remain visible and do not become false stopped-session claims.
- An older running conversation outside the flattened All cap appears in
  Active; duplicate cached identities appear once. Activity-only rows and
  rows with limited details keep their existing labels and action checks.
- Failed activity evidence is excluded, while existing supported activity
  matches across Codex, Claude Code, and OpenCode retain their meaning.
- Page changes remain free of provider/tmux/SSH queries and preserve search.
  Refresh preserves a surviving selected identity even when rows reorder;
  loss of that identity has the documented fallback.
- Reopening restores Active or a host page and its last-used conversation.
  Removed hosts, missing rows, invalid records, and unwritable state all have
  the documented fallback. Search starts empty and the action is Resume.
- Successful fast-path Resume and ordinary Resume both save context. New
  session here saves its source anchor; failed actions do not replace it.
  Preference-write failure after success never causes another lifecycle call.
- Native cancellation preserves a deliberately changed page without claiming
  to save an arrow-only selection. Background refresh and diagnostic CLI
  commands leave the preference file untouched.

After automated gates pass, verify repeated open/page/change/refresh/cancel
interactions through the exact managed Mod+A invocation on Snap and Starship.
Check the prompt, empty/failure notices, restored row, and focus behavior in
the real UI. Those observations supply manual acceptance; source and live
self-tests alone do not establish it.
