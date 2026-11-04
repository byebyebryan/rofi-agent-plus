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

[desktop]
motion = "inherit"

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

`desktop.motion` controls animations for newly opened native windows on this
display, including windows whose Codex or Claude target is remote:

| Value | Behavior |
| --- | --- |
| `inherit` | Keep each provider's configured motion setting. This is the default. |
| `normal` | Request normal Codex and Claude TUI animations for this window. |
| `reduced` | Request reduced motion in Codex and Claude for this window. |

Open/focus and request replay keep the existing window's captured setting. A
deliberate New window uses the current `desktop.motion` value. The preference
is passed as a per-invocation native CLI override; it does not update provider
settings, authentication, billing, conversation homes, or target identity.

Claude Agent View and newly dispatched sessions receive the captured setting.
When you attach to an existing Claude background session, that session retains
the motion setting from its own launch; the Agent View window's setting does
not override it.

For Claude's native default home, Agent Plus clears `CLAUDE_CONFIG_DIR` so
Claude uses `$HOME/.claude.json` for workspace trust and user MCP settings.
Setting the variable even to `$HOME/.claude` selects a different global config,
`$HOME/.claude/.claude.json`. Custom Claude homes retain an explicit
`CLAUDE_CONFIG_DIR`. Probes and native entry use the same environment policy;
it follows the child's absolute `HOME`, with explicit selection when that
default cannot be established.

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
