# Agent Plus session client: Kitty one-tmux spike results

Date: 2026-09-28

Status: the second Starship live replay completed. Direct ordinary-tmux viewing,
the persistent rail, native Codex streaming, and Starship -> Snap reconnect
worked at a realistic window size. A stale-selection test exposed one ordering
gap in the disposable supervisor; see below.
This record follows the [bounded plan](agent-plus-session-client-kitty-spike.md).
It records a disposable presentation study, not a client implementation or
deployment.

## Scope and preflight

Starship is the viewing endpoint; Snap is the remote fixture host. Both had
Kitty 0.49.1 and tmux 3.7c. The configured Starship -> Snap SSH route
responded. The existing ordinary tmux servers were Starship PID 14042 and
Snap PID 6691. Their pre-spike session identities and options were saved in
the private test directory before any fixture was created.

The first attempt used a dedicated Starship Kitty process (PID 391052), started under
the transient user unit `agent-plus-kitty-spike-vOlSNj.service` with a private
Unix control socket in a mode-700 temporary directory. It did not enable
remote control in the managed Kitty configuration. The process exposed one
Kitty OS window, one `splits` tab, and two test-owned panes. Starship Niri
identified that OS window as ID 18.

## First Starship attempt: gate status

| Gate | First-attempt result | Unmet at that point |
| --- | --- | --- |
| 0. Startup, geometry, focus | Partial pass. Two targeted panes opened. A separate process addressed only this Kitty instance and corrected the rail to 32 columns. A controlled awake-desktop retry brought Niri window 18 into focus, then targeted pane focus selected the viewer. | Resize/font-change width behavior and sustained visual focus judgment remain untested. |
| 1. Local handoff and A -> B -> A | Stopped. Marked Starship fixture A was created and identity-checked, but never attached. Replacing the test-owned idle viewer pane with `launch --keep-focus` brought the test OS window into focus while the operator was actively working elsewhere. | Startup with the supervisor already in the session file is an untested alternative. No A -> B -> A or stale-reference result was obtained. |
| 2. Native TUI | Not run after the Gate 1 stop. | No Kitty native-agent streaming, input, paste, scroll, or flicker verdict. |
| 3. Remote Snap | Not run after the Gate 1 stop. | No Kitty-specific remote attach, cut, reconnect, or status verdict. |
| 4. Cleanup | The transient Kitty unit was stopped and exact marked fixture A was killed after identity and zero-client checks. Both private directories, the socket, test PIDs, and marked session were verified absent. | Snap options matched the baseline; Starship's option comparison is inconclusive as described below. |

## Kitty control observations

The initial rail/viewer measurements were 53/53 columns at 71 rows. Targeted
`resize-window` calls on the rail pane led to 29/76, then 33/73, then 32/74.
The requested cell increment did not map one-for-one to the measured change,
so any future fixed-width controller must read the actual geometry and correct
it. The pane identities remained stable through these corrections.

`focus-window --match id:2` changed Kitty's active pane, but that alone did
not prove OS-window focus. The first OS-focus attempts coincided with an
exclusive DMS fade-to-DPMS layer and later with the operator changing active
windows. One attempted `focus_os_window` action was not a valid mappable
action; it opened a test-owned error pane, which was removed by exact ID.

