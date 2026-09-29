# Agent Plus session client: ordinary-tmux presentation spike results

Date: 2026-09-26

Status: **synthetic gates 0–4 completed; native gate 5 partially observed.**
This is a disposable feasibility record, not a product design approval,
implementation, or deployment.

Later operator feedback: WSNav's two-tmux presentation produces visible cursor
flicker during active native TUI use, and the operator finds it very annoying.
The ordinary-tmux spike used the same nested rendering topology but did not
reproduce a live streaming interaction in Ghostty. The 80×24 still image cannot
clear this known usability problem.

## Outcome

The WSNav-style persistent rail can switch a real nested tmux client among
marked sessions on the ordinary local and Starship default servers. The
synthetic applications survived viewer switching, outer detach/reattach, and a
controlled SSH child loss. Action-time fixture validation refused a stale
session reference after its name was reused. The ordinary server's window and
pane selection is shared between viewers, and the measured nested PTY stream
was substantially larger than direct attach for this synthetic churn case.

An isolated Codex TUI also survived A→B→A switching and rendered in Ghostty at
80×24 with a fixed 32-column rail and 47-column agent pane. The native TUI was
legible in a window capture, but text wrapped more and header/status details
were truncated. The operator confirmed from the 80×24 image that WSNav's
32-column rail width feels right; hands-on daily-use acceptance of the narrow
Codex pane remains open. A separate lifecycle issue remains: switching away
respawns the private viewer pane and kills its SSH child, but leaves the remote
card labeled `connected` until another attach or explicit loss event updates
it.

**Decision:** ordinary-tmux attachment through a private presentation tmux is
technically feasible, but the two-tmux renderer is not an acceptable default
for a new client given the operator's WSNav flicker experience. The operator
reported that the 32-column rail width feels right; retain the always-visible
rail design while studying a presentation that draws the rail without another
tmux renderer in front of the agent. A client-owned terminal surface beside
the rail is one candidate, but its native-TUI fidelity is untested. This is a
new feasibility question. Client implementation is not approved. A later
design must also define exact conversation/window/pane identity and reconcile
viewer status after switching.
If the nested tmux option is reconsidered, it needs a current native streaming
trial showing the flicker is gone or acceptable to the operator.

## Environment and safety

Both ordinary servers ran tmux 3.7c. Ghostty 1.3.1-arch2 was installed. The
local environment exposed `WAYLAND_DISPLAY=wayland-1`, `DISPLAY=:1`, and
`XDG_RUNTIME_DIR=/run/user/1000`. Gate 5 used one disposable local Ghostty
window. The Starship test shell had no GUI display and was used only over
existing SSH trust.

Before creating fixtures, the local default server contained 9 sessions and 9
panes; Starship contained 4 sessions and 5 panes. Both had the same relevant
global option values: prefix `C-b`, prefix2 `None`, status on, mouse on,
window-size `latest`, escape-time 10, and focus-events on. The presentation
used its own private tmux server and socket. No global option, key table, hook,
SSH trust setting, or unrelated SSH client was changed.

| Default server | Version | Before sessions/panes | Before and after session fingerprint | Before and after options fingerprint |
| --- | --- | ---: | --- | --- |
| Local | tmux 3.7c | 9 / 9 | `f45204ab8e04ef1b224d45828c97f817c43dd45cedce528a90517025d690a016` | `0a1270637cc419167f3006ba4e2e231251e6b8a9119d1465fb790d23bf6bf5fd` |
| Starship | tmux 3.7c | 4 / 5 | `4546e05c6fee615b0054056706d594ca946f77b8d06bedaa0f1b4a4b6d979d3e` | `0a1270637cc419167f3006ba4e2e231251e6b8a9119d1465fb790d23bf6bf5fd` |

The study marked five local synthetic fixtures (A–E), then one Starship
fixture (R). Each attach and cleanup revalidated the server generation,
session ID/name/creation tuple, marker, exact window and pane, process PID and
start ticks, executable, environment marker, and heartbeat. Fixture A also
had a second window with two panes. The study never attached to a pre-existing
user session. While fixtures were present, local tmux held 14 sessions and 16
panes; Starship held 5 sessions and 6 panes. Those deltas account for the five
local fixture sessions plus A's two extra panes and the single remote fixture.

## Gate results

