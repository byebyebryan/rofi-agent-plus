# Agent Plus session client: one-tmux Ghostty split spike

Date: 2026-09-27

Status: executed on 2026-09-27/28; see the
[results](agent-plus-session-client-ghostty-split-results.md). The operator
accepted the fixed 32-column rail and found no flicker in the wide live
replay. The 80x24 stress window was smaller than they would use for a normal
native TUI; it did not invalidate the rail layout. Remote validation and
targeted Ghostty control remained unresolved.
This was a disposable presentation study, not a client implementation, Agent
Plus integration, or deployment. The earlier
[Ghostty split research](agent-plus-session-client-ghostty-split-research.md)
established split creation in two inert windows before this attach/switch run.

## Decision to make

Can one Ghostty window keep a flat, approximately 32-column session rail
visible beside a **direct** ordinary tmux attachment, while making A -> B -> A
comfortable and avoiding the visible native-TUI flicker the operator experiences
with WSNav's two-tmux presentation? Test local and Starship sessions at wide and
80x24 sizes. Operator feedback on the switch gesture and flicker is a required
result, separate from process and terminal-stream checks.

```text
dedicated Ghostty window
  left split:  temporary flat rail (five peers, selection and viewer status)
  right split: persistent viewer supervisor
                 -> local tmux attach -> ordinary local tmux server
                 -> SSH -> tmux attach -> ordinary Starship tmux server
```

There is no presentation tmux server. The rail and supervisor own only the
viewer and selection; ordinary tmux owns its sessions and the provider owns its
conversation. Switching detaches or ends only the test-owned viewer client.
Viewer exit cannot mean that the underlying session stopped.

## Boundaries and fixture safety

- Keep the rail, supervisor, socket, and evidence under a private temporary
  directory. Display fixed test cards; do not read Agent Plus's private cache,
  add provider hooks, change Rofi Plus, persist a working set, or scaffold a
  general client. Borrow the earlier rail's visual geometry, not its private
  presentation tmux or runtime lifecycle.
- Record local and Starship default-server generations, non-fixture session
  fingerprints, relevant options, and Ghostty/tmux versions before creating
  fixtures. Use five uniquely named, marked synthetic local sessions in one
  directory and one marked remote session. Attach only to these fixtures.
  Revalidate server generation, session ID/name/creation tuple, marker, pane,
  and process identity before each attach and cleanup. A stale or reused name
  must refuse attachment. Preserve any fixture whose ownership becomes
  uncertain.
- Use one dedicated Ghostty instance and exact process/window identity. Its
  first command runs the rail; its second-surface command runs the viewer
  supervisor. For the experience test, create the split with Ghostty's normal
  split action. The observed GTK D-Bus `split-right` action may be tried only
  against this identified test window to assess startup automation; it is an
  undocumented, version-specific interface, not an accepted product contract.
- The rail sends fixture IDs, never shell commands, over a private local socket.
  The supervisor serializes requests, revalidates the target at action time,
  ends only its current `tmux attach` or SSH child, waits for that child to
  exit, then starts the new viewer in the same right-hand terminal surface.
  Record deliberate detach, route failure, and unexpected viewer exit
  separately. A switch must not create a second agent/provider process.
- Use the existing SSH route and trust. Simulate transport loss by stopping
  only the identified test-owned SSH child; do not interrupt the host network
  or unrelated clients. No default-server keys, hooks, global options, or
  window-size policy may be changed.

## Ordered gates

Stop at a failed interaction or rendering boundary and report it. Do not widen
the disposable harness to solve an unrelated product problem.

| Gate | Exercise | Required evidence and stop condition |
| --- | --- | --- |
| 0. Split, width, and focus | Launch rail and idle viewer in one disposable Ghostty window. Measure each pane's terminal columns at 80x24 and wide size. Set the rail near 32 columns using Ghostty's supported resize action; repeat after window resize and font zoom. From a focused viewer, use Ghostty's split-focus binding to reach the rail, then return to the viewer. | Record the exact gesture, pane widths, and whether the size can be restored predictably. If the 80x24 viewer is unusable or focus cannot return reliably, stop before attaching a provider. Separately record whether automatic split creation and focus have a dependable external control path; manual bindings alone do not prove automation. |
| 1. Local A -> B -> A | Attach marked synthetic A, switch to B and back using the rail while the viewer initially has focus. Repeat with the rail initially focused. Send input to each target and observe hidden process heartbeats. Try a saved stale reference after replacing one marked fixture with a same-name fixture. Close and reopen the test viewer without closing the rail. | The rail stays visible; selected card, keyboard focus, and actual attached target agree. A/B retain their original processes and state. Only the old viewer client exits. The stale reference is refused. Record keystrokes and time to usable TUI rather than assuming the gesture is fast. |
| 2. Display fidelity and native TUI | At equal **viewer** geometry, run a bounded synthetic redraw workload via full-window direct attach and the split viewer, first with a quiet rail and then with rail updates. Compare terminal traffic and inspect both in Ghostty. If that looks sound, use one isolated disposable native agent conversation and repeat typing, a streaming response, A -> B -> A, tmux prefix/copy mode, paste, mouse/scroll, and resize at wide and 80x24 sizes. | Confirm one tmux renderer by process topology. Record any cursor flicker, redraw artifacts, or input loss separately from byte counts. Obtain the operator's visual judgment; an image or synthetic count cannot clear the known WSNav flicker concern. Do not record private prompts, credentials, or conversation output as evidence. Stop if the live TUI remains annoyingly flickery. |
| 3. Remote recovery | Switch local -> marked Starship fixture -> local. Reattach Starship after stopping only the test SSH viewer; also inject a route failure before attach. Test local selection while remote is unreachable. | The rail remains usable and distinguishes `viewing`, `detached`, and `unreachable`. The remote fixture process survives, and reconnect restores input to the exact target without starting a duplicate. A controlled SSH cut is not a laptop sleep/wake acceptance test. |
| 4. Startup and cleanup audit | Repeat a fresh launch and split creation in the identified test window. Close the window while a fixture is viewed. Revalidate and remove only exact test-owned fixtures, socket, children, and temporary files. Compare local/remote non-fixture sessions and options with gate 0. | Record whether startup requires the internal GTK action, manual width adjustment, or manual focus. Original sessions/options are unchanged; no test viewer or fixture remains. Preserve anything with uncertain identity and report it. |

## Exit rule

The result is a short gate table with exact topology, before/after fingerprints,
pane geometry, switch keystrokes and timing, local/remote process continuity,
direct-versus-split visual and traffic observations, operator feedback, startup
control limits, and cleanup. Do not promote synthetic output or a still image
to native-TUI acceptance.

Recommend a later design pass for this topology only if the rail stays useful
at 80x24, A -> B -> A feels comfortable from the agent pane, native streaming
does not show the reported flicker, exact target/remote recovery holds, and a
defensible Ghostty startup/focus/width control route can be identified. A
passing presentation spike still leaves session discovery, conversation
identity inside multi-window tmux sessions, working-set persistence, and
Agent Plus integration for a separate contract/design pass.

Retrospective: the operator clarified after the replay that an 80x24 whole
window is not a normal-use size for a native TUI. The original minimum-size
condition remains recorded above as the study's test rule, but its result
does not reject the fixed 32-column rail, which the operator accepted. Further
design assumes a reasonable TUI-sized window rather than a minimum-size gate.
