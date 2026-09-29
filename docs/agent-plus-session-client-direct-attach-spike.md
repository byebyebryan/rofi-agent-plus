# Agent Plus session client: direct-attachment spike plan

Date: 2026-09-25

Status: study completed; see the [decision record](agent-plus-session-client-direct-attach-results.md).
This plan remained separate from client implementation. The
[first presentation spike](agent-plus-session-client-spike-results.md)
showed working synthetic switching and reconnect through a nested tmux pane,
but measured 2.254× cursor-motion traffic against direct tmux on 3.7c. Its
current-Codex native-TUI check remained inconclusive. This plan tests the
specific alternative named by that result.

## Decision to make

Can one terminal window show a **full-width, directly attached native tmux
client**, then return to a flat session picker on detach, with switching and
reconnection comfortable enough for daily use? The picker would be visible
between attachments, not beside the agent TUI. The study must establish
whether that interaction is a useful replacement for repeatedly opening
separate terminal windows, especially on a small screen.

The topology under test is:

```text
local:   Ghostty -> foreground tmux client -> existing tmux server -> agent
remote:  Ghostty -> foreground SSH -> remote tmux client/server -> agent

         detach or connection loss
                   ↓
         same terminal shows flat picker again
```

The experiment must begin outside an existing tmux client; otherwise it would
measure another nested renderer. A temporary launcher may run `tmux attach`
or `ssh -tt ... tmux attach` as a foreground child and regain the terminal when
that child returns. It must not proxy or redraw provider output, install a
global tmux binding, or treat the child's exit status as proof that an agent
conversation stopped. Native tmux detach is the initial switch gesture; the
study measures its actual cost instead of assuming it feels quick.

## Boundaries and setup

- Use disposable local and remote **private** tmux sockets, synthetic agent
  stand-ins, and static cards with stable fixture IDs. Include five distinct
  conversations in one directory and a mixed local/Starship list. Do not read
  Agent Plus's private cache or attach to ordinary user sessions.
- Keep the picker a throwaway terminal mock. It may remember its selected card
  during the test, but it must not own agent processes, saved conversations,
  working-set persistence, or a production SSH route.
- Use the existing known-host SSH route for a remote private server. Do not
  change SSH trust, host networking, ordinary tmux configuration, provider
  hooks, or installed pickers. An unavailable host remains **unreachable or
  unknown**, never automatically **stopped**.
- Record versions, sanitized process identities, sizes, elapsed switch and
  reconnect times, terminal-stream counts, and cleanup. Keep raw terminal
  output and any provider auth copy only in mode-restricted temporary storage
  during the test; remove them afterward.

## Ordered gates

Gate 0's mock should run the actual foreground attach/detach loop against
synthetic private sessions. Its card data can be fixed; its switching gesture
cannot be simulated on paper.

| Gate | Exercise | Pass or stop observation |
| --- | --- | --- |
| 0. Interaction cost | In a disposable mock, switch A → B → A, including five peers from one directory, at 80×24 and a wide terminal size. Compare the detach → picker → attach sequence with the prior two-pane rail and today's separate windows. | Record keystrokes, time to usable TUI, preserved card selection, identity clarity, interruption, and operator visual judgment. If returning to the picker feels too cumbersome for frequent switches, stop this topology before deeper transport work. |
| 1. Local direct path | Attach to two private synthetic sessions, detach with native tmux keys, and reattach repeatedly. Check literal prefix input, other modified keys, focus, copy/scroll, mouse, alternate screen, and resize. | Same process and terminal state survive A → B → A. The foreground launcher regains control on detach; no extra tmux renderer or default-server change appears. Record which interactions were automated and which were visually checked. |
| 2. Fidelity comparison | Replay the same bounded high-churn synthetic workload through direct and nested paths at equal geometry. Count cursor motion, visibility, CSI, and bytes; visually inspect both in Ghostty. | Compare numeric counts with the first spike's direct baseline and its 2.254× nested motion result. Evaluate the numbers independently: the current WSNav harness has defective Boolean threshold flags. Stream counts alone cannot establish visual acceptability. |
| 3. Remote loss and recovery | Attach to a private Starship session through one test-owned SSH process. Cut only that test transport, return to the picker, then reattach the exact private target. Separately inject a route failure before attach. | The remote pane process continues, input works after reattach, and a route failure never starts or resumes an agent. Record detection time and any stuck-client behavior. This controlled cut does not replace a later laptop sleep/wake check. |
| 4. Two viewers | Attach one client local to the private runtime host and another over SSH at different sizes. Switch tmux windows from one viewer while the other stays attached; test resize and input in both directions. | Record actual window-size policy, whether active-window selection follows the other viewer, and any unwanted focus/input interaction. Neither viewer is force-detached. |
| 5. One native TUI | Only if the interaction and synthetic gates hold, establish a current-version isolated Codex conversation **directly** first, then repeat one harmless interaction through the picker/attach loop. | Verify typing, streaming, copy/scroll, mouse, resize, detach, and reconnect in the actual terminal. Codex 0.157.0 opened an Agent command center in the first spike's old harness; resolve that entry path and isolation before attributing a failure to presentation. Do not claim native acceptance without visual feedback. |

The remote gate may use an exact test-owned SSH process or temporary local
proxy to create a transport cut. It must not disconnect the host's network or
touch unrelated SSH sessions. A test process exiting, a route failing, and a
provider process stopping are three distinct outcomes to record.

## Decision record and exit rule

The output is a short evidence record, not a client scaffold. It should show
the measured A → B → A experience beside the previous nested result, the
direct-versus-nested stream counts, local and remote process continuity,
shared-viewer effects, native visual feedback, failures, and what remains
untested. Verify private process/socket cleanup and an unchanged ordinary tmux
fingerprint before closing the study.

Recommend a direct-attachment client design only if the switching gesture is
comfortable, the native TUI behaves as expected, remote reconnection preserves
the runtime, and shared viewing introduces no unacceptable surprises. If the
interaction fails, retain the result and return to other presentation options
such as the nested rail or Herdr. If a technical gate is inconclusive, name
that gap rather than promoting the topology to implementation. A successful
spike authorizes a **separate** contract/design pass for exact conversation
identity, validated attach routing, discovery, and the working set; it does
not itself change the Rofi Plus P9 contract or daily workflow.