Once Starship was awake and focus was held still, the documented Kitty
[`nth_os_window 0` action](https://sw.kovidgoyal.net/kitty/actions/#nth_os_window)
addressed to the private instance returned success.
Niri immediately reported the test OS window ID 18/PID 391052 focused.
`focus-window --match id:2` then selected the viewer pane while Niri kept
window 18 focused. This is evidence that the supported controls can target
both levels under those conditions.

The first attempt then closed only the test-owned `sleep infinity` viewer pane and
used [Kitty `launch --keep-focus`](https://sw.kovidgoyal.net/kitty/launch/)
to start a small persistent supervisor in a new test pane. The command returned
new pane ID 4, but Niri focus changed from a non-test Kitty window to test
window ID 18. `--keep-focus` therefore did not preserve **OS-window** focus in
this run. This may reflect the option's internal-pane scope; the observation
does not prove that a two-pane Kitty startup session would steal focus during
later session switching. The supervisor remained idle and attached no tmux
client. To respect the operator's active Starship work, the study stopped
without further GUI control or native-TUI actions.

Fixture A was ordinary Starship tmux session `$9`, name `apsp-vOlSNj-A`,
created `1790650706`, with exact marker `APSP-vOlSNj-A` and pane `%10` running
test process PID 419815. It had zero attached clients. Those identities were
rechecked before removing it. No Snap fixture was created. Starship gained an
unrelated `remote-chrome-snap` session during the spike; it was preserved.

## Cleanup audit

The test Kitty unit was inactive with `MainPID=0`; Kitty PID 391052, rail PID
391065, supervisor PID 439056, and fixture PID 419815 were absent. The private
socket and directories on both hosts were removed and verified absent.
Starship's original sessions remained; its new, unrelated `$8`
`remote-chrome-snap` session remained intact. Snap's seven baseline sessions
were unchanged. Snap global tmux options matched the saved snapshot exactly
(SHA-256 `b808f5273b1ab783370e977d3ab02c8fd8121d903af3932ea6d58366792f316b`).
Starship's final global-option comparison was inconclusive because the baseline
and live queries rendered long `status-format` lines differently. The
window-option comparison was also inconclusive because its queries returned
empty output. The spike issued no global or window option changes.

## Decision boundary after the first attempt

Gate 0 alone supplied no production-client or flicker verdict. The follow-up
put the rail and supervisor in Kitty's initial session file, so switching did
not launch another Kitty pane. At this point, local A -> B -> A,
native-agent visual acceptance, remote recovery, and fixed-width behavior
after resizing still needed evidence. The second replay below addressed those
checks. The earlier Snap -> Starship marker-validation stall and real
sleep/wake recovery remain separate questions.

## Offline preparation for the second attempt

A private startup session named both the rail and persistent viewer
supervisor before Kitty launched. The temporary controller addressed a matched
tmux server socket and checked its PID/start generation plus the fixture's
session ID/name/creation time, marker, pane, and process start before attaching.
It kept the viewer's TTY and replaced only its own attach child. These files
were staged in a mode-700 `/tmp/agent-plus-kitty-replay-yyrJV6pZ` directory on
Starship; the five file hashes matched the local preparation copy. Before the
second replay, Starship had no second-attempt Kitty window, viewer socket,
manifest, or fixture.

On Snap, an isolated tmux server under a private test socket created marked A
and B fixtures. The controller's offline selection sequence returned
A -> B -> A while both original fixture PIDs/start generations remained
unchanged. A stale record pointing at B's session ID while claiming A's
identity was refused. The remote-check code compiled and validated those
private fixtures locally. That test intercepted child launch to isolate the
controller's identity decisions.

A second private-server smoke test gave the supervisor a real PTY and direct
tmux attach children. Its client followed A ($1) -> B ($2) -> A ($1); cutting
the viewer left no attached client and kept both fixture processes alive. It
removed its fixtures, server, socket, and temporary directory after checking
that an unrelated sentinel in the private server was the only session left
before server shutdown. This establishes the TTY handoff in a synthetic PTY,
not yet in Kitty's actual split. A read-only Starship -> Snap SSH preflight
responded.

An additional headless Starship PTY -> SSH -> Snap test used an exact marked
fixture in a **private Snap tmux server**. Its first attach child exited
because the noninteractive Starship shell had no `TERM`. With
`TERM=xterm-kitty` supplied to the test PTY, the Snap client attached to the
same fixture on the first selection, after a controlled SSH-child cut, and
again after an injected port-1 SSH route failure. The failure left zero Snap
clients; retry attached the same session/process. The private Snap server,
fixture, viewer process, sockets, and test directories were removed. This is
remote transport evidence, but it did not run in a Kitty GUI split or touch
either host's ordinary tmux sessions. Gate 3's actual presentation and
rail-status check was still open at that point.

This preparation improved the second test's targeting and avoided runtime
Kitty pane replacement. By itself it did not change any live result above or
supply a native-TUI flicker verdict.

## Unattended Snap Kitty replay

With Snap's desktop free, a separate test of the same startup session opened
one Kitty 0.49.1 OS window (Niri ID 61, Kitty PID 1517121) with rail/viewer
pane IDs 1/2 and no pane replacement. At 960 x 1432 pixels the panes started
53/53 columns and were corrected to 32/74 through targeted
`resize-window` calls. Niri width 1200 changed them to 40/93; a correction
restored 32/100. Returning to width 960 gave 25/80; another correction
restored 32/74. A one-point font increase changed the split to 29/66 at 65
rows, and a correction restored 32/63; resetting the font required another
measured correction to 32/73 at 71 rows. Pane IDs stayed fixed. Kitty's
`focus-window` selected either pane, and `nth_os_window 0` raised exactly
the test OS window after another Snap window was focused.

Five marked synthetic peers A-E ran in a **private Snap tmux server**. An
external request selected A -> B -> A; then targeted rail Down/Enter and
Up/Enter selected B -> A. The client session, visible card, and viewer text
agreed at each step. Old viewer client PIDs exited; the five fixture
processes kept their original PIDs/start generations. A key sent to the viewer
incremented A's synthetic input count. Cutting the viewer detached its tmux
client, and selecting A again reached the same fixture. A same-name E
replacement in the private server made the saved E reference stale; selecting
it returned `session identity mismatch` and attached no client. The original
E identity was restored before cleanup.

Niri closed the exact test window while A was viewed. Kitty asked for
confirmation; after confirmation, its unit and panes exited, the tmux client
detached, and A's fixture process remained alive. The supervisor's Unix socket
path remained stale after this close, with no listener or supervisor process;
the exact stale socket was removed during cleanup. All five marked fixtures
and the private server were removed after identity and zero-client checks.
Snap's ordinary tmux server PID, session identities, global options, and
global window options still matched the read-only preflight snapshot. No
managed product configuration changed.

This Snap replay supported Kitty's control and one-tmux TTY mechanics, including
width recovery and session switching. It preceded the Starship live replay
below and supplied no native-TUI flicker verdict itself.

## Second Starship live replay

The operator made Starship available for a dedicated test window. The transient
unit `agent-plus-kitty-starship-spike-yyrJV6pZ.service` started Kitty 0.49.1
as PID 573653 with only a private Unix control socket. Niri identified its OS
window as ID 28. Kitty's startup `splits` tab had rail pane ID 1 (PID 573665)
and persistent viewer pane ID 2 (PID 573671); switching never replaced either
Kitty pane. Starship and Snap both ran tmux 3.7c. The test used ordinary tmux
servers, with five marked Starship synthetic sessions A-E, one marked Snap
synthetic session R, and one isolated Starship Codex session N. No existing
session was attached or modified.

| Gate | Second replay result | Limit |
| --- | --- | --- |
| 0. Startup, geometry, focus | Pass for targeted controls. `kitten @ ls`, `focus-window --match id:1/2`, `resize-window --match id:1`, and `action --match id:2 nth_os_window 0` addressed only the private instance. Niri reported test window 28 focused. The rail was measured and corrected to 32 columns after startup, Niri width changes, and a font change. | Width must be remeasured and corrected after geometry or font changes; the split does not hold 32 columns automatically. |
| 1. External handoff and local switch | A separate process selected A -> B -> A, then rail Down/Enter and Up/Enter selected B -> A. The rail label, attached tmux ID, and viewer agreed. Original fixture PIDs survived, and pane IDs 1/2 stayed fixed. N -> A -> N returned to the same Codex PID/start generation. A same-name stale E reference was refused. | The temporary supervisor stops the current viewer **before** validating a different target. The refused E selection therefore detached N briefly. A real client must validate first and preserve the current view on refusal. |
| 2. Native TUI | Pass for the key wide-screen visual question. An isolated Codex 0.158.0 TUI ran in ordinary Starship tmux session `$16`, pane `%17`, PID 581303. At a 1280 x 1432 Niri window, Kitty gave the rail/viewer 32/109 columns and 71 rows (tmux viewer 109 x 70). The operator tried a harmless multiline response, Shift+Enter, paste, mouse/scroll, and focus; they reported that all checked out. They then watched N -> A -> N and reported comfortable switching with correct focus and no visible flicker. `tmux copy-mode` entered/exited mechanically, and PID/start ticks `581303/4057793` survived switching. | The native pane was not resized while its response streamed; this visual verdict applies to the tested wide layout. The physical tmux prefix gesture was not separately reported by the operator. |
| 3. Remote Snap | Pass in the tested direction. The Starship Kitty viewer attached exact Snap fixture R in ordinary tmux session `$14`, pane `%14`, PID 1773342. Cutting only the SSH viewer child left R alive with zero clients; retry reattached the same fixture. An injected port-1 route failure reported `unreachable`, kept local A attached and receiving input, and a later retry reached R. The rail showed the active host and state. | This does not test laptop sleep/wake or the earlier Snap -> Starship marker-validation stall. |
| 4. Close and audit | The exact Niri window was closed while N was viewed. Kitty's confirmation was answered in its test-owned pane 3. Its unit became inactive, N had zero clients, and the Codex process remained alive until exact fixture cleanup. All marked sessions and the scratch Codex home/workspace were removed. Both ordinary servers, original session identities, global options, and global window options matched their saved baselines exactly. | The stale supervisor socket remained after Kitty exited and required ownership-checked removal. |

The initial 960 x 1432 window opened at 53/53 columns and was corrected to
32/74. Changing Niri width to 1200 changed the split to 40/93; a targeted
correction restored 32/100. A one-point font increase made it 29/90 at 65
rows; correction restored 32/87. Resetting the font and window width required
another measured correction to 32/74 at 71 rows. At the final 1280-pixel
width, the split changed to 42/99 and was corrected to 32/109. These are
observed cell counts, not requested increment values.

Starship -> Snap initial attach returned in roughly 0.6 seconds in this run;
the controlled retry did too. There was no marker-validation stall in this
direction. The synthetic A input counter increased while the injected Snap
failure was visible. The operator's N -> A -> N judgment followed a three
second pause on A. No prompt or response text was retained in the results.

The stale E test renamed only its marked original `$15`, created a new marked
session with the same name, and requested the saved E reference. Identity
validation returned `session identity mismatch`. The replacement was removed
after exact identity and zero-client checks, and `$15` was restored and
validated. The supervisor's pre-validation detach left N unattached; selecting
N again reached the same PID/start generation. This is a test-harness
ordering fault and a concrete requirement for any later client design, not a
Kitty or tmux renderer failure.

The close left `viewer.sock` as a filesystem socket after the supervisor and
listener exited, as on Snap. Exact process/listener and ownership checks
preceded removal of the private directories. The final baseline comparisons
were exact on both hosts, including nonfixture session IDs and options. The
disposable Codex authentication copy and session history were deleted with
the test home. No managed product behavior or configuration changed.

## Study conclusion

The wide Kitty one-tmux presentation path is feasible for this daily workflow:
the operator accepted native streaming and switching without visible flicker,
the rail remained visible, and exact remote reconnect worked on Starship ->
Snap. A later design must validate a candidate before detaching the current
viewer and must implement 32-column correction after resize/font changes.
The reverse SSH direction and real sleep/wake recovery remain separate tests.
This spike does not authorize a production client implementation.
