# Agent Plus daemon and TUI design review

Date: 2026-10-21

Reviewed proposal: [Agent Plus daemon and TUI design](agent-plus-daemon-tui-design.md).
This is a source and design review; native implementation acceptance follows the
proofs listed below.

The direct-TUI model fits the intended helper: choose an environment, focus its
window or explicitly open another client. Individual window rows make each
client directly selectable. The design is ready for product review and focused
native proofs before implementation.

## Findings incorporated into the design

| Finding | Resolution |
| --- | --- |
| A daemon can have no conversations, and a custom native home may be invisible to ordinary discovery | Enumerate native environments/endpoints independently of conversations; accept host-local declarations and state the custom-home discovery limit |
| A daemon may not start until the first native manager opens | Discover installed environments before probing runtime status; preserve launcher rows with every daemon stopped |
| Native Codex profile files can be mistaken for separate daemon environments | Treat `--profile` files as home-local overlays; OSS/Work homes selected through `CODEX_HOME` remain separate targets |
| Codex's status probe emits JSON only when its endpoint is reachable | Normalize successful status and missing-endpoint/error paths separately; a nonzero exit alone cannot establish a stopped daemon |
| A profile name or executable name does not identify the owning daemon | Bind to the canonical host/provider/environment/endpoint; deduplicate aliases and keep runtime incarnation separate |
| A local window cannot prove remote connectivity or provider work state | Keep daemon facts and window presence separate; existing-window focus uses local evidence |
| Window titles and process/window IDs can change or be reused | Use a random instance ID with desktop, launch and process generations; titles and ordinals remain display labels |
| An exited TUI can leave a terminal or daemon descendant alive | Registration follows the foreground client; reconcile exits and never keep a TUI alive merely because a descendant remains |
| A slow first launch can produce duplicate windows | Reserve and recheck first launches under a target/desktop lock; replayed requests retain their instance ID |
| An unavailable compositor can be mistaken for no open windows | Preserve unknown presence and refuse actions until desktop validation recovers; expire reservations only on confirmed process/window absence |
| A row can disappear or a profile can change between selection and action | Revalidate the exact selected instance/specification; refresh instead of converting focus into launch or selecting another profile |
| An SSH timeout can occur after entry began | Pin the dispatch route and do not replay uncertain interactive entry |
| Passive SSH authentication can fail even when interactive login would work | Permit explicit terminal authentication, followed by host-helper validation before native client entry |
| An offline or removed target may still have a useful local terminal | Retain live window rows and permit exact local focus; independently refuse unvalidated new launches |
| Inheriting the picker's cwd or endpoint overrides can cross profile boundaries | Resolve native environment and host-local cwd deliberately at entry; test OSS/Work selection and effective provider configuration |
| The old session architecture could make the replacement unnecessarily large | Give the new path a small host helper and local window registry; remove Observer/tmux/session/batch dependencies from that path |

## Native dependencies before implementation

| Question | Required proof |
| --- | --- |
| Does the adapter select the installed Claude environment and preserve passive status behavior? | Enter the documented `claude agents` in the selected configuration scope and retain the independently checked running/stopped status behavior without supervisor startup during discovery |
| Can both providers support the intended concurrent TUIs? | Open two clients to one disposable target, select different conversations, resize and type drafts independently, then close one and verify the other and its work survive as intended |
| Can a host catalog distinguish all intended Codex environments? | Compare default OSS and dedicated Work, aliases, zero-conversation live daemons, stopped known environments and an explicitly declared custom home |
| Can Niri/Kitty focus be bound to the exact live client without global remote control? | Demonstrate marked dedicated windows, title changes, picker restart, process/window replacement, client exit and ambiguous-layout refusal |
| Does remote entry preserve environment and terminal behavior? | Use installed host helpers through Host Mesh routes, prove host/profile identity and native hooks, then test pre-entry failure and controlled SSH loss without replay |

The installed Codex help confirms the `agents` entry and per-environment daemon
management commands. Claude's [Agent View guide](https://code.claude.com/docs/en/agent-view)
establishes `claude agents` as its entry and `CLAUDE_CONFIG_DIR` as the supervisor
instance boundary. These are settled design inputs. Claude's absence from this
shell's PATH limits current native integration testing, rather than making its
command or namespace unknown. Installed routing, passive discovery and
concurrent-client behavior still require the integration proofs in this table.
The earlier installed Observer and Tmux acceptance records do not qualify this
direct-TUI path.

