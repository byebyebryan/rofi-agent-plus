# Previous Agent Plus implementation

The direct environment/window picker replaced the conversation picker, Observer
write client and Tmux viewer runtime. The retirement removed these unused
supporting files:

- The Tmux Session contract bundle and old consumer/Observer fixture corpora.
- The unused Observer fixture helper.
- The three-selector rollback tool, its tests and the old native selection
  manifest. That tool did not cover the direct trial's helper, configuration or
  binding targets.
- Earlier conversation-picker plans, client experiments, Observer integration
  records and their historical evidence files.

The retained public contract is Host Mesh v1. Current direct fixtures, native
proof drivers, design records and candidate receipts remain in the source tree.

## Retrieve historical records

The complete pre-cleanup tree is preserved in Git at
`3caa6120fa42715206c1708b63f90ddbb484e334`. For example:

```sh
git show 3caa6120fa42715206c1708b63f90ddbb484e334:docs/native-operational-proof.md
git show 3caa6120fa42715206c1708b63f90ddbb484e334:artifacts/managed-native-selection.json
git ls-tree -r --name-only 3caa6120fa42715206c1708b63f90ddbb484e334 -- contracts/tmux-session-v1
```

These are historical implementation/evidence records, not acceptance proof or
rollback instructions for the current direct picker. Current rollback captures
all owned direct targets as described in [managed operations](managed-operations.md).

## Managed installations

Snap, Starship and Carbon use the immutable direct wheel selected by
[managed-direct-selection.json](../artifacts/managed-direct-selection.json).
Chezmoi no longer declares the old picker archive or manages its legacy
`config.toml` on those hosts. Existing archived trees, versioned prefixes,
deployment preimages and running native clients are retained. Carbon now selects
the same direct helper, configuration and Mod+A binding.

The October 2026 retirement changed source support files and management policy.
It preserved installed direct package bytes and existing native clients.
The [history index](history/README.md) separates the original transition plans
and candidate evidence from the current guides.
