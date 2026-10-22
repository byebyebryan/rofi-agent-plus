# Direct TUI candidate execution record

> Historical record from the October 2026 direct-TUI transition. Commands,
> labels, refresh behavior and completion status below describe that stage.
> Use the [current guides](../README.md) for present behavior.

The direct environment/window picker is implemented as `0.15.0a1`. This record
separates the tested candidate from desktop selection, publication and final
human input acceptance. The original [design](agent-plus-daemon-tui-design.md),
[review](agent-plus-daemon-tui-design-review.md) and
[implementation plan](agent-plus-daemon-tui-implementation-plan.md) remain
byte-for-byte preserved as inputs.

## Result and implementation

Agent Plus discovers native Codex/Claude environments through a headless host
helper and public SSH Plus Host Mesh. The desktop displays one row per owned
direct TUI window, with a launcher row when no window is registered. Open
focuses the exact selected window; Tab selects Open another window. Native
managers retain conversation, authentication and daemon responsibilities.

The source cutover removes the former Observer/Tmux/session runtime and runtime
dependencies. It adds strict host references, finite passive inventory, canonical
home/profile identity, private Niri/Kitty registration, replay-safe requests and
independent foreground clients. The responsibility/test replacement map and
operational contract are in [SUITE_INTEGRATION.md](../SUITE_INTEGRATION.md).

The candidate uses the plan's **opening/Alt+R snapshot fallback**. Actual Rofi
2.0 idle-timeout experiments fired once per idle interval rather than providing
a continuous timer. Native testing also established the need to arm filter and
selection retention on every frame, enable custom hotkeys and use Rofi's row
delimiter consistently. Titles and catalog observations refresh on opening or
the next explicit callback; exact window existence is freshly checked on Open.

Missing, expired or partial catalogs refresh before the first frame. A complete
fresh catalog can render immediately. Refresh is bounded to 20 seconds for the
fleet, 12 seconds per host and four concurrent host workers. In the actual
Starship/Snap Mesh measurement, a cold first frame took **0.72 seconds** and a
second opening took **0.79 seconds**. Both catalogs were partial, so both
openings refreshed; these figures are one sample, not an upper-bound claim.

## Artifact and source provenance

Private evidence root on Starship and Snap:

```text
/home/bryan/.local/state/rofi-agent-plus/proofs/direct-20261022T035458Z
```

| Item | Value |
| --- | --- |
| Baseline Git revision | `ed27684683dbd59e73aa1f5ff46670d971d857d3` |
| Candidate version | `0.15.0a1` |
| Wheel | `wheels/rofi_agent_plus-0.15.0a1-py3-none-any.whl` on Starship; same filename at evidence-root level on Snap |
| Wheel SHA-256 | `549d5ec2b9fb66a052be41f71ed0b471d5242f47916c20364f87b29ba97fbd0c` |
| Isolated prefix, both hosts | Evidence root plus `/candidate-r1` |
| Installed Python | Starship 3.11.17; Snap 3.11.10 |
| Build snapshot | `source-candidate-r1`, indexed by `source-manifest-r1.json` |
| Build snapshot digest | `0ba192925e91013ddde4b6982ea0cb6b8e2c543f7308b399cc4495970832a616` |
| Final working-tree index | `source-manifest-final.json` in the private evidence root |

The wheel was built from the sanitized source snapshot outside the checkout.
Subsequent proof-driver, test and documentation corrections do not change its
26 packaged files. The final source/wheel parity receipt verifies that exact
runtime correspondence. The final tree is an uncommitted reviewable diff on
`main`; the original README is preserved in the private baseline copy.

## Validation and receipts

Receipt paths below are relative to the private evidence root. They contain
sanitized identity/check results; raw owned terminal captures remain private.

