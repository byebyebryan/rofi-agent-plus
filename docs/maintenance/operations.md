# Agent Plus managed operations

This guide describes the repository owner's chezmoi-managed fleet. The
selected immutable wheel, version, digest and hosts are recorded in
[managed-direct-selection.json](../../artifacts/managed-direct-selection.json).
Chezmoi owns installation, the four command links, `direct.toml` and Mod+A.
Snap, Starship and Carbon select this build. Carbon discovers its native default
without extra profile declarations; retired artifacts remain available for rollback.

The [0.15.0a16 release](https://github.com/byebyebryan/rofi-agent-plus/releases/tag/v0.15.0a16)
fixes Claude's native default-home configuration selection. Qualification covers
159 source tests, isolated installed-wheel byte/entrypoint checks and 22 host
tests against the installed package, including default/custom-home entry and
probe environments. Native desktop appearance/input acceptance remains separate.

The wheel is staged on each selected host under
`~/.local/share/rofi-agent-plus/artifacts/<wheel-sha256>/`. The managed
`18-install-agent-plus.sh` pre-hook verifies its checksum and installs it
offline into a version-and-digest prefix with Python 3.11. It verifies the
installed package against wheel bytes before selectors are applied. An existing
different/corrupt prefix is refused rather than overwritten. The host must have
`uv` and a locally available Python 3.11 interpreter.

The immutable wheel is distributed by the deployment owner; publishing a
documentation commit is not a deployment step. Keep the wheel and its checksum together. The package
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
Any disruptive GUI testing belongs on unattended Snap; Starship and Carbon are
reserved for the user's own trial. Human input/visual judgment remains separate from
package and passive inventory checks.

The Claude default-home environment fix changes the adapter revision. Refresh
and reselect an old Claude entry if it is rejected as stale. Existing native
clients and daemons retain their launch environment after a package update;
they are not restarted by deployment. A daemon started with the previous
`CLAUDE_CONFIG_DIR` may need a separately scheduled native restart, which ends
its background sessions. Preserve both Claude config files during rollout;
the launcher fix neither migrates nor deletes native settings.

Before applying, capture the selected links, direct configuration, active binds
and their source inputs in a private host-local rollback directory. Rollback
restores those exact inputs/targets and the previous selected prefix through a
scoped chezmoi apply. Preserve an immutable prefix while any direct TUI wrapper
still uses it. Changing picker selection never closes terminals or stops native
provider work.

The [development guide](development.md#native-proofs) documents the private
Xvfb input regression and the other native proof drivers. A documentation or
repository-metadata update does not alter the immutable selection. Preserve
the selected artifact while native wrappers still use it.
