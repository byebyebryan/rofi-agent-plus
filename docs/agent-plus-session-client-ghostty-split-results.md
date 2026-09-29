# Agent Plus session client: one-tmux Ghostty split results

Dates: 2026-09-27 to 2026-09-28

Status: feasibility study complete. The operator accepted the fixed 32-column
rail and saw no flicker during native streaming and switching at normal wide
size. The 80x24 stress window left only 47 columns for the native TUI, which
the operator would not use at that overall size; this is a whole-window size
limit, not a rejection of the rail. The one-tmux local path passed, including
an equal-geometry traffic comparison. Real-window Starship attach verification was
intermittent, and no supported external Ghostty split/focus/width control route
was established. This is disposable presentation evidence, not a client
implementation or an Agent Plus contract change.

## Decision state

One Ghostty window can keep a flat rail visible beside a direct attachment to
an ordinary tmux session. Local synthetic A -> B -> A and an isolated native
Codex -> synthetic A -> the same Codex process passed at 80x24. The provider
still runs in its own ordinary tmux session, with one tmux renderer between it
and Ghostty. A 32-column rail leaves 47 columns for the viewer at 80x24. The
native welcome/input screen rendered, with some text truncated. The operator
later clarified that they would not make a normal TUI window this small and
that the fixed 32-column rail itself feels right.

The operator saw no flicker while native Codex streamed in a wide 32/108-column
split, and reported that N -> A -> N switching, focus, paste, mouse, and scroll
worked. This is a positive visual result for that geometry, not a finding about
every terminal size. In the bounded synthetic redraw workload, the direct
attach and split viewer sent identical bytes and cursor-control counts at the
same 47x24 viewer size.

Ghostty startup, fixed rail width, and focus can be driven in this installed
build, but the external interfaces found so far are version-specific or global
key injection. A supported, targeted external split/focus/width route remains
unproven. Starship's ordinary tmux process survived every SSH cut, but a real
Ghostty reconnect intermittently timed out during identity verification; retry
worked. The harness then stopped its still-running SSH viewer child, whose
exit status was 255. That status does not establish the root cause.

The presentation topology is mechanically feasible and avoided the reported
flicker at the operator's wide test size. The fixed 32-column rail is accepted.
The original plan's 80x24 usability condition was stricter than the operator's
actual window-size requirement; its result establishes a stress-size limit,
not a layout failure. Further design assumes a reasonable native-TUI window
size. Reliable remote validation/reconnect and a defensible
Ghostty startup/focus/width control route remain unproven, so this spike does
not authorize a client implementation.

## Gates

| Gate | Result | Evidence and limit |
| --- | --- | --- |
| 0. Split, width, focus | Geometry pass; stress-size limit | Manual Ghostty split/focus keys worked. At 725x500 px the window provided 80x24 cells: 32x24 rail and 47x24 viewer after divider adjustment. Wide and return sizes could be restored to 32 columns. The operator accepted the fixed rail and would not normally use a TUI in an 80x24 whole window. External focus has no verified stable API. |
| 1. Local A -> B -> A | Pass | Five exact marked local fixtures stayed alive. Both the PTY rail gate and a real Ghostty window switched A -> B -> A; selected card, viewer, and input destination agreed. Same-name stale E was refused. In the wide live replay, N -> A -> N returned to the same Codex PID and the operator reported comfortable switching and correct focus/input. |
| 2. Display and native TUI | Wide visual pass; 80x24 stress-size limit | An isolated Codex 0.157.1 TUI attached at 47x24, accepted a benign typed request, produced six distinct sampled screen states, changed while hidden, and returned to the same PID. Tmux copy mode and resize worked. At equal 47x24 viewer geometry, direct and split traffic metrics were identical in a bounded synthetic redraw. A later Codex 0.158.0 operator replay found no streaming flicker and working paste, mouse, and scroll at 32/108 columns; at 32/47 columns the UI still worked mechanically, but the whole window was smaller than the operator would use for a native TUI. |
| 3. Remote recovery | Partial | The 80x24 PTY gate passed A -> R -> A, exact SSH-child cut, same-process reconnect, injected attach refusal, local use while unreachable, and retry. Six further PTY cycles passed. In real Ghostty, one attach verification stalled for roughly 12 seconds; a focused replay passed four cut/reconnect cycles before another stall in the remote `tmux show-options` marker block. The rail/supervisor survived after harness timeout handling was fixed; local A remained usable and a later R retry attached the same process. The marker-block stall remains unexplained. |
| 4. Startup and cleanup | Partial | A fresh split relaunched after a window-close test. Exact Ghostty children exited and local fixtures survived. The abrupt close left a stale supervisor state file/socket, which the next supervisor launch recovered. The initial cleanup restored both server fingerprints. The operator replay removed every marked fixture and the private auth copy, but an unrelated unmarked tmux session changed during that replay, so its final non-fixture fingerprint differs. Supported targeted startup/focus/width automation remains unproven. |

