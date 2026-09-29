# Agent Plus session client: Kitty one-tmux spike plan

Date: 2026-09-28

Status: the bounded Starship live replay is complete. The first attempt stopped
at a pane-replacement focus boundary; the startup supervisor replay then tested
the Starship Kitty split, native Codex TUI, and Snap reconnect. See the
[results](agent-plus-session-client-kitty-results.md) for the accepted visual
behavior and the stale-selection ordering gap.
This is a disposable presentation study, not a client implementation, Agent
Plus contract change, or deployment.

## Decision to make

Can a dedicated Kitty window on **Starship** keep an always-visible, 32-column
session rail beside a direct attachment to an ordinary tmux session, using
supported, targetable Kitty controls for startup, pane focus, width, and an
external Agent Plus-style handoff? The native agent TUI must remain comfortable
and free of the visible streaming flicker reported for WSNav's two-tmux path.

The [Ghostty split result](agent-plus-session-client-ghostty-split-results.md)
already accepted the fixed 32-column rail and found no flicker in a wide
one-tmux replay. Its unresolved presentation issue was supported external
control; remote validation also stalled intermittently. Do not repeat 80x24 as
a minimum-size gate: the operator would not use a native TUI in a whole window
that small. Test at a realistic window size and record the actual geometry.

The viewing endpoint for this spike is Starship, where the operator is working.
Read-only preflight on 2026-09-28 found Kitty 0.49.1 and tmux 3.7c there, and
the configured Starship -> Snap SSH route responded. Snap is the remote fixture
host in this direction. A pass in that direction does not resolve the earlier
Snap -> Starship marker-validation stall.
The shell used to draft this plan ran on Snap; launch and observe the spike's
GUI in Starship's active Niri session, not on the drafting host.

```text
dedicated Kitty instance on Starship (private control socket)
  left Kitty split:  temporary flat rail and selection
  right Kitty split: persistent viewer supervisor
                       -> direct ordinary Starship tmux attach
                       -> SSH -> ordinary Snap tmux attach
```

Kitty draws the two panes; only the session's ordinary tmux renders the agent
pane. Switching replaces the test-owned viewer child in the **same** right
split. It does not create a Kitty pane, tmux server, tmux session, or provider
process per selection. The rail owns view selection; tmux owns the runtime and
the provider owns conversation history.

## Why Kitty is worth this spike

