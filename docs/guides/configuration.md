# Environments and remote hosts

## Configure environments

Configuration is optional. Installed providers contribute the owning account's
`~/.codex` and `~/.claude` defaults even when their daemons are stopped. A provider
that is neither installed nor declared contributes no row or missing-provider
warning. Discovery never starts daemons; explicit native TUI entry may start
one according to the provider's configuration.

To label a default or add a prepared home, create
`~/.config/rofi-agent-plus/direct.toml` (or use `XDG_CONFIG_HOME`):

```toml
schema_version = 1

[[environments]]
provider = "codex"
home = "~/.codex"
label = "Default"

[[environments]]
provider = "codex"
home = "~/.codex-work"
label = "Work"
```

Extra Codex homes must already have a readable `config.toml`; extra Claude
configuration directories must already be usable. These rows identify native
homes, rather than Codex `--profile` overlays sharing the same home. Declare
remote homes on their owning host. See the [full example](../../examples/direct.toml).

## Remote prerequisites

Install/configure SSH Plus and expose its public Host Mesh command on the
launching desktop's PATH. Install the Agent Plus headless helper on each
participating remote host's noninteractive PATH. Declare additional native homes
on their owning host. Each remote TUI runs on its daemon's host.

Logical host catalogs are endpoint-specific and directional. Cached discovery
or a retained window never grants a new route. Agent Plus uses Host Mesh for
entry routing, independently of Observer/Tmux state delivery.

[Architecture](../reference/architecture.md) defines environment and window identity;
[managed operations](../maintenance/operations.md) describes the owner's separately
pinned installation. A config/example edit does not change deployed pins.