| Gate | Result | Evidence and limits |
| --- | --- | --- |
| 0. Baseline and fixture safety | **PASS** | Baselines above were captured before fixture creation. Local and remote fixture identities were distinct and marked. Presentation settings were private. Exact pre-existing session and option fingerprints matched after cleanup. |
| 1. Persistent rail and local switching | **PASS; visual observations in gate 5** | A 129×36 presentation PTY kept all five cards visible. Left-pane Down/Enter switched A→B; right-pane outer `C-b` Up/Down commands switched the nested viewer B→A→B→A. A separate PTY sequence exercised left-pane Down/Enter and Up/Enter. Rail routing passed the invoking `#{pane_id}` and client name; the key-event trace recorded source pane `%1` and its attached presentation client. Selection and focus were separately observed: the selected card changed while the active pane moved between rail `%1` and viewer `%0`. The PTY driver's first retained local TUI marker appeared 0.0025 seconds after the A-attach run began; that is byte availability, not an operator-ready latency. The selected app received test input, and A/B PIDs and heartbeat counters continued while hidden. At 80×24 the unhooked layout collapsed the rail to 7 columns and was structurally unusable. A private-only client-attached/window-resized width hook restored a 32-column rail and 47-column viewer; the 80→129→80 resize retained that split and all cards. This gate proves geometry and captured terminal output; native Ghostty observations are in gate 5. |
| 2. Nested input and display | **PASS for input; presentation not accepted** | The outer private prefix was `C-b`; its `C-b` binding used tmux `send-prefix`, forwarding the prefix into the ordinary nested client. With the ordinary server's actual prefix `C-b`, the PTY exercised inner copy-mode entry/exit, modified keys, F2, bracketed paste, mouse click/wheel, repeated resize, and literal `C-b` input. The synthetic app logged those sequences. The inner `pane_in_mode` state changed 0→1→0. In a bounded 120-frame churn segment at 96×36 agent geometry, direct attach emitted 7,089 bytes / 600 CSI / 360 cursor-motion sequences. The nested presentation emitted 39,461 bytes / 5,104 CSI / 4,596 cursor-motion sequences: 5.57× bytes, 8.51× CSI, and 12.77× cursor motion. The capture started at frame 000 and ended at the first `CHURN-END`; the rail renderer was SIGSTOPed for the nested capture and its PID/start identity was checked before resuming it. This removes its periodic redraw writes, but remains a synthetic PTY measurement, not a Ghostty visual comparison or a directly comparable repeat of the earlier 2.254× study. The operator's annoying WSNav flicker is separate live-use evidence against carrying this same two-renderer topology forward by default. |
| 3. Ordinary-session behavior | **PASS with shared-state constraints** | Two viewers at different sizes observed the same ordinary session. Changing the active window or pane in one viewer changed the server-wide selection seen by the other. The second viewer was 80×24 while the nested viewer was 96×36; resizing it to 88×26 made the active window 88×25 with two panes at 48×25 and 39×25. The active window can therefore show a different process than a session-level card suggests; inactive windows can retain stale dimensions until activated. Rail A→B→A restored A's active window index but does not promise a particular pane or conversation. A stale E tuple was saved, the marked old E session was removed, and the same name was reused by a new session. The saved tuple was refused with exit 2 (`session ID/name/creation tuple is absent or changed`); the new tuple validated. The first attempt hit a disposable harness manifest-entry setup bug; after verifying the old session/process were absent, the harness entry was repaired without changing the saved stale tuple and the intended check passed. |
| 4. Remote loss and recovery | **PASS for process survival/reconnect; partial lifecycle failure** | The private rail attached to Starship R through existing SSH trust. Switching to local removed the test viewer; remote client count fell to zero while R's app PID and heartbeat continued. A controlled kill of only the validated test SSH child also dropped the remote client to zero while preserving the remote tmux server generation, session, app PID, and heartbeat. The rail remained usable, showed unreachable, and reattaching the exact fixture restored a viewer to the same app process. The PTY driver's first synthetic marker appeared 3.86 seconds into the reconnect run; the driver had attached to the outer presentation before issuing Enter, so this is elapsed PTY time rather than a direct GUI interaction latency. An injected pre-attach route failure refused before attach and showed `unreachable (injected)` while R continued running. Outer presentation detach/reattach also preserved the selected remote viewer and remote process. **Failure:** switching away kills the adapter child during private-pane respawn, before the child can publish `detached`; the card remained `connected` even though the remote client count was zero. Controlled SSH loss did publish `unreachable`. This is a test-adapter lifecycle gap that a later client must address. No laptop sleep/wake test was run. |
| 5. Native TUI and operator check | **PARTIAL; rail width accepted, full TUI acceptance open** | A disposable Codex CLI 0.157.0 conversation ran with an isolated Codex home and empty temporary workspace. Direct attach displayed the native TUI; A→B→A through the private rail kept the Codex PID alive and returned to the same process. A real Ghostty window at 80×24 rendered 32 columns of rail and 47 columns of Codex. The prompt, reply, and input field were legible; the narrow agent pane wrapped text and truncated header/status details. Wide Ghostty rendering also worked. The operator confirmed from the image that WSNav's 32-column rail width feels right. This did not exercise a native streaming response, mouse/copy/paste, laptop reconnection, or hands-on operator judgement of the full TUI. |

