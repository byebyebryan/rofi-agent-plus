# Agent Plus documentation

Agent Plus is a direct native-TUI launcher and window picker for Codex and
Claude. Start with the [repository README](../README.md) for setup and keys.

| Guide | Use it for |
| --- | --- |
| [Architecture](architecture.md) | Environment identity, discovery, native entry, window ownership and refresh behavior |
| [Development and validation](development.md) | Source checks, installed-wheel checks, native proofs and the preview renderer |
| [Suite ownership](SUITE_INTEGRATION.md) | The boundary between Agent Plus, SSH Plus, native providers and the desktop |
| [Managed operations](managed-operations.md) | The pinned chezmoi installation, verification and rollback |
| [Configuration example](../examples/direct.toml) | Optional desktop settings and additional native homes |

[Fleet Plus](https://dev.byebyebryan.com/fleet-plus/) provides shared ecosystem
orientation; [Agent Plus's product page](https://dev.byebyebryan.com/fleet-plus/projects/agent-plus/)
provides its direct public introduction.

## Historical records

The [history index](history/README.md) preserves the October 2026 design,
implementation plan and first candidate receipts. They describe their own
stage, including labels and refresh strategies superseded by later fixes.
[Legacy retirement](legacy-retirement.md) identifies the Git checkpoint for
the previous conversation/Observer/Tmux implementation.

Current behavior comes from the guides above and the source. The
[managed selection](../artifacts/managed-direct-selection.json) identifies an
immutable deployed artifact; a documentation commit does not replace it.

The [removed-host row correction](removed-host-rows.md) records the a15
source, installed-artifact and managed-fleet acceptance.
