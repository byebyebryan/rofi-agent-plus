# Observer API v1 client handoff

Date: 2026-10-07. Observer's independent producer gate is accepted. This pass
updates handoff documentation only; another agent owns Plus implementation,
consumer acceptance and rollout. Producer defects reopen an Observer checkpoint.
Do not edit its collectors/contracts to make frontend work pass.

## Accepted producer and selected baseline

Updated: 2026-10-07 after Observer-only operational acceptance. Another agent
owns Plus implementation. No frontend code or provider policy is changed here.

| Component | Tuple |
| --- | --- |
| Accepted/selected Observer | `0.4.0a7`; source `8ca7a789899066cd6c2399f8c6fad60e06e869e7`; SHA256 `09f41f33e6c22fe019e518af3deae067b58b42f571b84d6c45aebe706079865a` |
| Prefix, both hosts | `/home/bryan/.local/share/agent-observer/0.4.0a7-09f41f33e6c22fe0` |
| Public versions | API 1; snapshot/watch 3; write 1; separately prerelease service protocol 1 |
| Normal operations | Read/write/service links select a7; `agent-observer.service` is enabled on both hosts |
| Selected Plus | `0.14.0a1`; source `90e5b4fa49bace6d4d1d09be833f88faadea0246`; SHA256 `8ba19a0eba87944aec2848542fb3ddcb91f3ccc33790ba42565928e18daa497b` |
| Existing Plus reader/cache | Observer `0.2.0a3`, wire 2; cache 7. It cannot consume the new wire. Its private venv can shadow the selected CLI/writer. |
| Native targets | Snap Codex/Claude; Starship Codex. Codex leads; Claude remains required; OpenCode stays removed. |

In `../agent-observer`, read `docs/api-v1.md`,
`docs/shared-observation-service-handoff.md`,
`docs/shared-observation-service-client.md`, `docs/service-protocol-v1.md`,
`docs/write-client-contract.md`, `artifacts/observer-0.4.0a7.json` and
`docs/evidence/2026-10-07-observer-operations/REPORT.md`. Public fixtures are
`tests/fixtures/contract-v3` and `write-v1`; `scripts/check-public-api` is an
independent jsonschema/CLI consumer. Pure public/service codecs and the bounded
local service transport are consumer APIs; collectors and private proof helpers
are not. Wheels/manifests are archived on both hosts under
`~/.local/share/agent-observer/artifacts/0.4.0a7-09f41f33e6c22fe0`.
Hosted publication and downstream installation have separate gates.

Observer-only acceptance covers bounded startup workspace mappings,
installed/schema/native comparisons, exact user-unit arguments, restart/crash
watch reconnection and six-target rollback/reselection. Both normal units passed
30-minute three-reader checks plus a non-reading peer and 100 cached CLI requests.
Snap's 30/120-second runtime/history cadence measured 102-ms read p95 and 4.48%
of one core; Starship's 20/60 seconds measured 79 ms and 1.81%. Push distributes
poll results; it does not remove native sampling latency or provide durable events.

Two direct and two cached/native rounds match supported fields for Snap's 50
Codex identities, 30 Claude conversations and four live Claude workers, and
Starship's 92 Codex identities/four live contexts. One known setup-only Claude
candidate remains omitted. Old unproved classifications/states stay unknown.

Native entry proof is context bounded. A fresh Codex CLI 0.160.1/server 0.161.0
pair rejects a default feature mismatch; disposable entry proof disables
`api_key_model_discovery` only there. Ordinary default cold entry is not accepted.
The writer explicitly rejects sampled live foreground Claude Resume with
`unsupported_resume_route`; background attach and saved Resume have their own
proof. Make deferred errors visible and never redispatch an uncertain write.
Foreground question detection, physical wake, event-assisted latency and
terminal-specific binding remain separate gates.

Provider compatibility follows required protocol/metadata/CLI contracts, not
version/hash allowlists. Versions and executable replacement remain diagnostic
and action-context guards. Compatible daily upgrades require no registration.

## Migration semantics

- Preserve full host/provider/store-namespace/native-kind/native-ID identity.
  Match returned host authority to the reached route. Titles/UUID alone/cwd/PID
  and project keys never merge conversations.
- Display bounded native provider titles or UUID fallback. Equal titles are valid;
  Codex can settle its name after a completion callback. Observer-generated
  previews and separate Claude AI-title/nickname fields are not promised. Hide only confirmed children; unknown
  stays visible. Fresh exclusions must precede cache retention.
- Distinguish running, parked and unknown. Working/blocked/waiting are phase,
  with approval/question blocked reasons. Completed outcome, worker presence and
  attachment remain separate. Missing partial rows cannot prove stopped.
