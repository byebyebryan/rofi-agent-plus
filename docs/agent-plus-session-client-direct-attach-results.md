# Agent Plus session client: direct-attachment spike result

Date: 2026-09-25

Decision: **stop the full-window direct-attachment plus transient-picker
direction as the primary client presentation**. The operator requires an
always-visible session list during agent work. Native tmux detach returning to
a picker, followed by Enter to attach another session, does not meet that
requirement. This is a product-fit result even though the completed synthetic
transport checks passed.

Later feedback on the [ordinary-tmux rail spike](agent-plus-session-client-ordinary-tmux-results.md)
keeps the always-visible rail requirement but rejects assuming WSNav's nested
two-tmux renderer is acceptable: the operator finds its visible flicker very
annoying. The rail design and renderer topology are separate choices.

The [spike plan](agent-plus-session-client-direct-attach-spike.md) used private
tmux sockets and synthetic processes. No existing conversation, ordinary tmux
session, installed picker, provider hook, or public contract was changed.

## Observed evidence

| Area | Result | Limit |
| --- | --- | --- |
| Local A → B → A | A throwaway five-card picker launched foreground direct tmux clients at 80×24 and 160×48. Native `Ctrl+B`, `d` returned to the picker with the current card selected; Enter attached another full-window runtime. Both synthetic pane processes retained their identities and advanced while detached. Escaping the tmux prefix also delivered a literal `Ctrl+B` to the synthetic pane. | The measured transitions were automated PTY timings, not a Ghostty visual or human speed judgment. The list is absent while the agent TUI is visible. |
| Remote direct path | On Starship tmux 3.7c, a foreground `ssh -tt` client attached to a private remote tmux session. Native detach returned control. Cutting only the test-owned SSH process left the remote pane running; reattach restored input. An injected route failure did not change the runtime. | This cut tests SSH transport closure, not laptop sleep/wake. It did not exercise production Host Mesh routing or ordinary sessions. |
| Two viewers | Private viewers at 80×24 and 120×36 both displayed the initial runtime and sent input to the same pane. Switching the session window from one viewer moved **both** clients from window 0 to 1. The private tmux server used the `latest` window-size policy; observed runtime size varied between 80×24 and 120×36 across runs. | This is a shared tmux-session selection effect that a real client would have to respect. No viewer was force-detached. |
| Terminal fidelity and native TUI | The earlier nested-path study measured 2.254× cursor motion against direct tmux. The direct local loop used one tmux renderer. | The operator's always-visible-list requirement stopped Gate 2 before a new stream result and stopped Gate 5 before a current-provider or Ghostty visual trial. Mouse, copy/scroll, modified keys, and dynamic resize remain untested for this candidate. |

The local PTY harness measured 0.9–2.4 ms for detach and 4.8–7.8 ms for
selection plus attach readiness across the two sizes. These are automated
transition timings; they exclude a person's selection time and any judgment
of the on-screen experience.

The remote experiment used a temporary local script and remote private socket;
it removed both after the checks. It reported complete cleanup and unchanged
ordinary tmux fingerprints on both hosts. The local mock also removed its
private sockets and files and matched the before/after ordinary tmux
fingerprint: nine sessions and the same hash. No provider, SSH, or remote path
was used by the local mock. The stopped Gate 2 attempt produced no new stream
metrics; the 2.254× figure above belongs to the previous spike, not this run.

## What this decides

The direct path is useful evidence that a foreground tmux client can return to
a launcher and reconnect to the same running process. It does not solve the
product's presentation problem: the active sessions must remain visible while
working in the native agent TUI. The shared-viewer window movement is an
additional behavior to consider for any tmux-backed design.

The leading UI reuse target is now **WSNav's always-visible Navigator**:
its left pane remains visible beside the native provider TUI, including while
the provider pane has keyboard focus. WSNav accepted its residual cursor
artifact as nonblocking V1 polish; the synthetic cursor-traffic ratio is a
reason to check current visual behavior, not grounds by itself to reject the
layout. The next study should exercise that layout with a disposable runtime
shaped like an existing Agent Plus tmux session and obtain current Ghostty
visual feedback. The [ordinary-tmux spike plan](agent-plus-session-client-ordinary-tmux-spike.md)
sets out that study.

The intended product remains a dedicated client. Borrow the Navigator layout,
card and selection behavior, focus cues, and suitable code/tests from WSNav.
Keep WSNav's host-local Workstream and private Runtime lifecycle out of this
client; Agent Plus's native conversation history and ordinary tmux sessions
have different identities and ownership. The attach and visual boundaries
still need study before implementation. This spike publishes no Agent Plus
consumer contract or client implementation.
