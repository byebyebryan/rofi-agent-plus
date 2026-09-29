# Agent Plus session client: persistent-rail ordinary-tmux spike

Date: 2026-09-26

Status: study completed; see the [results](agent-plus-session-client-ordinary-tmux-results.md).
No client implementation or deployment followed.

The [direct-attachment result](agent-plus-session-client-direct-attach-results.md)
established that a hidden picker does not meet the operator's requirement: a
flat session list must remain visible while an agent TUI has focus. This study
returns to WSNav's two-pane Navigator pattern and tests the missing boundary:
attaching its right-hand surface to **ordinary tmux sessions** instead of
WSNav-owned private Runtimes. The [exploration note](agent-plus-session-client-exploration.md)
records the wider product questions. No Rofi Plus contract or daily workflow
changes during this spike.

## Decision to make

Can one always-visible, flat Navigator and one native TUI pane switch among
test-owned sessions on the normal local and remote tmux servers, without
changing those servers' configuration, ending their processes, or confusing a
lost viewer with a stopped conversation? Does the resulting interaction feel
good enough in Ghostty to justify a later client and contract design pass?

The topology under test is:

```text
Ghostty -> private presentation tmux
             | left: flat, always-visible Navigator
             | right: nested tmux client -> normal local tmux server -> session
                      or SSH -> normal remote tmux server -> session
```

The presentation owns only its panes, selection, focus, and test viewer
processes. The normal tmux servers own their sessions; the provider owns its
native conversation. Switching the right pane ends or detaches only the old
**viewer**, then attaches the selected session. Removing a card, losing SSH,
or closing the presentation must not kill a tmux session or infer that its
agent stopped.

## Reuse boundary and test equipment

- Borrow WSNav's visible 32-column Navigator geometry, two-line card style,
  selection/focus cues, two-pane private presentation, and relevant navigation
  tests. Use a visually faithful subset with five distinct cards for one
  directory plus host/provider context. Record any differences from WSNav's
  rail. This can be disposable copied/adapted code in temporary storage; do
  not modify or launch WSNav's managed Workstreams for the study.
