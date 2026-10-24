# Getting started

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

Use [configuration](guides/configuration.md) to label defaults, add prepared
homes or enable remote hosts, and [usage](guides/usage.md) for exact window
actions. Native managers own conversations; New window opens an independent
TUI without creating a conversation. No Observer/Tmux runtime is required.