## Second review and implementation readiness

The second pass checked the full design against the source entry points,
Rofi script callbacks, dependency metadata, CI and installed/public check
scripts. The product model remains direct native managers with individual
window rows. The following implementation gaps are now resolved in the design
and [implementation plan](agent-plus-daemon-tui-implementation-plan.md).

| Finding | Decision |
| --- | --- |
| A target claimed to survive daemon restarts but entry also required the previously observed daemon incarnation | Key targets by host/user/provider/native home; attach to the current daemon in that same environment. Incarnation is diagnostic, while environment/specification validation remains required. |
| A fresh daemon-status failure could unnecessarily block native on-demand startup | Validate the environment and entry capability; unknown daemon status alone does not prevent explicit Open. |
| An event stream was proposed without a mechanism to redraw the existing script-mode Rofi | Reuse its one-second timeout for bounded local window/registration reads and preserved row/filter/action state. No new native plugin or persistent watcher is needed. |
| New configuration could make rollback to the parked release fail | Use new `direct.toml` and `direct-v1` state/registry paths; keep old configuration and artifacts separate. |
| A misspelled extra home could create a new unintended profile | Require declared extra homes to be prepared; ordinary default first-use setup remains native and explicit. |
| One broken registration could block healthy sibling targets | Scope registration, locks and faults per desktop/target; compositor loss remains a desktop-wide condition. |
| Request replay and intentional extra windows needed different identities | One accepted action keeps one request ID across callbacks; each deliberate additional action creates a fresh ID. |
| Detached launch requests could disappear with the dialog's temporary frame | Store requests in runtime state owned by their launch process/instance, independent of dialog cleanup. |
| Old package and CI gates still required Observer and tmux semantics | Include explicit dependency removal, active-test/tool replacement and a clean dependency-free artifact gate before release. |

The current `launcher.py` already implements first-frame reuse and initial
selection; `rofi.py` already has the one-second timeout callback and filter
preservation. Its conversation renderer is large and imports batch, Observer
and tmux lifecycle code. Reuse those small UI mechanisms while replacing the
model/renderer. `pyproject.toml` still pins Observer and CI downloads that wheel;
those remain required implementation changes, not completed cleanup.

This pass found no remaining product choice that prevents starting the bounded
mechanics proof. Native window/TTY behavior, independent-client behavior and
the final installed Rofi update behavior remain acceptance work, not unresolved
questions about the basic command or environment-discovery model.

## Unattended execution readiness

The [implementation plan](agent-plus-daemon-tui-implementation-plan.md) now
separates the unattended candidate from human input acceptance and managed
delivery. These additional decisions keep a long run bounded and resumable:

| Risk or ambiguity | Execution decision |
| --- | --- |
| Discovery language could imply every arbitrary custom home would be found | First implementation covers installed defaults and declared additional homes only; live undeclared-home adoption is deferred |
| Human keyboard judgment could block all implementation | Automate owned mechanical/input observations where possible and retain a separate final human checklist |
| Rofi's timer could disrupt search input | Permit a whole-view opening/Alt+R snapshot strategy with bounded inventory work and documented cold-opening latency; exact focus always uses current live identity |
| Remote proof could accidentally select the candidate for normal use | Install in a private candidate prefix on Snap, verify provenance and invoke that explicit path without changing the managed selection |
| Request IDs could be mistaken for transactional process launch | Persist intent and viewer acknowledgement; uncertain dispatch is reconciled without automatic replay |
| Old native fixtures could give misleading acceptance | Replace their pinned prior artifacts, tmux adapters and setup assumptions; separate real native/compositor results from controlled cases |
| A parent provider shell could route entry to another environment | Set the intended native home and clear conflicting routing overrides; the current Codex fleet wrappers explicitly clear `CODEX_PLUS_SOCKET` |
| A long run could lose progress or cleanup ownership | Persist a per-run gate/fixture manifest, scoped native cleanup and final source/artifact/native evidence |

