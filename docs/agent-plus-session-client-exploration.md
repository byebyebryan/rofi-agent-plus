# Agent Plus session client exploration

Date: 2026-09-25

Status: product exploration, before implementation planning. The operator is
**leaning slightly toward a dedicated client**: building it sounds enjoyable,
and owning the client would give maximum control over the exact workflow and
interaction. Herdr remains a credible alternative and reference. This is a
preference, not a final architecture or implementation decision.

This note captures the discussion about Herdr, an Agent Plus integration, and
a dedicated session client. The [current status ledger](https://github.com/byebyebryan/dotfiles/blob/main/docs/rofi-plus-status.md)
continues to describe the deployed product. No runtime, public contract,
dependency, or deployment change follows from this note. Carbon appears below
as a workflow example, not as a newly accepted deployment target.
The [first feasibility spike](agent-plus-session-client-spike-results.md)
tested terminal presentation and reconnection. Its
[direct-attachment follow-up](agent-plus-session-client-direct-attach-results.md)
confirmed the direct transport path but stopped that presentation direction:
the operator requires an always-visible session list during agent work.
WSNav's always-visible Navigator remains the leading UI reference. The
[ordinary-tmux presentation spike](agent-plus-session-client-ordinary-tmux-results.md)
proved that its two-tmux layout can switch among existing local and remote
sessions. Subsequent operator feedback says the irregular flicker already
experienced in WSNav's nested native TUI is very annoying. WSNav's own V1
record treated that artifact as nonblocking polish, but it is not acceptable
as a default assumption for this new client. Borrow the rail design and
suitable UI code; study a way to keep it visible without another tmux renderer
in front of the agent. WSNav's host-local Workstream and Runtime lifecycle
remain outside the intended client.

The later [one-tmux Ghostty result](agent-plus-session-client-ghostty-split-results.md)
accepted the 32-column rail and found no wide-screen flicker, but left
supported external split/focus/width control unproven. The
[Kitty follow-up](agent-plus-session-client-kitty-results.md) exercised that
control boundary with Starship as the viewing endpoint. Its startup split,
targeted focus and width correction, native Codex streaming, local switching,
and Starship-to-Snap reconnect worked at a realistic window size; the operator
reported no visible flicker. A stale-selection test exposed a supervisor
ordering requirement: validate the new target before detaching the current
viewer. This remains a feasibility result, not a client implementation.

## Starting point and workflow pressure

Agent Plus is the product the operator uses daily. Its value is discovering
native agent conversations and making it easy to return to paused work or
reach work from another endpoint. Keeping the native agent TUI intact, with
little setup or management overhead, is central to that success.

The current Resume and New session here actions remain useful. Starting a
completely new project is infrequent enough to do outside the picker. Starting
another conversation in an existing session's directory is useful; the
existing conversation remains untouched. See the
[action cycle record](https://github.com/byebyebryan/dotfiles/blob/main/docs/rofi-plus-action-cycle-plan.md).

The next pressure is the presentation model of one session per terminal
window:

- At work, use five Claude sessions on Snap and four Codex sessions on
  Starship. At home, reopen all nine from Starship.
- On Carbon, view four sessions from each host. Sleep interrupts SSH; resuming
  work currently means dealing with broken connections and reopening windows.
- Several independent conversations may belong to the same project directory.
  The desired navigation is an **always-visible** flat session/card list, with
  project and host as context on each card.
- Start conversation A, move on to B, then return to A later, even after A's
  process or terminal has been retired.

Niri and an ultrawide monitor make multiple windows easier to manage, but the
underlying issue is window-per-session presentation. The desired experience
should also work on a small screen and with other window managers.

Sticky picker scope/selection, bulk reopening, and saved groups were earlier
possibilities. On 2026-09-30, the operator selected two small picker
improvements: a global Active page in the existing Left/Right ring, and
remembering the last page and successfully used conversation. The
[picker navigation plan](agent-plus-picker-navigation-plan.md) records that
direction and replaces the initial Active-only toggle proposal. Implementation
and deployment are pending. Bulk reopening and saved groups remain separate
possibilities. The discussion moved to a terminal client because it could
address window management and reconnection together. Named workspaces and
their UI complexity remain open.

## The primary object is the agent conversation

The strongest reason for considering a dedicated client is continuity of
identity across discovery, navigation, and reopening. The user wants to return
to the same conversation through this sequence:

**running -> stopped -> resumed -> accessed from another endpoint**

A terminal can host different conversations over time. A directory can contain
many conversations. Neither terminal identity nor directory identity alone
identifies the work the user wants to continue.

| Concept | Meaning and ownership |
| --- | --- |
| Native conversation history | Provider-owned saved conversations; the client need not copy transcripts or invent another history store. |
| Session catalog | Agent Plus discovers native identities, titles, directories, hosts, and available runtime evidence. |
| Running set | Conversations with a live provider process, including agents idle at their prompts. |
| Current working set | Conversations chosen for this view or work period; potentially a subset of the running set, and potentially containing stopped conversations. |
| Visible attachment | The terminal connection currently used to interact with a conversation. |

Two UI layers can still implement these concepts. Separating them does not
settle how many components, databases, or protocols to build.

The desired Open behavior is to focus/attach when the exact conversation is
already running and otherwise resume its native saved session. Unreachable or
ambiguous runtime evidence is not proof that a conversation is stopped.
Opening must account for duplicates and concurrent access from other clients.

Endpoint switching in the examples means reaching work on its existing host.
Migration of processes, project files, credentials, or provider histories to a
different execution host has not been requested.

## Herdr: current evidence and limitations

This assessment uses upstream documentation reviewed on 2026-09-25, with
Herdr 0.9.1 as the release reference. It is not hands-on acceptance or an
audit of every implementation path. Links to the documentation track upstream
and can change.

The earlier
[WSNav Herdr 0.8 study](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/studies/0004-herdr-v0.8-comparison.md)
is historical. In particular, its single-remote-client assessment is superseded:
Herdr 0.9.0 added a combined local/remote experience. See the
[0.9.1 release changelog](https://github.com/herdrdev/herdr/blob/v0.9.1/CHANGELOG.md).

### Runtime and remoting

Herdr's server owns terminal PTYs and processes; agents run their native TUIs
inside those terminals. Workspaces contain tabs, which contain panes. Clients
render and interact with the server's terminals. Separate clients can select
different tabs, while the underlying panes remain server-owned.
[Concepts](https://herdr.dev/docs/concepts/).

Remote access uses OpenSSH configuration and authentication. A local Herdr
client exchanges terminal content and session state with a remote Herdr server
over SSH. Both ends need Herdr. Ordinary SSH followed by running Herdr on the
remote host is another supported path.
[Remote access](https://herdr.dev/docs/persistence-remote/).

Saved machines provide a combined agent list and independent reconnection
after sleep or network interruption. Each profile targets one remote Herdr
session namespace, rather than discovering every Herdr namespace on that
machine. Losing one connection leaves the others usable. This directly
addresses the mixed-host and laptop-sleep scenarios for Herdr-hosted terminals.
[Connecting machines](https://herdr.dev/docs/connecting-machines/).

### Idle terminals, stopped conversations, and history

| Situation | Documented behavior or evidence limit |
| --- | --- |
| Agent waits for input, or user switches away | Terminal and agent continue running. |
| Client detaches or SSH disconnects | Server retains the running terminals. |
| Agent exits back to its shell | Shell pane remains; the live agent name is cleared on exit/replacement. |
| Server restarts | Layout can return; eligible panes resume recorded native conversations through official integrations. |
| User closes a pane or replaces A with B | A unified history browser for reopening previous conversations has not been established. |

Herdr also offers optional saved terminal-screen history. That preserves
terminal output. Native conversation recovery instead depends on recorded
session references; the documented automatic recovery follows saved panes.
A built-in park/unpark flow that stops an agent while retaining a browsable
conversation card has not been established.
[Session restore](https://herdr.dev/docs/session-state/),
[agent lifecycle](https://herdr.dev/docs/cli-reference/#agents).

This is a limit of the evaluated built-in workflow, not proof that no plugin or
future version can provide conversation history. Herdr retains more than
currently busy processes: idle terminals, persistent layout, and eligible
restart recovery all matter.

### Organization is more flexible than the initial example

One project workspace with five agent tabs is a possible arrangement. Herdr
also exposes individual entries in an Agents sidebar, configurable row
content, direct agent navigation, and ordering by spaces or priority. Its
Agents sidebar should be evaluated before concluding that project-first
navigation is unavoidable.
[Sidebar configuration](https://herdr.dev/docs/config-reference/#ui-and-sidebar).

One workspace per conversation with a single terminal and hidden single-tab
bar is another possible arrangement to test. Neither arrangement has been
visually accepted for this workflow. Exact native titles, stable ordering,
five conversations in one directory, and mixed-host navigation need checking.

## Option 1: use Herdr by itself

For conversations launched inside Herdr, it could cover the primary running
workflow: persistent terminals, multiple agents in one window, access from
other endpoints, and reconnect handling. This option deserves consideration
before building an integration.

The remaining concern is returning to conversations after their terminal is
gone, including conversations originally started elsewhere. Provider-native
resume tools remain available inside terminals, but equivalent unified history
discovery has not been demonstrated. Keeping every old conversation alive in
a tab would accumulate the same kind of working-set clutter.

Adoption would also change the runtime used for those launches. Herdr does not
automatically import Agent Plus's existing tmux-backed running set: its docs
explicitly say agent detection does not inspect tmux sessions launched inside
a Herdr pane. Running a tmux attachment there is a separate integration path.
[Agent detection](https://herdr.dev/docs/agents/#detection-manifests).

## Option 2: Agent Plus plus Herdr

The proposed split is:

- Agent Plus provides the native conversation catalog and is mainly used to
  add or recall conversations into Herdr.
- Herdr provides runtime, presentation, navigation, and remote connectivity
  for the open conversations.
- Providers continue to own the saved history and native interaction.

Selecting an already open conversation should focus its exact existing pane.
Selecting a stopped conversation should resume it in an appropriate pane.
Retiring its runtime should leave the saved conversation discoverable.

Herdr exposes agent focus/start operations and native session references when
integrations report them. These are useful integration primitives, not proof
of a complete Agent Plus adapter. Starting an agent requires an available
shell pane. Exact conversation-to-pane identity, remote targeting, focus on
the intended client, and reconnect behavior remain untested.
[CLI](https://herdr.dev/docs/cli-reference/#agents),
[session references](https://herdr.dev/docs/socket-api/#agent-state-reporting).

The friction discussed was explicit cleanup in Herdr followed by reopening
from Agent Plus. Exiting an agent may leave a shell pane to clean up. History
and current work would also be navigated in different interfaces. An in-Herdr
history picker backed by the same discovery could reduce that separation,
but remains an idea rather than a verified integration.

Two runtime arrangements need separate evaluation: launching agents directly
in Herdr, or using Herdr panes as viewers of existing tmux runtimes. Closing a
viewer and ending the underlying agent have different effects. Nested tmux
detection and terminal fidelity cannot be assumed to match direct hosting.

## Option 3: a dedicated session client

The initial working name was **wsnav-plus**. The name, repository, language,
and terminal implementation are all undecided.

The proposed product is a minimal session navigator with one native terminal
surface. Each card represents a native agent conversation. Five conversations
in the same folder are five peer cards; project, provider, and host are useful
context. History and the working set can be views over the same identities.

The original proposal was to reuse Agent Plus discovery and attach to the same
tmux sessions Agent Plus already opens. Selecting a conversation in the Rofi
picker could open/focus it in this client. Discovery might also be available
within the client so Rofi and a particular desktop are optional entry points.
These are integration directions, not an accepted public API.

The intended responsibility split is:

| Responsibility | Proposed owner |
| --- | --- |
| Native conversation persistence and agent interaction | Provider |
| Discovery, provider identity, runtime correlation, native resume selection | Agent Plus capabilities |
| Host identity and SSH routing | Existing suite host services |
| Persistent agent processes | Existing runtime infrastructure, initially considering tmux |
| Flat cards, chosen working set, selection, visible attachment | Dedicated client |

The client would coordinate existing open/resume capabilities. Owning a new
provider orchestration engine, conversation lineage store, or terminal
emulator is not part of the established need. WSNav supplies useful card UI,
private presentation, and tmux attachment patterns. Its current host-local
Workstream catalog and runtime lifecycle should not be inherited wholesale;
they solve a different product boundary. See the
[current WSNav model at the inspected head](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/README.md#host-local-by-design).
The concrete reuse target is the always-visible Navigator pane: its layout,
card rendering, selection, focus cues, and navigation behavior, plus any
decoupled code and tests that support them. Session identity and state would
come from Agent Plus capabilities, not WSNav Workstreams.

A dedicated client still carries substantial work: attachment/reconnection,
exact identity, stale or ambiguous evidence, client ownership, and terminal
input/rendering fidelity. Reusing tmux does not settle native keyboard,
clipboard, mouse, resizing, or nested-rendering behavior. One visible
attachment switched on demand was discussed; keeping multiple attachment
clients alive is still an implementation choice.

Existing provider discovery must remain a single maintained capability. The
current [P9 boundary](https://github.com/byebyebryan/dotfiles/blob/main/docs/rofi-plus-p9-cli-contracts.md#agent-plus) deliberately
excludes publishing provider discovery or the private Rofi cache as a consumer
contract. A new client requires an explicit contract design; it must not
silently depend on private caches, cross-package Python imports, or copied
provider detectors. See also
[Agent Plus ownership](https://github.com/byebyebryan/rofi-agent-plus/blob/4a45282f40c2c4a4d7db9af60962a13c506af9ca/docs/SUITE_INTEGRATION.md#agent-plus-retains).

## Dedicated-client feasibility: what the existing code proves

Source review on 2026-09-25 used WSNav `42ce138`, Agent Plus `4a45282`, and
Tmux Plus `e20d870`. This is a code and recorded-evidence study. It did not
run a new client, attach to ordinary user sessions, exercise live providers, or
change public contracts. WSNav's final D26 artifact passed its source,
integration, CI, and byte-identical installation gates. Its recorded live
Codex/OpenCode acceptance belongs to a pre-review candidate; live acceptance
of the final artifact remains open. See the [D26 acceptance record](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/acceptance/d26-post-reattach-exit.md).

| Need for this client | Existing evidence | Remaining work |
| --- | --- | --- |
| Flat, stable session cards and selection | WSNav's pure navigator model renders selectable cards, preserves a selected ID across snapshot replacement, and separates card selection from terminal focus. | Replace Workstream IDs, project grouping, lifecycle states, and provisional-shell rows with native conversation keys `(hostId, provider, session id)` and the small set of states this product needs. |
| One visible native TUI with fast switching | WSNav's private presentation has a navigator pane and one provider pane; exact attach, focus, resize, detach/reattach, and owned-topology recovery have deterministic tests. | Attach that pane to existing ordinary local or remote tmux sessions rather than WSNav's private Runtime. The current attachment helper requires WSNav Workstream/Runtime IDs and revisions. |
| Discover and reopen saved conversations | Agent Plus discovers Codex, Claude, and OpenCode native histories on the configured hosts, correlates process/tmux evidence, and selects provider-native resume commands. Its Resume workflow is in daily use on Snap and Starship. | Publish a bounded, versioned consumer contract for discovery and an operation that resolves/starts an exact conversation **without opening an OS terminal**. Its current `list` is diagnostic JSON from a private cache, and Resume delegates to Tmux Plus `open`. |
| Identify a live tmux runtime safely | Tmux Session v1 inventories local/remote sessions and revalidates `(hostId, serverGeneration, sessionId, createdAt)` with optional provider-option guards. | Give the client a validated attach-in-caller route or equivalent handle. `open` currently focuses a Niri window or launches a separate terminal; `attachedClients` is a count, not proof of which conversation an individual client displays. |
| Reach another host and recover | Host Mesh supplies host identity/SSH policy; Tmux Plus already operates against remote default tmux servers. WSNav's isolated [transport spike](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/spikes/0001-tmux-remote-transport.md) passed input, resize, process survival, and reattachment through local tmux, SSH, and remote tmux. A [native Codex follow-up](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/spikes/0002-codex-native-tui.md) passed on an isolated older CLI. | Resolve the approved SSH route into a viewer attachment, detect a dropped connection, reconnect the exact remote tmux target, and distinguish network failure from a stopped provider. WSNav's current catalog and control plane are host-local. |
| Choose a small daily working set | WSNav maintains current view/selection for its own Workstreams. Agent Plus provides a recency-sorted picker. | Define add/remove-from-view and restart behavior over provider identities, without turning view removal into provider stop or native-history deletion. No current component provides this working-set contract. |

The reusable WSNav material is mainly **patterns and tests**, rather than a
module that can be imported intact. Its [navigator model](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/src/navigator/view.rs),
[presentation topology](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/src/presentation/mod.rs),
[attachment handoff](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/src/presentation/attachment.rs),
terminal capability settings, and disposable presentation tests give us a
starting design and regression cases. Their implementation is coupled to
WSNav-owned private runtimes, schema/revisions, provider observation, and
Workstream lifecycle. Porting those assumptions would enlarge this client
without serving its session-history workflow.

### The hard seams

1. **Discovery contract.** Agent Plus's current `list --limit` refreshes and
   prints a private snapshot. The default limit is 40 and the accepted maximum
   is 100. A history browser cannot silently treat this bounded recent list
   as complete history. Define stable IDs, paging/search or an explicit bound,
   per-host freshness/error semantics, and snapshot versioning before a client
   consumes it. Keep the existing P9 CLI boundary intact until a deliberate
   follow-on contract is published.
2. **Open without a new window.** Agent Plus currently revalidates the native
   conversation, then asks Tmux Plus to focus/launch a terminal or to create a
   deferred provider command and open it. The new operation must return an
   exact runtime attachment target after revalidation, start a stopped
   conversation at most once, and handle the deferred first attachment. A
   `terminalLaunched` result only proves a process was spawned, not that SSH
   or tmux attachment completed. See [Tmux Session v1](https://github.com/byebyebryan/rofi-tmux-plus/blob/e20d870c03a806d02f722a645473875818ed124b/docs/TMUX_SESSION_V1.md).
3. **Conversation identity while a TUI stays open.** A provider can switch to
   another native conversation inside one terminal. Tmux session identity
   alone does not prove the current conversation. WSNav's current design even
   treats Codex native `/new` as unsupported for exact managed binding; it
   cannot infer the new thread safely. The client needs a stated policy for
   uncertain identity, duplicate provider processes, and access from another
   endpoint before it claims one card always names the visible TUI. See
   [WSNav's native-thread boundary](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/design.md#native-codex-thread-management).
4. **Terminal fidelity and connectivity.** A private navigator tmux attaching
   to an existing tmux runtime is a nested renderer. WSNav's [A/B spike](https://github.com/byebyebryan/wsnav/blob/42ce13841f027e4754ec04e7819ca0480430257f/docs/evidence/spikes/0014-terminal-fidelity-a-b.md)
   measured cursor-motion amplification around 2.4-2.6x on tmux 3.7b. That
   does not settle current behavior, but warrants an early comparison with a
   single-tmux or another presentation topology. Remote sleep/reconnect,
   mouse, modified keys, copy/paste, resize, and all three native TUIs need
   client-specific acceptance.

### From study to proof

The [proposed spike](agent-plus-session-client-spike.md) uses disposable tmux
sessions to study switching, detach/reconnect, terminal behavior, and the
usefulness of a flat card view. It deliberately leaves public contract and
client implementation planning until after the result. Synthetic sessions
cannot prove that an inventory route and session ID are safe to attach to
later, or that a tmux runtime still contains the named native conversation.

## Unsettled interaction choices

- **Starting work:** the operator expects Agent Plus could be the day's entry
  point, choosing conversations into an empty client. Whether to start fresh
  or restore the previous selection remains open. Recovery after sleep and
  endpoint changes should also be considered independently of deliberate
  fresh starts.
- **Working-set scope:** all discovered sessions, all running agents, or an
  explicitly chosen subset? Is selection local to an endpoint, portable across
  endpoints, or saved in named groups? No synchronization model is chosen.
- **Close semantics:** distinguish removing a card from a view, detaching a
  client, stopping a provider process, and deleting native history. Their
  commands, defaults, and ownership are undecided. Earlier discussion deferred
  Agent Plus Park/Pause and Kill/Delete actions.
- **Identity changes inside the native TUI:** A and B remain distinct
  conversations even if the same terminal hosts both. Exact detection of
  native new/resume transitions and behavior under uncertain evidence need
  investigation; labels and directory matches are insufficient identity.
- **Native experience:** preserve the provider's own commands, naming,
  interaction, and terminal behavior. Keeping session navigation useful must
  not require adopting a new agent workflow.

## Current leaning and next evidence

The operator currently prefers exploring a dedicated client slightly more
than adopting or integrating Herdr. Enjoyment of the project and maximum
control over the final experience are explicit reasons. The product argument
is the mismatch in primary object: Herdr organizes terminal runtimes, while
this workflow returns to agent conversations across runtime lifetimes.

That preference does not reject Herdr or select an implementation stack. The
two-tmux presentation worked functionally but is not the preferred client
topology after the operator reported annoying WSNav flicker. A comparison or
later prototype should answer the same concrete scenarios:

1. Five peer conversation cards in one directory, with recognizable titles
   and predictable selection/order.
2. A -> B -> A with A both still running and fully stopped; reopening the
   exact conversation and avoiding duplicate launches.
3. Mixed Snap/Starship work viewed from another endpoint, including sleep,
   failed connections, and recovery without rebuilding windows individually.
4. Opening, removing from view, and retiring work with clear effects on
   provider processes and saved conversation history.
5. Native TUI behavior on a small screen, including modified keys, clipboard,
   scrolling, mouse input, resizing, and focus.
6. The amount of setup and bookkeeping required at the start and end of work.

The presentation studies led to the
[completed Kitty control spike](agent-plus-session-client-kitty-results.md).
Ghostty showed that the one-tmux rail can feel good in a wide window. Kitty
added targeted startup, focus, and width control; the Starship replay then
passed native streaming and switching without visible flicker. It also
reconnected an exact Snap fixture after a controlled SSH cut and an injected
route failure. A later design must validate a target before detaching the
current viewer and must restore the 32-column rail after geometry changes.
Architecture decisions, hands-on Herdr acceptance, reverse-direction remote
validation, real sleep/wake recovery, and implementation scope remain open.
