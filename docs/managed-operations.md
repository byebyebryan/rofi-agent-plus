# Agent Plus managed operations

## Retained artifact and portable operations checks

[Managed native selection](../artifacts/managed-native-selection.json) extracts
the still-selected Plus wheel and its frozen pure Observer dependency from the
retired joint pilot. It covers Snap/Starship; Carbon retains the older archive
and script mapping declared by dotfiles, and is outside this native selection.
The manifest does not select the producer collector, reader, bridge or writer.
Provider versions and other picker entry hashes are historical diagnostics, not
current allowlists.

```sh
./scripts/check-installed-artifact --selection artifacts/managed-native-selection.json --host starship
./scripts/check-installed --self-test
./scripts/check-public-contract --self-test
```

The artifact gate is passive: it checks wheel digests, installed members,
interpreter entrypoints, dependency/SDK profile, public read/write schemas and
selected Plus links. Cache-v7 identity and contract guards remain in the owner
installed gate and synthetic tests. Use `--live` only for explicit installed
read/refresh evidence; it isolates cache/preferences and performs no provider
write. `--legacy-callbacks` is an explicit historical foreground-picker probe,
not proof that native conversation focus/Close or group viewers are supported.
Current unsupported bindings remain described in [suite integration](SUITE_INTEGRATION.md).

`scripts/managed-selection-rollback` accepts an explicit `--source` and new
private `--directory`; `capture`, `arm`, and `rollback` affect only the three
Plus selector templates/links and optionally captured UI state. Source/link/state
changes after arm, corrupt payloads and foreign/nonprivate backups reject
before restoration. It never operates on providers or producer selectors.
Old joint backups require their [immutable original procedure](https://github.com/byebyebryan/dotfiles/blob/5ff8b412a45b69cac64b8eb356444e6cf0705012/docs/agent-observer-pilot-operations.md)
and cannot be reinterpreted as new scoped snapshots.

Carbon's older foreground selection uses `scripts/check-installed --live --legacy-profile --legacy-callbacks`; the native manifest above does not apply there. Host-render and archive selection checks remain configuration-owned.