## Topology and local identities

The temporary study under `/tmp/agent-plus-ghostty-split-055b05bea1` ran a
fixed-card rail in the left Ghostty surface and a viewer supervisor in the
right. The supervisor used a private Unix socket and started only foreground
`tmux attach-session` clients. The normal local tmux server retained the
synthetic and native fixture processes; no presentation tmux server was used.
No provider hooks, Agent Plus cache, Rofi configuration, or managed targets
were changed.

The installed versions were Ghostty `1.3.1-arch2`, tmux `3.7c`, and Codex CLI
`0.157.1`. Local default-server generation was
`/tmp/tmux-1000/default|15598|1790268785`. Before fixture creation it had nine
sessions; its non-fixture ID/name/creation/window-count fingerprint was
`b4f225c6e20f7b03e576f746d90220b5bc4d2097ed414b10e45395ea45e3b0cb`
and the tested option fingerprint was
`a416b14e122a58ace4c8fa485e17d37fc2a89b0f8e5b14dd881f376b93cacb8e`.
The after-cleanup check matched both exactly after marked A-E were removed.

Before creating the remote fixture, Starship's default-server generation was
`/tmp/tmux-1000/default|89474|1790541046` on tmux `3.7c`. It had two
non-fixture sessions and two windows, with session fingerprint
`3f76850be8dec18259ab9553dda0925116d949bce0fa541793140ca370683661`,
non-fixture option fingerprint
`0df45c06f7d1dbee18555f58553671eda5da3e423d09cfa2142756025039b672`,
and server option fingerprint
`6f18969fdcd4506efb417db34a7be581e5ca5003b002929e4c50c7924e719448`.
These were captured before the marked Starship R session was created. The PTY
gate compared non-fixture sessions and options while R was live, and the
after-cleanup check matched the pre-fixture generation and all three remote
fingerprints exactly.

Local A was session `$44`, pane `%46`, synthetic PID `783444`; B was `$45`,
pane `%47`, PID `783469`. C, D, and E were separate marked peers in the same
test directory. The old E reference (`$48`) was replaced with a new marked
session of the same name (`$49`) and refused by exact session identity. The
local PTY gate verified per-run input increments, continuing hidden
heartbeats, old viewer-child exits, and viewer detach/reopen without replacing
the rail or supervisor. It saved no terminal output.

## Ghostty geometry and control

The normal `Ctrl+Shift+O` split action created the second surface and started
the configured viewer command. The initial 80x24 split was 39/39 columns.
Seven `Super+Ctrl+Shift+Left` resize actions from the right pane produced a
32/47 split; `Ctrl+Alt+Left` and `Ctrl+Alt+Right` reliably moved keyboard
focus. In the real synthetic window, input after those gestures reached the
selected fixture. From an initially focused viewer, the exact automated
sequence was `Ctrl+Alt+Left`, one rail arrow, `Enter`, `Ctrl+Alt+Right` for
each adjacent switch.

