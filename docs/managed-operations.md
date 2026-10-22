# Agent Plus managed operations

The direct-TUI trial selects `0.15.0a2` on Starship and Snap. Its immutable
wheel and selected hosts are recorded in
[managed-direct-selection.json](../artifacts/managed-direct-selection.json).
Chezmoi owns installation, the four command links, `direct.toml` and Mod+A.
Carbon retains its historical artifact and has no Agent Plus desktop binding.

The wheel is staged on each selected host under
`~/.local/share/rofi-agent-plus/artifacts/<wheel-sha256>/`. The managed
`18-install-agent-plus.sh` pre-hook verifies its checksum and installs it
offline into a version-and-digest prefix with Python 3.11. It verifies the
installed package against wheel bytes before selectors are applied. An existing
different/corrupt prefix is refused rather than overwritten. The host must have
`uv` and a locally available Python 3.11 interpreter.

The trial artifact is distributed locally; no GitHub push or publication is a
deployment prerequisite. Keep the wheel and its checksum together. The package
has no Observer or Tmux dependency. Its remote headless helper must be reachable
as `rofi-agent-plus-host` in the owning user's noninteractive PATH.

Review scoped `chezmoi status`, `chezmoi diff` and
`chezmoi apply --dry-run --verbose` before applying the installer, four links,
Rofi script link, direct configuration and active Niri binds. Use `umask 022`
and preserve unrelated drift. Native settings, authentication, daemons and
existing clients need no restart. Niri reloads its configuration in place.

After installation, use the source-owned artifact verifier with the exact
managed prefix and wheel:

```sh
scripts/check-installed-artifact --candidate-prefix "$PREFIX" \
  --wheel "$WHEEL" --output "$PRIVATE_RECEIPT_DIR"
"$PREFIX/bin/rofi-agent-plus-host" inventory --json
```

Check local and SSH inventory, canonical Default/Work homes, the four selected
entrypoints and Mod+A independently. Passive checks open no native manager.
Any disruptive GUI testing belongs on unattended Snap; Starship is reserved
for the user's own trial. Human input/visual judgment remains separate from
package and passive inventory checks.

Before applying, capture the selected links, direct configuration, active binds
and their source inputs in a private host-local rollback directory. Rollback
restores those exact inputs/targets and the previous selected prefix through a
scoped chezmoi apply. Preserve an immutable prefix while any direct TUI wrapper
still uses it. Changing picker selection never closes terminals or stops native
provider work.

The older [managed-native-selection.json](../artifacts/managed-native-selection.json)
and three-selector `managed-selection-rollback` tool describe the retired
Observer/Tmux artifact only. They do not qualify or fully roll back this direct
trial's additional helper/configuration/binding targets. The
[candidate execution record](direct-tui-candidate-20261021.md) preserves its
source, wheel and native evidence boundaries.
