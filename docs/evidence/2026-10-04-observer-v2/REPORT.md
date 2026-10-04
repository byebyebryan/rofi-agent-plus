# Observer v2 consumer acceptance

Date: 2026-10-04. E8 accepted for the source, installed and bounded native matrix
below. Observer closed independent G1/G2 at `ca98337` before Plus implementation;
its producer code and accepted wheel stayed unchanged throughout E8. Optional
Observer networking G3 remains deferred. Plus retains external Host Mesh.

## Exact candidate tuple

| Component | Source | Version | Wheel SHA-256 |
| --- | --- | --- | --- |
| Observer | `222c5bf` | `0.2.0a3` | `6aa793e6a0ba5ae37403f73999ea6e6d56bc8c21e5e4c7a10ac52cb946a45a3f` |
| Plus | `90e5b4f` | `0.14.0a1` | `8ba19a0eba87944aec2848542fb3ddcb91f3ccc33790ba42565928e18daa497b` |

Both candidates are installed **unselected** on Snap and Starship:

- `/home/bryan/.local/share/agent-observer/0.2.0a3-6aa793e6a0ba5ae3`
- `/home/bryan/.local/share/rofi-agent-plus/0.14.0a1-8ba19a0eba87944a`

The wheel files are retained under each project's `artifacts` directory below
`~/.local/share`. Plus pins the core-only Observer Python package for pure public
validators; host-local collection and actions use the independent Observer CLI.
Snap's external Observer environment has the source-built Claude history SDK;
Starship and the Plus environments need no Claude SDK or bundled executable.

## Acceptance matrix

| Layer | Case | Result and scope |
| --- | --- | --- |
| Source | Full local gate | 309 tests, Ruff, format, shell/text and released Host Mesh/Tmux bundle checks pass |
| Installed | Frozen public read/watch/write corpus | Hashes and public projection/validators pass under isolated interpreters outside the checkout on both hosts; provider internals are not imported |
| Installed | Console entry | Installed diagnostic help passes on both hosts |
| Native read | Ordinary host-local public snapshot | Both hosts pass; unknown conversation age remains unknown, partial Claude saved coverage remains explicit |
| Native read | Snap to Starship SSH | Real SSH/marker/public snapshot passes with an explicit candidate executable override and controlled Mesh authority; route-hint persistence disabled |
| Native entry | Codex New/exact Resume | Installed Plus prepare/execute/handoff delegates once to the accepted public CLI; native TTY entry and exact full identity pass on both hosts |
| Native entry | Claude New/live Resume | Snap native background creation and exact attach pass; waiting/completed and current worker retained independently |
| Native entry | Claude saved Resume | Snap history-only fixture passes; requested identity retained and actual returned identity independently resolved |
| Preflight | Missing Claude registry directories | Incomplete runtime coverage rejects before any write; restoring empty owned directories admits the separate saved-history case |

[Snap package](packaged-snap.json), [Starship package](packaged-starship.json),
[Snap entry](native-snap-initial.json), [Starship entry](native-starship-initial.json),
[saved entry](native-snap-saved.json) and [preflight rejection](missing-runtime-rejection.json)
contain bounded metadata only. No titles, prompts, responses, provider payloads
or terminal captures are repository evidence.

Native entry uses real installed Plus/backend code, public Observer commands
and provider executables. **Tmux responses are controlled**, with an owned PTY
executing the public handoff. **Host Mesh authority is controlled**. This proves
the consumer delegation/native entry path; it does not establish graphical
window behavior, ordinary remote write routing or managed pilot selection.
Source tests separately cover pinned remote execution, reached-domain failures,
marker loss, transport/timeouts, effect uncertainty, copied results, stale
authority, name collisions and preservation of provider descendants after
launcher timeout. No possible provider dispatch is automatically repeated.

Codex 0.160.0 and Claude 2.1.287 use Observer's fixed accepted executable hashes.
Claude proofs use an operator-created private trusted workspace. Trust-dialog
UX is unclaimed; the client does not approve native prompts. Saved resume
returned the original UUID; copied UUID handling is controlled coverage.

## Cleanup and remaining gates

Private user/PID/mount namespaces, private `/tmp` and masked ordinary provider
paths were established before native launch. Namespace exit removed provider
descendants; owned private stores, histories and borrowed credential copies
were then deleted. [Snap cleanup](cleanup-snap.json) and
[Starship cleanup](cleanup-starship.json) verify preserved auth/config/settings
hashes for this E8 window, including Snap's global Claude preferences. The
earlier Observer G2 window had separately documented unrelated preference drift;
this narrower unchanged result does not rewrite that evidence.

Selected links remain Observer `0.1.0a5-b68ebf4cefbd8b58` and Plus
`0.13.0a3-76f8a951e2975283`. No global `agent-observer-write` link was added.
Ordinary hooks, runtime policy and the current coordinator were preserved.
Candidate checks used explicit entry paths/PATH and private consumer caches.

Compatible managed tuple selection must update both read and write entrypoints
on each target host. Desktop picker/selection/focus, actual Tmux windows, native
remote writes/transport-loss recovery, physical suspend and coordinator reopen
remain separate acceptance. The prerelease Observer dependency is unpublished;
hosted CI and public release are pending. No push, publication or fleet rollout
was performed. The local source/package/native gates are complete within these
explicit bounds.
