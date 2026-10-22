# Suite ownership

Agent Plus owns the environment picker and its dedicated native-TUI windows.
It consumes SSH Plus through the public Host Mesh CLI, and enters provider
managers through its own headless host helper.

| Responsibility | Owner |
| --- | --- |
| Logical host IDs, aliases, routes and SSH connection policy | [SSH Plus / Host Mesh](https://github.com/byebyebryan/rofi-ssh-plus) |
| Installed environments, labels, capabilities and passive daemon observations | Agent Plus host helper and provider adapters |
| Local picker, catalog cache, launch requests, registration and exact focus | Agent Plus desktop runtime |
| Conversations, agent work, settings, authentication and daemon startup | Codex and Claude |
| Window surfaces and foreground terminal clients | Niri and dedicated Kitty |
| Immutable installation, command links, configuration and key bindings | The deployment owner; chezmoi in the managed fleet |

## Integration contracts

- Host discovery uses `rofi-ssh-plus mesh ... --json` and the vendored
  [Host Mesh v1 contract](../contracts/host-mesh-v1/contract.md). Agent Plus does
  not import sibling implementation modules or read their private state.
- Remote discovery and entry invoke fixed `rofi-agent-plus-host` operations.
  Helpers must be installed on their owning hosts. Inventory never supplies
  executable commands.
- Entry revalidates the logical host, user, canonical native home and launch
  specification before executing `codex agents` or `claude agents` in a real
  TTY. Native settings and authentication stay provider-owned.
- Focus validates the exact local process generation, compositor window and
  private Kitty layout. It requires no SSH or provider status probe.

Agent Plus has no Agent Observer or Tmux runtime dependency. It does not read
conversation databases, supervise agent work, adopt arbitrary manual TUIs,
or stop provider daemons. A local terminal proves window presence, rather than
successful remote login or daemon connectivity.

## Acceptance and change ownership

Source tests, installed bytes, native terminal/input behavior, human visual
judgment and managed deployment establish different things. The
[development guide](development.md) maps the checks to their scope; the
[operations guide](managed-operations.md) covers selection and rollback.

The [architecture](architecture.md) is the current behavioral contract. The
[transition history](history/README.md) records earlier proof decisions;
[legacy retirement](legacy-retirement.md) identifies retired responsibilities
and their recovery checkpoint. Preserve native work and unrelated deployment
changes when updating this component.