At an outer width of 1080 px, Ghostty kept the split **ratio** and changed
32/47 to 48/70. Moving its GTK accessibility `Value` divider to pixel 295
restored 32/86. Returning the outer window to 725 px first gave 21/58;
setting the divider to 295 again restored 32/47. The divider setter worked
when the test window was not focused. Tmux content added extra accessibility
`Value` nodes for scrollbars, so the controller had to find the splitter panel
by role and range, not assume there was one Value interface. A right-pane
font zoom also changed its cell geometry in the inert gate and could be reset.

The identified test window's GTK `split-right` D-Bus action created a split in
an earlier inert probe and in the operator replay. In that replay, desktop
focus moved away after an attempted global key injection, so the targeted GTK
action avoided sending a split key to another window. The test-only controller
set the divider to pixel 295 through GTK accessibility; Niri's window-ID
actions changed the outer window size. Ghostty's installed CLI has no
documented `+split` action. Accessibility `grab_focus()` on the test pane nodes
failed, and the window action list did not expose a targeted focus or resize
action. A chained
keybinding that tried `goto_split:left`, text, then `goto_split:right` sent the
text to the original right pane, so it did not supply a one-key selection
gesture. This leaves manual Ghostty bindings, a version-specific GTK action
and accessibility divider, or global key injection after exact-window focus as
the demonstrated control routes. None is accepted here as a public Ghostty
automation contract.

The first temporary rail used the normal screen. After a window-width change,
old rail text remained visible above the new redraw. Entering Ghostty's
alternate screen in the **temporary rail** removed that persistent ghosting;
the screen was clean after the 0.5-second geometry poll settled. This is a
rail implementation detail observed during the spike, separate from the
operator's provider-streaming flicker concern.

## Real-window local and native checks

The real 80x24 synthetic-window run delivered A input counts `4 -> 5 -> 6`
and B `4 -> 5`. The rail selection and viewer target agreed after each
switch. Automated time from focus-left to attached target was 110 ms A -> B
and 169 ms B -> A; these timings do not represent human comfort or visual
readiness. Closing that exact Ghostty window while A was viewed displayed
Ghostty's normal confirmation. Confirming it removed the test Ghostty, rail,
supervisor, and tmux client, while the marked A process survived. The
supervisor's state file still said `viewing` and its socket path remained;
the next launch removed the stale socket and attached successfully. A later
controlled `stop` followed by rail `q` exited cleanly.

For the native trial, an empty scratch workspace and private temporary Codex
home were used with only a mode-600 copy of authentication. The process ran
`codex --no-daemon` with read-only sandbox and no approvals. Its disposable
session was `$50`, pane `%52`, Codex PID `835671`; the process and start ticks
stayed the same through native -> A -> native. The initial native screen was
legible at a 32-column rail plus 47-column viewer. A benign typed list request
caused six distinct sampled screen states; the native pane changed while A was
viewed, and A received its own input. Automated attach-ready times were 146 ms
native -> A and 236 ms A -> native. No prompt or response text was retained
as study evidence. Tmux `Ctrl+B`, `[` entered copy mode (`pane_in_mode=1`);
`q` exited it. Escape alone did not exit under the current tmux mode keys.
Resizing to 1080 px and back restored rail/viewer cell sizes 32/86 and 32/47,
with the native tmux pane at 86x23 and 47x23; the Codex PID remained stable.
The exact native session, process, scratch home, and copied auth were then
removed. Its pre-prompt screenshot contains no conversation output.

Paste and mouse/scroll gestures were not injected into the unattended desktop:
doing so would require changing the operator's clipboard or pointer state.
The later operator replay covered those gestures and the visual judgment.

## Operator visual replay on 2026-09-28

