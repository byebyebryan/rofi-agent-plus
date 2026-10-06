# Observer API v1 client handoff

Date: 2026-10-06. Observer's independent producer gate is accepted. This pass
updates handoff documentation only; another agent owns Plus implementation,
consumer acceptance and rollout. Producer defects reopen an Observer checkpoint.
Do not edit its collectors/contracts to make frontend work pass.

## Accepted producer and selected baseline

| Component | Tuple |
| --- | --- |
| Accepted Observer candidate | `0.3.0a2`; source `7ffbe246e6bac2c9d320328ce639b833b3a73bfa`; SHA256 `98ce1f9203f97c13adc2d19cb11bf8f13a0dc3889fc899debc94da1bf0b1430d` |
| Candidate prefix, both hosts | `/home/bryan/.local/share/agent-observer/0.3.0a2-98ce1f9203f97c13` |
| Public versions | API 1; snapshot/watch wire 3; write request/plan/result wire 1 |
| Normal selected Observer | `0.2.0a11`; SHA256 `999d45171adfc4c4e62fe57b417614c0814de2a556693a6d23bb97b45a0a8b65`; wire 2 |
| Selected Plus | `0.14.0a1`; source `90e5b4fa49bace6d4d1d09be833f88faadea0246`; SHA256 `8ba19a0eba87944aec2848542fb3ddcb91f3ccc33790ba42565928e18daa497b` |
| Existing Plus reader/cache | Observer `0.2.0a3`, wire 2; cache 7. This reader cannot consume wire 3. |
| Native targets | Snap Codex/Claude; Starship Codex. Codex leads; Claude remains required; OpenCode stays removed. |

In the adjacent `../agent-observer` checkout read `docs/api-v1.md`,
`docs/api-v1-client-handoff.md`, `docs/public-contract-v3.md`,
`docs/write-client-contract.md`, `artifacts/observer-0.3.0a2.json` and
`docs/evidence/2026-10-06-stable-api/REPORT.md`. Public fixtures are
`tests/fixtures/contract-v3` and `write-v1`; `scripts/check-public-api` is an
independent jsonschema/CLI consumer. The pure supported Python facade is
`agent_observer.public`; internal collector/worker modules are not client APIs.
The a2 wheel is archived under `~/.local/share/agent-observer/artifacts` on both
hosts. Hosted dependency availability/public release are separate gates.

A2 repairs review R1-R8: contract-based native support, stale age, child/watch
reconciliation, row/byte/node retention bounds, row fault isolation, write-result
identity semantics, independent reference coverage and compatibility policy.
Native New/Resume/recovery, held Claude approval/question, stop-to-parked and
partial sibling cases pass. A1 failed a Claude local-command housekeeping-age
case; use a2. This does not establish Plus GUI or native-route acceptance.

Normal a11 still contains old provider release/hash gates. A2 is separately
installed; no normal links/provider settings/hooks/sessions were changed.
Do not infer native capability from the selected a11 path during client testing.
Provider versions are diagnostic; compatible daily updates need no registration
or Plus release pins. Actual executable replacement invalidates old write plans.

## Migration semantics

- Preserve full host/provider/store-namespace/native-kind/native-ID identity.
  Match returned host authority to the reached route. Titles/UUID alone/cwd/PID
  and project keys never merge conversations.
- Display bounded native user titles or UUID fallback. Equal titles are valid;
  AI previews/nicknames are not promised. Hide only confirmed children; unknown
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
observer_candidate=/home/bryan/.local/share/agent-observer/0.3.0a2-98ce1f9203f97c13
"$observer_candidate/bin/agent-observer" api
"$observer_candidate/bin/agent-observer" snapshot --host-scope snap --provider codex --provider claude
ssh starship /home/bryan/.local/share/agent-observer/0.3.0a2-98ce1f9203f97c13/bin/agent-observer snapshot --host-scope starship --provider codex
```

Start with snapshot refresh and private cache/UI-state roots. Watch may follow:
replace views on initial/change/resync, retain on heartbeat, mark gaps, track
stream UUID/revision continuity and reconnect with a new epoch. It is sampled
push, without native completion replay or cross-host ordering.

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