- Age uses current `activity.at` or explicitly stale `lastKnownAt`. Never renew
  it from refresh/create/mtime/rename/resume. Order blocked, waiting, working,
  unknown, then descending conversation activity and complete identity. Missing
  and clock-ahead ages stay explicit; active rows survive history caps.
- Inspect source coverage and each dimension's health. Preserve healthy siblings
  through failed providers/hosts. Capabilities are usable observation predicates,
  not action permission or guarantees for every row. Unknown finite reasons stay
  diagnostic, never idle/success/stopped.
- Recorded cwd, root/relative path and Git context have provenance. Only explicit
  project mappings join hosts; work/general categories remain presentation policy.

Wire 3 is an intentional cutover. Update the pure reader/dependency and consumer
cache version together; reject old snapshots. No legacy collector/converter is
supplied. Daily provider upgrades do not themselves reset the wire/cache.
Read-only consumers do not need writer support.

Claude parked requires a terminal idle job, complete stable inventory and proved
absence of bound workers. Saved-only absence, Codex parked inference, Claude
foreground questions/transient helper ancestry and current-viewer binding remain
unsupported. The proof PTY did not establish `/exit` behavior; viewer detach and
explicit native Stop were accepted separately.

## Transport and actions

Plus retains Host Mesh routes, SSH/deadline policy, partial-host cache and UI.
Observer is host-local and has no networking. Invoke candidate read/write paths
explicitly, including inside deferred TTY commands: Plus's venv contains old
Observer scripts and can silently shadow the intended executable in PATH.

```sh
observer_candidate=/home/bryan/.local/share/agent-observer/0.4.0a7-09f41f33e6c22fe0
"$observer_candidate/bin/agent-observer" api
"$observer_candidate/bin/agent-observer" snapshot --host-scope snap --provider codex --provider claude
ssh starship /home/bryan/.local/share/agent-observer/0.4.0a7-09f41f33e6c22fe0/bin/agent-observer snapshot --host-scope starship --provider codex
```

Start with envelope/fixture conformance and private cache/UI-state roots, then
use `service snapshot/list/watch` on the owning host's selected publisher.
Direct API watch 3 cannot parse service envelopes. Validate host/UID/boot/time
scope and component leases before using the embedded snapshot. Use one service
stream guard per connection; sequence starts at one and is distinct from shared
view revision. New service IDs, EOF, silence and gaps invalidate current claims;
reconnect creates a new guard and gap/resync replaces the view. Null warming
views are pending discovery, not complete empty discovery. A healthy heartbeat
cannot renew cached history or work evidence. Recompute display ages locally.

Local BOOTTIME values cannot be compared across hosts. Host Mesh retains route
validation and must conservatively translate remote freshness and transport loss.
There is no direct collection fallback, client-triggered refresh or native
completion replay. Keep this transport acceptance separate from UI and actions.

Use owning-host `agent-observer-write prepare/execute/enter`. Pass opaque guarded
plans unchanged; re-prepare changed context. Status/effect/requested/resulting
identity/handoff must be interpreted together. Codex execute may only prepare a
TTY entry; Claude saved Resume may confirm a new copied UUID. Retain confirmed
effects after viewer failures. Never automatically redispatch an uncertain
launch, try another route or stop provider work. Native entry replaces the
process; delayed entry failure must be visible in the terminal/UI.

Tmux Plus owns viewer/window lifecycle. Historical tmux options and attach counts
do not bind a selected conversation. Session-specific focus/Close/group actions
stay disabled pending current binding; generic terminal management stays separate.
The earlier silent Codex Resume incident is still not reproduced or explained by
these producer fixes. Notification events/publication are a separate Observer
client checkpoint; Plus should not diff watch into completion notifications.

## Client checkpoints

1. Reinspect Git/receipts/routes and freeze the accepted producer plus migrated
   reader/client tuple. Use fixture conformance and private caches first.
2. Validate age/order, parked/unknown labels, duplicate-title selection, child
   reconciliation, partial host/provider failure, stale recovery and active caps.
3. Prove single New/Resume on Snap for both providers and cross-host Codex,
   including prepared rejection and deferred TTY error visibility.
4. Independently review candidate artifacts/managed selection, then prove picker,
   real terminal entry, Kitty/tmux association, graphical focus/Open and remote
   recovery. Preserve unrelated changes and existing provider work.

The [earlier a11 plan](observer-a11-integration-plan.md) supplies useful consumer
cases but its producer/wire assumptions are superseded here. Report producer
issues with exact public input/output and tuple; Observer acceptance and frontend
acceptance remain separate. RLCD/349 physical work and public release are not
inferred from these documentation or native PTY gates.