A fresh isolated Codex `0.158.0` process ran in marked local session `$56`,
pane `%58`, PID `2992161`, with a private scratch workspace and a mode-600 auth
copy. A dedicated Ghostty window used a 32x71 rail and 108x71 viewer at
1280x1432 px. The operator sent a harmless prompt, watched the native response
stream, switched N -> synthetic A -> N, and tried paste and mouse/scroll. They
reported that everything worked and there was **no visible flicker**. The
supervisor events show exact N -> A -> N viewer-child changes, and the original
Codex PID revalidated after return. No prompt or response text was saved.

The same window was then resized to 725x500 px and its divider restored to a
32x24 rail plus 47x24 viewer. The operator reported that the mechanics still
worked, but they would not normally resize a native TUI window to 80x24. They
clarified that the fixed 32-column rail is perfectly fine; this stress test
found a total-window-size limit, not a rail-layout defect or renderer/input
failure. The operator did not give a separate narrow-size flicker verdict.

## Equal-geometry terminal traffic

A separate 47x24 PTY attached directly to marked local A, then used the
split's foreground viewer to attach to that same A. The bounded workload ran
65 exact `tmux refresh-client` calls over four seconds in each mode, after
draining the initial attach frame. It compared only terminal control/output
bytes, not pixels or perceived flicker. The attached viewer stream was
identical in all three modes:

| Mode | Viewer bytes | CSI sequences | Cursor moves | Cursor visibility | Rail bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct ordinary tmux attach | 17,205 | 2,145 | 130 | 130 | 0 |
| Split viewer, quiet rail | 17,205 | 2,145 | 130 | 130 | 0 |
| Split viewer, rail redrawn every 0.5 s | 17,205 | 2,145 | 130 | 130 | 6,520 |

All four split-to-direct viewer ratios were `1.000`; the updating rail's
separate stream contained 56 CSI sequences, eight cursor moves, and eight
cursor-visibility changes. This is consistent with one tmux renderer feeding
the viewer while Ghostty draws the rail separately. It establishes no visual
flicker verdict for active native agent output.

## Starship recovery and real-window finding

The marked remote R fixture was ordinary Starship tmux session `$2`, pane
`%2`, with synthetic PID `386273` and process start ticks `2433810`. The
80x24 PTY gate used the same rail and viewer code as the local test. It
switched A -> R -> A, cut only the identified SSH viewer child, reattached
to the same session and process, injected a one-shot refusal before another
R attach, kept A selectable and accepting input while R was marked
`unreachable`, then retried R successfully. The R heartbeat advanced across
detach and cut. The rail recorded `viewing`, `detached`, and `unreachable`;
R had zero clients when the PTY gate exited. Starship's non-fixture session
and option fingerprints still matched the pre-fixture snapshot. The gate
saved counts, IDs, and statuses, not terminal output.

In a dedicated real Ghostty split at 32x24 rail and 47x24 viewer, selecting
R attached the exact remote client and typing into the right pane advanced
R's input count from zero to one. The same PID and process start ticks
remained. Cutting the exact SSH viewer child left R running without a client,
and the R card visibly changed to `detached` while the rail and supervisor
processes remained. The rail footer still said `viewing` and the right pane
kept the last remote frame plus SSH's connection-closed line. Those stale
presentation elements are visible cleanup work for a later client.

The first real-window reattach after that cut failed its SSH verification.
The rail socket's 1.5-second deadline was shorter than that remote operation;
after its request disconnected, the supervisor also exited. The temporary
harness was changed to wait 20 seconds and to treat a disconnected socket
reply as nonfatal. A fresh Ghostty run then attached R, cut its SSH child,
reattached, and delivered another input event to the same R process. A further
bounded cut/reconnect failed again: one remote identity query returned
`unreachable` after roughly 12 seconds, matching its configured timeout. The
SSH viewer child was still running when the supervisor stopped it; the child
then exited `255`. A later identity query succeeded with zero R clients.
Starship's SSH journal shows that it accepted both the viewer and query
connections at the start of the failed attempt. The evidence therefore does
not identify a transport failure; the cause of the identity-query stall is
unknown. With the timeout handling fixed, the rail and supervisor stayed
alive, showed `unreachable`, and switched to
local A, which accepted input. A later retry attached the original R process
without replacing either local presentation process.