The existing chezmoi Work/OSS launcher sources select a native home, clear
`CODEX_PLUS_SOCKET`, require the extra home's `config.toml` and execute the same
native Codex binary. The planned adapter can preserve that environment selection
without discovering or executing arbitrary shell wrappers. No chezmoi edits or
provider configuration changes are part of this planning pass.

Required candidate gates cannot become passed tests merely because a provider,
GUI or peer is unavailable. Independent implementation can continue; native
qualification remains incomplete where its required evidence is unavailable.
The confirmed unattended objective includes a tested candidate and owned
Starship/Snap proofs. Publication, managed selection and human-only acceptance
remain outside that objective.

## Discovery check on 2026 10 21

Installed command, native status and user-service checks were made on Starship
and on Snap through the current SSH Plus route. Separate temporary empty native
homes exercised missing-daemon status probes. Snap's remote logical identity was
verified through its own Host Mesh catalog; its operating-system hostname differs
from its logical host ID.

| Host | Detected environments | Current native state | Startup configuration |
| --- | --- | --- | --- |
| Starship | Default Codex home; Claude and a Work launcher were absent from the inspected command locations | Codex 0.163.0 running with the PID backend | Effective daemon auto-start enabled; no Codex/Claude user service found |
| Snap | Codex default OSS home, dedicated Work home and Claude | Both Codex 0.163.0 daemons running with the PID backend; Claude 2.1.306 supervisor running as transient/on-demand | Both Codex homes configure daemon auto-start; `codex-app-server.service` is disabled and inactive; installed Claude help says service installation is disabled |

Snap's enabled Codex Work report-server service hosts reports; it does not prove
that a provider daemon starts at boot. Transient Codex scopes are not enabled
boot services either. These are current installed observations, not a reboot or
first-launch experiment and not an exhaustive audit of custom system startup.

The default/Work homes contain native `.config.toml` overlays as well as their
base configurations. Those files are discoverable settings profiles, but their
existence does not identify additional daemon targets.

The inspected Claude `daemon status` query left the existing supervisor process
generation unchanged and reported multiple Agent View clients attached to it.
This supplies ordinary evidence of concurrent native managers. It does not
replace the independent-size/draft/exit checks.

| Isolated status probe | Observed missing-daemon result |
| --- | --- |
| Codex 0.163.0 on Starship and Snap, with auto-start enabled in the temporary home | Nonzero exit, no JSON and a missing-control-socket connection error; no runtime socket/PID entries created in that home |
| Claude 2.1.306 on Snap in a temporary configuration directory | Nonzero exit with `not running`, unreachable control socket and absent roster/log; no supervisor was started |

These probes establish that the current commands can check missing native
environments without auto-starting daemons. Their output still needs deliberate
adapter normalization: unrelated failures cannot become stopped-state evidence.
Temporary homes were removed after the probes. No ordinary daemon was started,
stopped or reconfigured for this check.

## Product defaults for review

| Topic | Proposed default | Reason |
| --- | --- | --- |
| Initial desktop support | Niri and Kitty | Matches the inspected terminal selection and bounds the first focus proof |
| Main list | All hosts, grouped by stable target order, with one row per TUI | Direct selection without another chooser or permanent header rows |
| Empty target | One launcher row, including a known stopped environment | The user can reach an environment after its daemon has stopped |
| Discovery order | Installed CLI, native environments, then existing daemon status | The first picker after a reboot remains useful before a TUI starts any daemon |
| Actions | Open and Open another window | Covers reuse and additional clients without importing conversation lifecycle |
| Closure | Normal terminal/window controls | Avoids a new picker close policy while native client-exit behavior is being established |
| Manual TUI adoption | Registered Agent Plus launches guaranteed initially | Arbitrary local process and remote-shell correlation needs separate proof |
| Mosh and Ghostty | Later independent adapters | Neither is required to validate the agreed direct-TUI model |

## Review result

The individual-row model and direct launch topology have no unresolved
structural conflict in this review. The native dependencies above are the
remaining prerequisites for accepting an implementation. Installed environment
routing, custom-environment coverage and client-exit semantics must remain
explicit until those proofs pass.

The next concrete step is Step 0 in the implementation plan: a bounded native
and dedicated-window proof, followed by the host helper, window lifecycle, SSH
integration, picker and separate artifact/delivery gates. This design review
does not enable Mod+A, deploy packages, launch provider clients or alter running
daemons.
