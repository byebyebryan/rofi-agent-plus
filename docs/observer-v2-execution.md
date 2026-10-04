# Observer v2 consumer execution

Date: 2026-10-04. Regular goal loop; implementation and validation owned by the
primary. This pass began only after Observer committed independent G1/G2
acceptance at `ca98337`. Its accepted producer artifact is source `222c5bf`,
version `0.2.0a3`, wheel SHA-256
`6aa793e6a0ba5ae37403f73999ea6e6d56bc8c21e5e4c7a10ac52cb946a45a3f`.
Observer's optional networking G3 remains deferred; Plus retains Host Mesh.

## Source boundary

Plus baseline: `536851b`, clean, 367 tests. New candidate: `0.14.0a1`, pinned
`agent-observer==0.2.0a3` pure contract library dependency. The frontend imports
public bounded JSON, snapshot and write validators; it does not import collectors
or provider action internals. Host-local discovery/actions remain independent
public commands. A v1-only installed tuple fails visibly and is not upgraded
through compatibility discovery.

The old private v1 parser, native executable acceptance, provider launch argv,
Claude cue parsing and background correlation were removed. Tests for those
retired consumer implementations were replaced with accepted public corpus,
route/effect/handoff and cache conformance checks. Provider predicates and launch
ownership tests remain in Observer's independent 153-test suite. Generic Host
Mesh, Tmux, cache, picker/navigation, batch/viewer ownership checks remain here.

Projection keeps full store-scoped references; phase, runtime, worker, outcome,
workspace, history and clocks remain separate. Attention ordering is blocked,
waiting, working, unknown, then actual conversation activity. Nullable activity
does not fall back to creation or metadata clocks. Confirmed children from both
providers are excluded; unknown rows remain. Cache version 7 is a deliberate
cutover. Partial coverage retains missing rows stale with original clocks and
does not overwrite current independent facts.

Write preparation can use existing pure route selection. Execution pins that
prepared route, validates exact request/plan/result provenance and dispatches
once. Timeout, missing marker, malformed result, stale post-write authority or
viewer failure never cause another provider operation. Cleanup owns only the
write-client launcher, preserving provider descendants. Tmux gets a public
literal-JSON native entry plan, not provider argv. Typed viewer name collisions
reuse the same plan. Current conversation binding remains unsupported.

## Development and artifact gates

The prerelease Observer package is not published. Before running checks, install
the accepted wheel in the development environment and install Plus with its
pinned dependency satisfied. Hosted CI now installs `.[dev]`; publication of the
Observer dependency is a separate prerequisite, not claimed in this run.

Example for the existing local artifact:

```sh
python3 -m venv /tmp/agent-plus-development
/tmp/agent-plus-development/bin/pip install \
  ~/.local/share/agent-observer/artifacts/agent_observer-0.2.0a3-py3-none-any.whl
/tmp/agent-plus-development/bin/pip install -e '.[dev]'
PATH=/tmp/agent-plus-development/bin:$PATH ./scripts/check
```

Candidate packaging/installation and native consumer results are recorded below
after the source gate. Neither the old Observer nor Plus pilot links are changed
by source checks or candidate installation. Graphical UI/focus, physical suspend,
coordinator reopen, compatible managed pilot selection and public release remain
separate gates. No source change was required in Observer to make Plus tests pass.

Source gate: all 309 tests, Ruff, format, shell/text checks and released public
bundle verification pass in the isolated development environment against the
accepted installed Observer wheel. Controlled write timeout preserves a separate
descendant, and cleanup removes only that test-owned child. Hosted CI was not run.

## Installed/native closure and continuation

E8 source checkpoint `90e5b4f` is packaged as `0.14.0a1`, wheel SHA-256
`8ba19a0eba87944aec2848542fb3ddcb91f3ccc33790ba42565928e18daa497b`, and installed
unselected on Snap and Starship at
`/home/bryan/.local/share/rofi-agent-plus/0.14.0a1-8ba19a0eba87944a`.
The [consumer report](evidence/2026-10-04-observer-v2/REPORT.md) records installed
conformance, passive native reads, real SSH read and native entry results.

Installed Plus/public Observer/native TTY delegation accepts Codex New/exact
Resume on both hosts and Claude New/live attach/saved Resume on Snap. Tmux
responses and Host Mesh authority are controlled; graphical/native Tmux and
ordinary remote writes are later gates. Missing Claude registry directories
correctly rejected before dispatch; the corrected history-only owned fixture
passed saved Resume. No producer modification was needed for that rejection.
All owned private stores and borrowed credentials were removed after namespace
exit. Ordinary auth/config/settings hashes and old pilot links stayed intact
during E8. The current coordinator was not reopened.

Next is a compatible managed tuple selection review. The current chezmoi source
has read/Plus links to the old tuple and no `agent-observer-write` link; installing
the new wheel alone does not make it available to frontend actions. Update
Observer read/write and both Plus entry links together on each selected host,
with scoped rendering/apply/verification and the old tuple retained. Then run
desktop picker/focus and actual local/remote Tmux entry acceptance. Keep current
conversation binding unsupported. Native transport-loss/suspend, coordinator
reopen and dependency publication/hosted CI are independent remaining gates.
No managed link/configuration edit, push or public release occurred here.

## Subsequent operational selection

The next regular goal loop retained these exact package bytes. Observer independently
accepted fresh installation and bounded watch/daemon recovery before consumer
work. Plus `a2dcf65` then accepted actual installed Tmux/SSH native entry in owned
namespaces: Codex New/exact Resume on both hosts and Claude New/live/saved Resume
on Snap. See [the operational proof](native-operational-proof.md); terminal and
private trust adapters are explicit, with no graphical acceptance claim.

Chezmoi `ac0f420` prepared all five selectors, exact artifact/live gates and private
guarded rollback. Observer `0.2.0a3` and Plus `0.14.0a1` are now selected on Snap
and Starship, including the public write entry and Rofi script link. Selected
ordinary passive reads, v7 refresh, headless callbacks and launcher preparation
pass on both hosts. The generic Tmux lifecycle gate covered both hosts with exact
cleanup and preserved SSH usage history. Ordinary provider hashes match baseline;
all owned provider test stores and borrowed auth copies are removed. Plus has
312 passing source tests; package/source code is otherwise frozen at `90e5b4f`.

Desktop names/pages/order, ordinary New/Resume focus/trust UX, physical suspend,
deliberate coordinator reopen and public publication/hosted CI remain manual or
separate gates. No push occurred. An unrelated managed external archive checksum
blocks broad chezmoi validation/apply; a reviewed five-template projection enabled
only this scoped pilot selection without changing those pins. The selected-live
reports under `docs/evidence/2026-10-04-operational` are bounded metadata, not raw
provider observations.