The exact synthetic input evidence was retained in the temporary study directory
at the time of this run,
including the A→B→A PTY streams, inner-key stream, two-viewer traces, stale
reference, route refusal, SSH-loss, and reconnect traces. These synthetic
captures contained no provider prompt, credential, or conversation output.
The temporary directories were subsequently removed; no replay harness is
versioned here.

The native trial ran on local ordinary server generation
`/tmp/tmux-1000/default|15598|1790268785`. It created marked session A
`$42` with Codex pane `%44` / PID `442507`, marked mock B `$43` with pane
`%45` / PID `447373`, and a separate private presentation server. The native
session had no clients after direct detach but Codex remained alive. The
presentation attached to A, switched to B, then reattached A; the selected
card and ordinary-server client counts followed the switch while PID `442507`
remained alive. The Ghostty window was resized to exactly 80×24; tmux reported
rail `%1` at 32×24 and agent `%0` at 47×24. A wider Ghostty window also
displayed the same native TUI and rail. This validates one native provider and
local switch only; the remote native-provider case remains untested.

## Cleanup and final state

Cleanup revalidated each current marked fixture before killing it. The stale
old E and the same-name replacement E were separately verified; the new E was
the only E fixture removed by the normal cleanup path. Local A–E, Starship R,
the private presentation server, its rail/viewer processes, all test SSH
children, and the unique remote study directory were absent after cleanup. The
temporary study artifacts were later removed from `/tmp`; no fixture server or
client remained live after cleanup.

Two cleanup-helper defects required exact manual recovery. The remote directory
helper initially passed a malformed quoting form; inspection showed only the
study's synthetic app and heartbeat in its unique directory, with the exact R
session and app PID absent and the Starship server generation unchanged. The
directory was then removed with remote `/usr/bin/python3` and a direct argv
call. The private tmux server had exited but left its socket path; the exact
path was an owned Unix socket, its PID was absent, and a connect returned
`ECONNREFUSED`, so only that stale socket was unlinked. The harness helpers
were corrected for replay. Final local and Starship session counts and both
fingerprints exactly match the before values above.

The native follow-up closed its exact Ghostty client, then revalidated the
private server and both marked ordinary sessions by generation, session tuple,
marker, pane, process PID, and start ticks before removal. Both test PIDs and
the private server exited. The local ordinary server returned to its native
trial baseline of 9 sessions; its session fingerprint over
ID/name/creation/window-count rows
`cf2283b74c6cc9fb04637468b3c562c9edd41dca19ae838a328fff2d961a4b0a`
and tested prefix/mouse/window-size option fingerprint
`edc5c0e205a3b6e4f80dcfac17a4eee59a65aa07397ab3d0f1f40cf50f8636fd`
matched exactly. These use a narrower input format than gate 0, so their
hashes differ. The temporary copy of Codex authentication was removed.

## Replay handoff at the time of the study

A private replay copy and the original synthetic evidence were available in
temporary directories after the initial cleanup. Both directories have since
been removed. This repository records the test contract and results, but does
not contain a runnable replay harness. A new run would need fresh marked
fixtures and the same exact-identity cleanup checks.

## Scope limits

This study proves behavior only for tmux 3.7c, these two ordinary default
servers, the synthetic TUIs, one isolated local Codex TUI, and the captured
PTY/Ghostty observations. It does not prove native-provider conversation
identity or resume semantics, complete session
discovery, duplicate-free resume, persistence of a working set, safe behavior
across laptop sleep/wake, independent per-viewer active windows, small-screen
acceptance of the narrow native TUI in daily use, a public attach operation,
or Rofi integration. The 32-column persistent rail remains a promising UI
element; the two-tmux presentation is not accepted for this client. The next
bounded study should keep the rail visible while exposing the existing session
tmux through only one renderer. A later design pass must resolve connected-state
reconciliation after viewer switching and the meaning of a session card when
ordinary tmux has multiple active windows and panes.