A focused replay created a new exact marked R session (`$3`) after both
servers' original baselines revalidated. Six 47x24 PTY attach/cut cycles
passed, each with one matching viewer client during attachment and zero
clients after the cut. In a new 32/47 real Ghostty split, four cut/reconnect
cycles passed; the fifth again timed out during remote validation. The
content-free stage trace reached `markers`, the block that issues the two
remote `tmux show-options` checks, and did not reach `panes`. The viewer SSH
child was still running at the deadline; the harness stopped that exact child
and it then exited `255`. This narrows the observed stall to the remote
marker-validation block, without identifying which command or why it waited.
The rail and supervisor remained usable. The second R was then removed after
an exact identity check, and Starship again matched its original baseline.

The test-only route-failure injection in the PTY gate refused the next R
selection before attach. It proved state handling and local fallback, but it
was not a real network outage or a laptop sleep/wake test. The observed
identity-query stall supplies a separate real failure path. Neither result
proves automatic recovery across laptop sleep.

## Cleanup audit

After the first GUI run, R had zero clients. Its exact marked session `$2`, app
PID `386273`, and private Starship fixture directory were removed after
another identity check. The later diagnostic R (`$3`, PID `420047`) was also
removed after the trace run. Starship returned to two sessions and two windows
on the same server generation, session fingerprint, non-fixture options fingerprint,
and server options fingerprint recorded before R existed.

Before local cleanup, A-E all revalidated, the old same-name E reference still
failed validation, and the local non-fixture baseline matched. The cleanup
removed only sessions `$44`, `$45`, `$46`, `$47`, and `$49`; their fixture
processes exited. The local server returned to nine sessions on its original
generation with session fingerprint
`b4f225c6e20f7b03e576f746d90220b5bc4d2097ed414b10e45395ea45e3b0cb`
and options fingerprint
`a416b14e122a58ace4c8fa485e17d37fc2a89b0f8e5b14dd881f376b93cacb8e`.
The test Ghostty windows, viewer children, rail processes, and private socket
were absent. No Agent Plus or other user session was attached, renamed, or
removed by that unattended run.

For the operator replay, fresh marked synthetic sessions `$51` through `$55`
and native session `$56` were removed after exact identity and zero-client
checks. The Codex process exited, its private auth copy and scratch workspace
were deleted, and no marked or study-prefixed session, private socket, or test
Ghostty window remained. The two private `/tmp/agent-plus-ghostty-*` study and
GUI-evidence directories were then removed.

The local server generation, nine-session/nine-window counts, and tested
options still matched the pre-replay baseline. The non-fixture session
fingerprint did **not** match: it changed from
`b4f225c6e20f7b03e576f746d90220b5bc4d2097ed414b10e45395ea45e3b0cb`
to `313ae3330aad5974711c0029ffaa1eab170d76b968e7225e607bf3f22ceff0ea`.
An unmarked session `$57` appeared during the live replay; at least one prior
non-fixture entry was no longer in the nine-session set. The study did not
create, attach, or remove `$57`, and it preserved all ordinary sessions at
cleanup. The earlier baseline was not restored by this second run.

## Design implications

1. Keep the fixed 32-column rail as an accepted design input and assume a
   reasonable window size for native TUI work. The 80x24 stress viewport is
   historical evidence, not an open minimum-size requirement. The wide
   one-tmux layout avoided the reported flicker in this replay.
2. Before a client implementation, investigate the intermittent remote
   marker-validation `tmux show-options` stall and identify a supported,
   targeted Ghostty startup/focus/width control route.
3. A controlled SSH-child cut and retry did not test laptop sleep/wake
   recovery; that remains a later acceptance scenario.

The [test contract](agent-plus-session-client-ghostty-split-spike.md)
remains the source for stop conditions and the later design decision.
