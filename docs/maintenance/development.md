# Development and validation

## Source checks

```sh
python3 -m venv .venv
. .venv/bin/activate
python scripts/bootstrap-dev
./scripts/check
```

The source gate runs unit tests, Ruff lint/format checks, shell/text-file
guards, public Host Mesh bundle validation and artifact-verifier negative
cases. It uses private state and controlled provider, SSH, process and desktop
fixtures. It opens no physical desktop windows or native provider managers.
CI runs this gate with Python 3.11.

To compare the vendored public bundle with an explicit SSH Plus checkout:

```sh
./scripts/check-contract-sync /absolute/path/to/rofi-ssh-plus
```

The contract bundle is producer-owned. Update its provenance and checksums
together when deliberately adopting a new public contract.

## Diagnostics

```sh
./bin/rofi-agent-plus list --json
./bin/rofi-agent-plus-host inventory --json
./bin/rofi-agent-plus-host inventory --json --config /private/path/direct.toml
```

`list` refreshes the bounded catalog and emits diagnostic schema 1, including
host/provider coverage, freshness, desktop availability and registered window
presence. A nonzero result identifies a refresh/cache failure. A peer failure
remains a diagnostic with last-known targets. The host command emits the strict
schema-1 inventory directly. Neither command opens a native manager.

The public commands are `list --json`, host `inventory --json`, and host
`enter --reference-json`. Entry requires an exact validated reference and a
real foreground TTY. The launcher and viewer entry points serve their dedicated
roles. Retired conversation `active`/`refresh`, session-limit and batch commands
have no compatibility runtime.

## Verify an installed artifact

Build and install into a new, unselected prefix. Keep the wheel digest with
the resulting receipts:

```sh
RUN=/absolute/private/run
PREFIX=/absolute/private/candidate-prefix
mkdir -p "$RUN/wheels"
python -m pip wheel --no-deps --wheel-dir "$RUN/wheels" .
python3 -m venv "$PREFIX"
"$PREFIX/bin/python" -m pip install --no-deps "$RUN"/wheels/*.whl
./scripts/check-installed-artifact \
  --candidate-prefix "$PREFIX" \
  --wheel "$RUN"/wheels/*.whl \
  --output "$RUN/artifact-receipt"
```

Use one wheel per run directory. Prefix and receipt output paths must be new;
the prefix must be absolute and owned by the current user. The verifier checks
wheel paths, installed package-byte parity, entry points, isolated imports,
runtime dependencies and controlled headless inventory outside the checkout.
This does not qualify native UI or provider entry.

## Native proofs

Native drivers are explicit tools, separate from ordinary source checks. Use
an installed prefix's interpreter with `-I -B` so the proof imports that
artifact instead of the checkout. Output directories must be new.

| Driver | Scope |
| --- | --- |
| `direct-rofi-input-proof drive OUTPUT cached` or `cold` | Private Xvfb, inert rows, idle-arrow navigation, automatic cold arrival and zero automatic callbacks after usable rows |
| `direct-rofi-proof OUTPUT` | Real Rofi first-frame selection, Tab action changes and filter retention; takes desktop focus |
| `direct-window-proof run OUTPUT` | Owned Niri/Kitty windows, real TTY, private layout validation, exact focus and independent client lifetime |
| `check-native-entry ...` | One selected host/provider/home through the installed helper, with owned synthetic native sessions |
| `direct-picker-proof --native-root FIXTURE_ROOT --output OUTPUT` | Actual picker launch, additional window, exact-row focus and stale-row refusal with an owned local fixture |
| `native-fixture prepare/observe/cleanup` | Private provider-home lifecycle and exact owned cleanup references |

The input proof needs Rofi, Xvfb, libX11 and libXtst. It uses a private Rofi PID
file and sends input only to its private X server. The cold case publishes
rows and waits for Rofi's idle callback without a key driving arrival.

```sh
"$PREFIX/bin/python" -I -B scripts/direct-rofi-input-proof drive "$RUN/cached" cached
"$PREFIX/bin/python" -I -B scripts/direct-rofi-input-proof drive "$RUN/cold" cold
```

The other GUI/native-entry proofs can change physical desktop focus. Run them
only on an unattended test desktop with owned fixtures. In the managed fleet,
that means unattended Snap, while Starship remains available for normal work.
Do not send input to ordinary native conversations. Revalidate cleanup handles
and preserve native daemon/worker descendants outside the fixture's ownership.

An installed host/provider case uses host-local configuration and helper paths:

```sh
"$PREFIX/bin/python" -I -B scripts/check-native-entry \
  --host "$LOGICAL_HOST" --home "$HOST_NATIVE_HOME" \
  --config "$HOST_DIRECT_TOML" --provider "$PROVIDER" \
  --helper "$HOST_PREFIX/bin/rofi-agent-plus-host" \
  --expected "$OWNED_FIXTURE_TEXT_A" --expected "$OWNED_FIXTURE_TEXT_B" \
  --exercise-client-state --output "$RUN/provider-case"
```

Omit `--helper` for a local case. `--fixture-script` observes the owned daemon
on its host; `--disconnect-first` exercises an owned SSH interruption without
entry replay. Run `--help` for the full selectors.

## Repository preview

```sh
python -B scripts/render-preview --output docs/assets/picker.png
```

This renders the production Rofi frame with synthetic rows on a private Xvfb
display and captures it with ImageMagick. It requires Rofi, Xvfb, ImageMagick
and DejaVu Sans Mono. It performs no host/provider discovery, opens no native
TUI and sends no physical desktop input. The theme is for documentation only;
Agent Plus uses the user's own Rofi theme in normal operation. See
[asset provenance](../../ASSETS.md).

## Review and delivery

Keep source, wheel, native mechanical/input checks, human judgment and managed
selection distinct. Repeat the gates affected by a change; a docs update does
not require another provider-entry matrix or a managed reinstall. A runtime
change needs a newly verified immutable artifact before deployment.

Keep the runtime limited to its [architecture](../reference/architecture.md): provider
adapters and native clients, exact owned-window identity, public Host Mesh and
finite discovery. Preserve unrelated work, native settings and existing clients.
[Managed operations](operations.md) covers scoped delivery and rollback.

## Documentation maintenance

Keep only `README.md` and `getting-started.md` at the docs root. Use lowercase
kebab-case filenames in one shallow topic folder: `guides/` for workflows,
`reference/` for current interfaces, and `maintenance/` for development/operations.
Media belongs in `assets/`. Mesh's hash-locked `contract-freeze.md` is the sole
root exception; its original bytes remain normative input.

Update or consolidate the existing guide instead of adding a dated plan,
handoff or status page. Delete superseded notes; Git retains earlier revisions.
Do not add forwarding pages or copy old reports into the current docs tree.
Keep executable test data beside tests, not under docs. Record necessary release
provenance with an immutable Git/release link in the operations guide.

Run `scripts/check-docs` and the canonical source gate before committing. The
docs check enforces this layout and checks local links/heading fragments.
Package, native, managed, consumer and physical acceptance remain separate.
