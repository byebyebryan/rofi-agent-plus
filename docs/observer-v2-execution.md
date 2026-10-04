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