| Gate | Evidence | Scope |
| --- | --- | --- |
| Source | `source-gate-final.log` | 120 unit tests, Ruff, shell/text guards, released Host Mesh checksums/provenance and artifact-gate self-test |
| Python 3.11 installed unit run | `installed-unit-final.log` | Current source tests executed with candidate Python isolation and installed runtime imports |
| Starship wheel install | `artifact-starship-r1-2/receipt.json` | Isolated import, wheel byte parity, metadata/assets/entrypoints, no runtime dependencies, controlled headless inventory |
| Snap wheel install | `artifact-snap-r1/receipt.json` | Same exact wheel and independent installed-artifact gate on Snap |
| Niri/Kitty mechanics | `window-mechanics-candidate-r1/receipt.json` | Real foreground TTY, private control, dynamic title/exact focus, extra-tab refusal, interrupt survival and independent exit |
| Rofi snapshot/callback | `rofi-snapshot-candidate-r1/receipt.json` | Real Rofi with inert rows; filter/action/selection stability and zero provider actions |
| Actual picker actions | `picker-actions-reviewed-r1.json` | Primary acceptance of recorded candidate launcher/callback observations: Open, additional window, exact first-window focus after picker exit and stale-row refusal with a live surviving picker |
| Local Codex | `native-local-candidate-r1-2/receipt.json` | Two actual managers, separate synthetic sessions, independent drafts/size/exit, replay and exact focus |
| Snap Codex OSS | `native-oss-candidate-r1-2/receipt.json` | Same native checks through SSH; owned SSH disconnect without replay |
| Snap Codex Work | `native-work-candidate-r1/receipt.json` | Same native checks; remote path contains spaces and apostrophes |
| Snap Claude | `native-claude-candidate-r1/receipt.json` | Two actual managers, independent unsent drafts/size/exit, exact focus, replay and owned SSH disconnect |
| Simultaneous profiles | `native-profile-separation-r1.json` | OSS and Work both running, separate canonical homes and daemon processes, logical host `snap` despite OS hostname `80H1VV3` |
| Catalog latency | `catalog-latency-candidate-r1-2/receipt.json` | Actual public Mesh, explicit candidate helper/config on Snap; passive first-frame measurement |

Native versions were Codex **0.163.0** and Claude Code **2.1.306**. Every native
entry used the installed candidate package/helper/viewer, rather than checkout
imports. The native case drivers ran with candidate `python -I -B`.

Codex fixtures contained two synthetic native rollouts. Claude used an empty
owned home: independent drafts, resize and lifetime were automated; selecting
different real Claude agents remains on the human checklist. No proof submitted
an agent turn or required a model response. Manager entry itself did not ask
Agent Plus to create a conversation. Native sign-in/trust prompts were kept
native, with ordinary home-directory trust declined in the owned Codex cases.

Earlier failed proof attempts remain in the private run directory. They exposed
Rofi protocol details and harness assumptions about native row ordering, filter
selection and Escape return codes. The accepted receipts name the corrected
case; a failed attempt is never counted as a pass.

The actual picker action review links the untouched
`picker-actions-candidate-r1-4` receipt, callback log, input sequence and explicit
live stale-picker confirmation by digest. That driver's final predicate failed
because it also required native-window focus while Rofi still owned its modal
grab. Its independent observation after Rofi exited verified the exact selected
window ID and terminal process generation. The primary reviewed that evidence
under the corrected condition; the original failed receipt remains unchanged.
Further GUI reruns stopped when the user reported disruptive focus stealing.
The corrected proof driver is source-checked; no later end-to-end driver pass
is claimed.

## Preservation, delivery and remaining acceptance

Fixture cleanup uses the exact private home and recorded daemon process birth
identity. Native shutdown stops only a daemon owned by that fixture, after its
clients close. Same-host login-cache copies are removed after proof. Final
cleanup receipts record provider configuration hashes, ordinary daemon process
generations, owned window/process absence and credential removal.
`final-local-audit.json` checks all 41 recorded owned window markers and 119
recorded process identities: none remain live. `final-snap-audit.json` confirms
all three final remote fixtures are stopped and credential-free. A filename
scan of the owned run directories found no remaining `auth.json` or
`.credentials.json` on either host. These audits perform no GUI actions.

Each native/window case checks that its baseline ordinary window IDs survive.
Ordinary work continued concurrently, so this is per-case preservation, not a
claim that the whole desktop was frozen. The final fixtures compare all three
ordinary provider configuration paths and declared ordinary daemon generations.
Claude's global state comparison permits the observed concurrent usage counters
`promptQueueUseCount` and `pluginUsage` to drift; it does not restore ordinary
state. An earlier exploratory Claude fixture observed those counters changing
and records that limitation separately.

The parked executable selection still resolves to
`0.14.0a1-8ba19a0eba87944a`. This run installs only into private candidate
prefixes and changes no managed selector or Mod+A binding. No commit, push,
publication or chezmoi apply was performed. The preserved legacy installation
and configuration remain the delivery rollback inputs. Any future selection
must retain the candidate artifact while direct windows still use its wrappers.

Before managed rollout, complete the separate human checklist:

- Judge real keyboard focus, search/caret movement, paste, scrolling and visual
  presentation in the picker and native clients.
- Select different real Claude agents in two windows and judge native behavior
  beyond the automated empty-manager draft/resize/lifetime proof.
- Review the immutable release, per-host declarations and desktop binding;
  then perform scoped managed selection and rollback verification if authorized.

Luna implemented bounded host, transport/action, UI/cache and proof-driver
slices. The primary owned architecture decisions, integration review, native
operations, failure diagnosis and final acceptance.