Kitty documents [startup sessions](https://sw.kovidgoyal.net/kitty/sessions/)
with the `splits` layout and `launch --location=vsplit`. Its
[remote-control interface](https://sw.kovidgoyal.net/kitty/remote-control/)
provides `ls`, `launch`, `focus-window`, and `resize-window` against matched
window IDs through a per-instance Unix socket. The
[configuration reference](https://sw.kovidgoyal.net/kitty/conf/#allow_remote_control)
documents `socket-only`, so the test need not enable remote control in the
normal managed Kitty configuration. `resize-window` accepts cell increments;
`launch --bias` uses a percentage in `splits`, so a 32-column width must be
measured and corrected rather than inferred from the initial split ratio.

These are documented mechanisms, not a result on Starship's Niri desktop.
In particular, `focus-window` success inside Kitty does not by itself prove
that a separate picker can raise the OS window and deliver keyboard focus under
the compositor. The spike must observe that behavior directly.

## Boundaries and safety

- Use one identifiable disposable Kitty instance and a private Starship
  temporary directory for its session file, Unix control socket, rail,
  supervisor, fixtures, and evidence. Do not use `--single-instance`, a
  global `listen_on`, or a managed Kitty/Niri/Agent Plus configuration change.
  Record the Kitty process, OS-window ID, split IDs, and socket path before
  sending targeted commands. Do not address ordinary Kitty windows.
- Use a private socket with `allow_remote_control=socket-only` for this test
  instance. Do not pass that socket into SSH or provider children. Check that
  commands addressed to the test instance cannot hit another Kitty window.
  If the instance or target ID becomes ambiguous, stop rather than guess.
- Record ordinary Starship and Snap tmux server generations, exact existing
  session identities, relevant options, and fixture markers before mutation.
  Create only marked disposable synthetic sessions, five local peers in one
  directory and one remote Snap session. Revalidate session ID, creation time,
  marker, pane, and process identity before each attach and cleanup. Preserve
  any object whose ownership becomes uncertain.
- Do not change ordinary tmux global options, key tables, hooks, or SSH trust.
  End only the exact test-owned viewer/SSH child during switch and cut tests.
  Do not attach to or interrupt existing provider sessions. For a native TUI
  trial, create one isolated disposable conversation and retain no prompt or
  response text in the evidence.
- Do not change Agent Plus discovery, its cache, CLI contracts, or Rofi mode.
  Simulate the later external picker handoff with a separate process sending
  an exact fixture ID to the temporary supervisor. No persistent working set
  or general client framework is part of this spike.

## Ordered gates

Stop at a failed control or native-display boundary and record the reason.
Do not expand the harness to solve a separate product problem.

| Gate | Exercise | Required observation |
| --- | --- | --- |
| 0. Targeted startup and geometry | Start the dedicated Kitty instance with a rail and idle viewer in one `splits` tab. From a separate Starship process, enumerate only this instance, target each split by ID, focus rail/viewer, and correct the rail to 32 columns with `resize-window`. Resize the Niri window and change font size, then restore the rail. Try external focus from a separate window. | Record the exact supported API calls, two split IDs, measured columns before/after each resize, which pane and OS window actually receive input, and any compositor focus limit. A command returning success without visible focus/width is insufficient. If stable correction needs a resize event, assess Kitty's documented [window watcher](https://sw.kovidgoyal.net/kitty/launch/#watching-launched-windows) with a disposable callback; do not add a permanent watcher. |
| 1. External handoff and local switch | Use a separate process to request marked local A, then B, then A. Switch again using the rail while the native viewer has focus. Inject a stale same-name fixture reference and close/reopen only the viewer. | The rail remains visible. Selection, focus, attached tmux ID, and input destination agree. A and B keep their original processes. Only the old viewer child exits. The stale reference is refused. Record the operator's actual switch gesture and time to usable TUI. |
| 2. Native fidelity | At a realistic wide size, attach one isolated native Codex TUI and watch a harmless multi-line response stream; switch Codex -> synthetic A -> the same Codex process. Exercise typing, Shift+Enter, paste, tmux prefix/copy mode, mouse/scroll, and Niri window resize. | Operator reports on flicker, responsiveness, readable viewer width, and focus. Verify process continuity and one tmux renderer. A screenshot or synthetic byte count alone cannot clear the prior flicker concern. Do not capture private content. |
| 3. Remote Snap and recovery | Switch local -> marked Snap fixture -> local; stop only the identified SSH viewer child, retry the same remote target, and inject one route failure before attach. Keep local selection usable while Snap is unreachable. | The Snap fixture survives with the same process and input resumes on that exact target. Distinguish `viewing`, `detached`, and `unreachable` without leaving a stale rail label. Record timing and any marker-validation stall. This does not test laptop sleep/wake or prove the earlier reverse-direction stall fixed. |
| 4. Close and audit | Close the disposable Kitty window while viewing a fixture. Revalidate and remove only owned children, fixtures, socket, session file, and temporary evidence. Compare non-fixture session identities and global options with gate 0 on both hosts. | No test process, window, socket, or marked fixture remains. Existing sessions/options are intact; report unrelated user activity separately instead of treating a whole-server hash change as test damage. |

## Exit record

Publish a short result table for each gate with exact Kitty/tmux versions,
instance and pane identities, API calls, geometry, switch gesture, process
continuity, operator visual feedback, remote failure/retry behavior, and the
cleanup audit. Distinguish API availability, automated observation, and
operator acceptance.

A passing Kitty spike would justify a later design pass for Agent Plus handoff,
conversation identity, work-set persistence, and lifecycle boundaries. It would
not approve a production client. A control or compositor-focus failure should
be recorded as a concrete integration gap; do not hide it with an unplanned
Niri-specific workaround. The earlier Snap -> Starship stall and real
sleep/wake recovery remain separate questions.
