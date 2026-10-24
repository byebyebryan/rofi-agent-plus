# Agent Plus

Find local and remote Codex/Claude environments, then open or focus their native
TUIs. Agent Plus owns discovery and its launched windows; native managers own
conversations and agent work.

[Project showcase](https://dev.byebyebryan.com/fleet-plus/projects/agent-plus/) ·
[Get started](docs/getting-started.md) · [Documentation](docs/README.md) · [Usage](docs/guides/usage.md)

[![CI](https://github.com/byebyebryan/rofi-agent-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/byebyebryan/rofi-agent-plus/actions/workflows/ci.yml)

![Agent Plus with local/remote environments and native windows](docs/assets/picker.png)

*Actual Rofi rendering with sample data and a preview theme; no live sessions.*

## Install and open

Requires Linux with Niri, dedicated Kitty windows, Python 3.11+, Rofi 2.0+ and
provider CLIs supporting `codex agents` or `claude agents`. The package has no
runtime Python dependencies. Remote use needs SSH Plus/OpenSSH and the Agent
Plus host helper on each remote host's noninteractive PATH.

```sh
git clone https://github.com/byebyebryan/rofi-agent-plus.git
cd rofi-agent-plus
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rofi-agent-plus-rofi
```

Run from your Niri session. An optional binding inside Niri's `binds` block:

```kdl
Mod+A { spawn "/absolute/path/to/venv/bin/rofi-agent-plus-rofi"; }
```

Installation adds no key binding and starts no provider daemon. Passive discovery
does not start one; explicit native entry may do so under provider configuration.

## Use and configure

| Key | Action |
| --- | --- |
| Up / Down | Select an environment or registered window |
| Enter | Open/focus, or run the selected New window action |
| Tab / Shift+Tab | Cycle Open / New window |
| Alt+R | Refresh inventory |
| Escape / Ctrl+G | Close the picker |

Installed provider defaults appear automatically; optional configuration labels
them or adds prepared native homes such as Codex Work. **New window** opens an
independent client, without creating a conversation. Arbitrary manually launched
TUIs are outside the registry. See [usage](docs/guides/usage.md) and
[environment/remote configuration](docs/guides/configuration.md).

Agent Plus uses Host Mesh for routing and has no Observer/Tmux runtime dependency.
Claude native entry here is independent of Observer's retired Claude monitoring.
Other desktops/terminals, Mosh and picker-driven Close remain deferred.

## Develop and operate

Use [development](docs/maintenance/development.md) for the source gate, isolated native proofs
and preview renderer. [Architecture](docs/reference/architecture.md) defines discovery,
identity and window ownership; [managed operations](docs/maintenance/operations.md)
records separately pinned deployment/rollback.

Current guides are organized in the [documentation index](docs/README.md).
