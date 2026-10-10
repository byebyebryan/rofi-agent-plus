# Installed operational proof

Date: 2026-10-04. The unchanged `90e5b4f` / `0.14.0a1` wheel consumes the
independently accepted Observer `222c5bf` / `0.2.0a3` wheel. This gate uses the
installed interpreter under `-I -B`, never the source tree as its package path.

`scripts/native-fixture` owns disposable user/PID/mount namespaces, private
provider stores, private Tmux endpoints and terminal PTYs. It masks ordinary
provider paths before possible native startup. Borrowed authentication stays
in mode-0600 private files and is removed with the fixture. An explicit operator
trust fixture covers Claude entry; native trust-dialog UX remains manual.

`scripts/check-native-entry ROOT initial` uses the installed Plus backend and
lifecycle with actual installed Host Mesh and Tmux public CLIs. A copied Mesh
configuration retains its routes while its SSH executable wraps real SSH entry
in the already isolated peer namespace. A terminal/user-scope adapter attaches
an owned PTY to actual Tmux. It neither mocks Tmux responses nor manufactures
provider results. Terminal data is consumed in memory and never written to evidence.

Accepted cases: Codex New/exact Resume on Snap and Starship through real SSH;
Claude New/live Resume and native phase-clock preservation on Snap. Stopping
only the Snap fixture namespace and clearing only its owned runtime registries
preserves Claude history. `scripts/check-native-entry ROOT saved` then accepts
history-only Resume through real Tmux with the full resulting reference.
Every case requires exactly one public prepare/execute pair. Fixture restart
requires a recorded stop; enter/stop reject changed process incarnations.

A real read-only SSH disconnect/reconnect leaves the owned remote namespace
alive. Native uncertainty after a possible provider write is still covered by
controlled evidence, not a real uncertain-write experiment. Replaced/expired
plans and failed presentation retain their existing independently accepted
source/controlled cases. None of these tests supplies current-client binding,
focus, conversation-specific Close, sleep/wake or ordinary desktop acceptance.

Prepare/start the private fixtures on both hosts, save the peer route/root and
resolved SSH policy in `ROOT/peer.json`, then invoke `enter ROOT --` with the
installed Plus Python and the proof script. The helper requires the exact
accepted prefixes and provider cache; it is an explicit native action proof,
not a passive daily diagnostic. Stop each exact namespace before deleting its
owned `native-HOST` store. Retain no ordinary provider test sessions.

For local development, run the environment's Python with
`scripts/bootstrap-dev --observer-wheel /absolute/path/to/the/accepted/wheel`.
It verifies the exact producer hash before installation. CI requires the
repository variable `AGENT_OBSERVER_0_2_0A3_WHEEL_URL` to supply those exact bytes;
that input/publication and hosted CI remain pending. This operational pass does
not publish either package or change the pinned public dependency.

Current artifact and selector tooling is documented in [managed operations](managed-operations.md).
