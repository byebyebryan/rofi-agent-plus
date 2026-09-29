# Agent Plus session client: presentation spike result

Date: 2026-09-25

Decision: **study a direct, full-window tmux attachment with a transient
session picker next**. The [next spike plan](agent-plus-session-client-direct-attach-spike.md)
defines that comparison. Keep the nested two-pane idea as a reference, but do
not start the client implementation or change Agent Plus contracts yet.
The [follow-up result](agent-plus-session-client-direct-attach-results.md)
stopped the transient-picker direction after the operator required an
always-visible session list. That result identifies WSNav's always-visible
Navigator as the UI design/code reuse target for a dedicated client; the
measured cursor-traffic increase still needs current visual judgment and is
not an automatic product rejection.
Later operator feedback in the
[ordinary-tmux rail result](agent-plus-session-client-ordinary-tmux-results.md)
reports that the same two-tmux topology produces very annoying visible flicker
in WSNav. The measured ratio alone was inconclusive; the lived visual effect
changes the presentation recommendation.

This is the result of the bounded [feasibility spike](agent-plus-session-client-spike.md),
not acceptance of a new daily workflow. All tests used disposable private tmux
sockets. The local list and processes were synthetic; no existing agent session
was attached or changed. No installed picker, ordinary tmux session, provider
configuration, or suite repository was modified.

## Environment and observations

Local tmux and Starship tmux were 3.7c. Ghostty 1.3.1-arch2 was installed,
but the PTY test did not establish a Ghostty visual result. The controlled
provider trial used Codex CLI 0.157.0. The local
execution environment was neither Snap nor Starship. Starship was reachable
over SSH with its existing known host key. Snap's SSH probe stopped because
this environment had no known ED25519 host key for it; no trust setting was
changed.

| Check | Observed result | Limit |
| --- | --- | --- |
| Local flat list and switching | A private presentation showed two synthetic runtime sessions and five peers from one directory. With the attachment focused, sending the runtime tmux's `Ctrl+B 2` through the presentation switched from alpha to beta. Both runtime pane processes and heartbeats persisted. | The list was a throwaway mock, not Agent Plus discovery or a client UI. With list focus, `Ctrl+B 3` reached the list as literal input and did not switch runtimes. |
| Local detach and reattach | Detaching the presentation viewer left the selected beta process running; its heartbeat advanced while detached. Reattaching showed beta again with the same pane process. | This was a local viewer detach, not a machine sleep or network failure. |
| Small terminal | At 80×24 the saved split squeezed the list to three columns. Explicitly resizing it to 24 columns gave a 24×24 list and 55×24 runtime pane, with all five peers visible. | A real client would need an adaptive minimum width or a different layout. The 55-column agent TUI may be cramped; readability in Ghostty was not visually accepted. |
| Remote transport | WSNav's existing synthetic private-socket harness passed on Starship: input round trip, resize, 256-color configuration, remote process persistence, and reattachment after recreating the local presentation. It reported complete cleanup and unchanged ordinary tmux state. | The harness recreates the outer local tmux server; it does not simulate a dropped network path, nor validate a production Host Mesh attach route. Native-provider assertions were not run. |
| Two viewers | Two disposable clients attached to one private runtime session, with a 55×24 nested pane and an 80×24 direct viewer. The runtime window measured 80×24. Input from the direct viewer advanced the same synthetic input count seen through the nested viewer. Both detached normally. | The shared session had one window. Independent window selection, focus, and resize behavior need a further controlled check before claiming shared-viewer UX is safe. |
| Terminal stream A/B | With the same bounded synthetic workload, nested versus direct tmux emitted 2.254× cursor-motion sequences, 1.206× total CSI sequences, 1.455× cursor-visibility sequences, and 1.116× bytes. The cursor-motion result exceeds WSNav's recorded 1.5× limit. | These are PTY stream counts, not a Ghostty visual judgment. The harness's two Boolean threshold flags are defective: their shell arithmetic wraps a Python command that prints nothing. The numerical ratios and independent 1.5× comparison support the cursor-motion finding; the 1.116× byte ratio is below the recorded 1.3× byte limit. |
| Native Codex TUI | The unmodified isolated WSNav harness built its private runtime and presentation, then stopped because the expected workspace trust prompt was absent. A temporary study-only copy skipped that outdated gate; it reached resize and focus checks, but Codex opened its Agent command center and the expected result never appeared. Both reported complete cleanup and unchanged ordinary tmux state. | Typing, streaming, clipboard, mouse, and reconnect with a live Codex conversation remain unproven on 0.157.0. A further `--no-daemon` diagnostic exited before a provider pane was captured and supplied no acceptance result. |

The local switching and shared-viewer checks used a synthetic process and
synthetic input. The remote check used `spikes/tmux-remote-transport.sh`; the
terminal stream check used `spikes/codex-terminal-fidelity.sh`; the native check
used `spikes/codex-terminal-presentation.sh` and a temporary copy for the
current startup flow. The remote and native harnesses reported complete
cleanup and unchanged ordinary tmux state. The local mock also removed its
private sockets and processes and matched its before/after ordinary tmux
fingerprint. Literal prefix escaping, other modified keys, mouse interaction,
copy/scroll, and visual alternate-screen behavior were not checked manually;
the stream A/B included alternate-screen and high-churn synthetic output. No
raw provider pane capture or auth copy was retained.

## Why the next spike changes topology

The one-window switch and reconnect path works in the synthetic checks, but
the extra tmux renderer still amplifies cursor movement on tmux 3.7c, and the
current Codex TUI has not passed a native trial in this layout. Those are the
two practical risks behind WSNav's earlier terminal-fidelity concern. A
client plan built on the nested rail would be premature.

The next bounded comparison should use **one full-window direct tmux client at
a time**. A transient flat picker can run before attach and reappear after the
native tmux detach action; choosing another card attaches that session in the
same terminal window. This loses the always-visible rail and makes switching
more deliberate, so compare A → B → A and five same-directory peers against
the nested rail. Measure the direct terminal stream and visually check the
native TUI in Ghostty. Keep the runtime sessions owned by their existing tmux
servers and leave Agent Plus as the history/discovery owner.

That follow-up must also test a real network interruption, two viewers with
different sizes and active windows, and a current-version provider startup
path. It should stay a disposable presentation study. Discovery identity,
action-time attach routing, working-set persistence, Herdr integration, and
public CLI changes remain undecided. Herdr was present locally but was not run
in this spike, so this record makes no hands-on Herdr comparison claim.