- Replace WSNav's `_provider_attach` path with a test-only adapter that
  resolves a fixture's exact tmux target at action time and launches `tmux
  attach-session` or an SSH-wrapped attach **inside the right pane**. WSNav's
  current helper requires its Workstream/Runtime revisions, private socket,
  lifecycle reconciliation, and owned-runtime `Ctrl+B` handling. Those are not
  ordinary-session contracts.
- Create uniquely named, marked, disposable sessions on the **default** tmux
  servers. This is what makes the test different from the two completed
  private-socket spikes. First use synthetic long-running processes; use a
  disposable native provider conversation only after the synthetic gates pass.
  Never attach to or alter an existing user session. Record each fixture's
  server generation, session ID, creation time, session name, and process
  identity. Revalidate the fixture before every attach or cleanup. A reused
  name or stale ID must refuse attachment.
- Keep all presentation tmux options and key bindings on its private socket.
  Do not change default-server global options, key tables, hooks, window-size
  policy, or user configuration. Fingerprint non-fixture sessions and relevant
  options before and after. Clean up only exact marked fixtures and the
  presentation's own processes/socket; preserve anything whose identity
  changed or is uncertain.
- Use the existing SSH trust and route to Starship for remote checks. A
  test-owned SSH child may be terminated to simulate transport loss. Do not
  interrupt the host network or other SSH clients. Do not read Agent Plus's
  private cache, install provider hooks, change suite repositories, or publish
  a new API. Current Tmux Plus `open` launches/focuses an OS terminal, so this
  test adapter is not a proposed use of that public operation.

The code references are WSNav's [pure Navigator model](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/src/navigator/view.rs),
[presentation topology](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/src/presentation/mod.rs),
and [outer key controls](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/src/presentation/control.rs).
Its [attachment helper](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/src/app/launch.rs)
and private Runtime lifecycle are explicitly outside the reuse target.

## Ordered gates

Each gate records automated evidence and, where requested, separate operator
visual feedback. A numerical terminal-stream result cannot stand in for
Ghostty visual acceptance. Stop at a failed boundary and write down the
result instead of broadening the prototype into a client.

| Gate | Exercise | Required observation |
| --- | --- | --- |
| 0. Baseline and fixture safety | Record tmux/Ghostty versions, current default-server sessions, global key/size options, and non-fixture fingerprints. Create five marked synthetic sessions in one directory on the local default server. | Fixture identities are distinct from user sessions. Presentation options are confined to its private server. Pre-existing sessions and options can be compared after cleanup. |
| 1. Persistent rail and local switching | Show A in the right pane, use the rail to switch A -> B -> A, and repeat while the agent pane has focus using WSNav-style outer controls. Try a wide terminal and 80x24. Detach and reattach the presentation. | The list stays visible throughout; card selection and pane focus remain distinct; the selected fixture receives input; A and B keep the same running processes and advance while not visible; no second OS window appears. Record keystrokes, time to usable TUI, and whether the 80x24 pane is readable. |
| 2. Nested input and display | With the normal server's actual tmux configuration, test outer `Ctrl+B` focus/switch actions, forwarding its prefix to the inner tmux client, an inner tmux command, and a literal prefix delivered to the synthetic app. Check modified keys, alternate screen, mouse, copy/scroll, paste, and repeated resize. Compare a bounded high-churn stream with direct attach at the same geometry. | Keys go to the intended layer and target. No outer binding silently steals a needed inner action. Record the actual inner prefix and key table, visual artifacts, cursor-motion/CSI/byte counts, and deviations from direct attach. Prior 2.254x cursor traffic is a comparison point, not an automatic failure threshold. |
| 3. Ordinary-session behavior | Use a marked fixture with two windows and two panes; attach a second viewer at a different size. Change the active window, pane, and size from each viewer, then switch the rail away and back. Inspect the server's existing window-size policy without changing it. Replace one unused marked fixture with a new session of the same name and try its stale reference. | Record whether viewers share window selection, whether the card can show a different process than expected, the effective runtime size, and whether replacing the nested viewer leaves another client unaffected. The stale reference must refuse. A session ID alone must not be claimed as proof of the visible conversation. |
| 4. Remote loss and recovery | Add a marked synthetic session on Starship's default tmux server. Switch local -> remote -> local, cut only the test-owned SSH viewer, inject a pre-attach route failure through the test adapter, then reattach the exact remote fixture. Also detach and reattach the outer presentation. | The rail remains usable and clearly shows disconnected/unreachable state; the remote pane process survives; reconnection restores input to that process. No route failure triggers a provider resume, kills a session, or silently attaches a changed target. A controlled SSH cut is not a laptop sleep/wake acceptance test. |
| 5. Native TUI and operator check | If gates 0-4 hold, establish one disposable current-version native agent conversation directly first, then repeat A -> B -> A through the rail in Ghostty. Check typing, streaming, scroll/copy, mouse, paste, resize, inner tmux keys, and reconnection. Use a small display as well as a wide one. | Record provider/version and exact operator visual feedback separately from synthetic checks. Do not capture prompts, credentials, or conversation output as evidence. If startup or auth isolation prevents a clean trial, mark native fidelity unproven rather than blaming the presentation. |

The default-server fixture, multiple-window case, and inner key handling are
necessary for this question. Earlier private-socket tests cannot substitute
for them. The multi-window case may reveal a limit that requires a later
eligibility rule or a window/pane-specific attachment contract; this spike
should report the behavior, not invent that contract.

## Result and exit rule

Produce a short decision record with a gate-by-gate pass/fail/unknown table,
topology and process identities, before/after ordinary-tmux fingerprints,
local and remote switch/reconnect observations, key-routing and two-viewer
behavior, direct-versus-nested stream counts, Ghostty visual feedback, exact
cleanup, and untested cases. Mark current native TUI and operator feedback as
unknown if they are not observed.

Recommend a dedicated-client design pass only if the rail stays useful while
the TUI has focus, exact input reaches the selected ordinary session, viewer
switching and SSH failure preserve runtime processes, and the operator accepts
the native TUI on a small screen. Otherwise record the specific failed seam
and choose a narrower follow-up or revisit Herdr. Even a passing spike does
not establish provider-conversation identity inside tmux, discovery/history
completeness, duplicate-free resume, a validated public attach operation,
working-set persistence, or Rofi integration. Those need separate design and
contracts after the presentation decision.
