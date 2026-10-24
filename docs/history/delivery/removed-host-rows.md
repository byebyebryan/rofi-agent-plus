# Removed-host rows correction

> Historical record. This document preserves its original checkpoint and may describe retired providers, interfaces or plans. Use the [current documentation](../../README.md) for supported setup and behavior.

Snap's current Host Mesh contained only Snap and Starship. Agent Plus had
already marked Carbon removed in its cached catalog, but the renderer still
emitted a removed-host status and two unknown-provider warnings. No cached
Carbon target or registered Carbon window existed on Snap.

The renderer now ignores removed hosts' cached targets and provider warnings.
Registered windows remain independently available; a removed-host notice is
shown only alongside such a retained window. This preserves local access to
existing clients without granting a new host route. A configured but unreachable
peer still retains its last-known targets and ordinary failure diagnostics.
The cached tombstone/history remains intact.

The [acceptance receipt](../../evidence/2026-10-23-removed-host-rows.json) records the
new private a15 wheel and scoped five-link selection on Snap, Starship and
Carbon. All 155 source cases pass, including removed empty/provider-warning
hosts, cached targets and retained registered instances. Installed-byte,
entrypoint and controlled-inventory gates pass. Against the exact Snap cache,
Carbon's three warning lines become zero and every other row is unchanged.

Native processes, services, direct configuration, bindings and directional
Host Mesh were preserved. No daemon or client restart, native action, cache
purge, GitHub push or public publication was performed. Existing open picker
processes retain their old artifact until reopened. Physical GUI/entry behavior
is not inferred from these headless checks.
