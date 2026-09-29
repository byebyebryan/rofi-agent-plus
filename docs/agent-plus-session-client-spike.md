# Agent Plus session client feasibility spike

Date: 2026-09-25

Status: study completed; see the [decision record](agent-plus-session-client-spike-results.md).
This remains before any client implementation plan. The
[exploration note](agent-plus-session-client-exploration.md) records the
workflow and source review. The [Rofi Plus status ledger](https://github.com/byebyebryan/dotfiles/blob/main/docs/rofi-plus-status.md)
remains the authority for the deployed pickers. This spike publishes no new
CLI contract and changes no installed workflow.

## Decision this study should inform

Can one terminal window present several **existing** local and remote tmux
sessions with quick switching, reliable reconnection, and an acceptable native
TUI experience? The leading candidate is a small navigator beside one visible
tmux attachment, borrowing WSNav's private presentation pattern. We want to
learn whether that presentation model is worth pursuing before planning a
client or changing Agent Plus and Tmux Plus.

This study does not decide how to discover all native conversations, resume a
stopped one, start a new one, persist a working set, or integrate the Rofi
picker. Those questions matter only if the terminal presentation proves useful.

## Evidence already available

- WSNav has tested a private two-pane presentation, pane focus, switching,
  resize, detach/reattach, and owned-runtime recovery. Its current attachment
  code depends on WSNav Workstream and private Runtime identities; it is not a
  drop-in attachment to Agent Plus's ordinary tmux sessions.
- WSNav's [remote transport spike](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/spikes/0001-tmux-remote-transport.md)
  showed that local tmux, SSH, and remote tmux can preserve one synthetic
  remote process across detach/reconnect. Its [Codex follow-up](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/spikes/0002-codex-native-tui.md)
  passed on an isolated older Codex version. Neither exercised this proposed
  mixed-host client or ordinary default tmux sessions.
- WSNav's [terminal-fidelity A/B study](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/spikes/0014-terminal-fidelity-a-b.md)
  measured extra cursor traffic from nested tmux on 3.7b. The version-specific
  result is a risk to recheck, not a current acceptance result.

## Bounded experiment

Steps 1-4 use disposable tmux sockets, synthetic processes, and a temporary
local presentation. Do not modify default tmux servers, launch agents, install
hooks, capture provider content, or change the suite repositories. Record
tmux/terminal versions and sanitized process and topology evidence. Any
scripts or mock UI are throwaway study equipment, not a client skeleton.

| Step | Question | Observation that matters |
| --- | --- | --- |
| 1. Local surface | Can a narrow flat list and one attachment pane fit and remain easy to switch, including five peer rows from one directory? | Switch between two synthetic runtime sessions; compare with a direct tmux client at wide and small terminal sizes. Record selection/focus behavior and whether the layout still feels useful. |
| 2. Terminal path | Does the extra tmux renderer preserve practical input and display behavior? | Test literal prefix keys, modified keys, mouse, copy/scroll, alternate screen, resize, and a bounded high-churn synthetic stream. Record visible artifacts and WSNav-style cursor/byte ratios against direct tmux. |
| 3. Disconnection | Can the viewer reconnect without changing the remote process? | Repeat with a disposable remote tmux server over SSH. Interrupt the connection, then reattach; verify the same remote pane process and usable input. Test an unreachable host separately so it is never mistaken for a stopped session. |
| 4. Shared viewing | What happens when another endpoint is still attached? | Attach two disposable viewers to one tmux session. Observe size, active window/pane, and input/focus interactions. Do not force-detach either viewer. |
| 5. Native TUI check | Does the promising synthetic path still feel native with a real provider? | Only after the earlier steps pass, run one controlled native-TUI trial with disposable provider state. Observe typing, streaming, clipboard, mouse, resize, and reconnect without changing ordinary provider state. Keep this result separate from the synthetic transport result; provider breadth belongs to a later study. |

A small UX comparison can run alongside the technical spike: show five cards
from one directory, A → B → A, and a mixed Snap/Starship list in a static mock
or disposable prototype. If Herdr is evaluated hands-on, use those same tasks
and record its actual Agents-sidebar behavior. This comparison tests whether
the flat conversation view is useful; it is not a reason to build lifecycle
machinery during the spike.

## Pass, failure, and limits

The synthetic path passes its functional checks only if the same runtime
process survives switching and reconnect, input reaches the selected target,
resize behaves predictably, and ordinary tmux state is unchanged. Record
unacceptable visual or interaction behavior even if those checks pass. A
failed nested path leads to a concrete alternative-topology study; the
unspecified idea of a "single-tmux client" is not an A/B implementation target.

The experiment cannot prove exact provider-conversation identity, safe
action-time SSH routing, or duplicate-free native resume. Tmux Plus's
inventory route is observation metadata and a tmux session ID is valid only
within its server generation. A future client would need a validated attach
operation; this study must not turn an inventory result into a production
`ssh tmux attach` recipe.

The study ends with a short decision record: observed results and versions,
what was and was not tested, native visual feedback, the Herdr comparison if
run, and one recommendation: continue with the nested presentation, study a
specific alternative, or stop the client direction. Public contracts, working
set semantics, new-session handoff, repository choice, and rollout planning
come **after** that decision.
