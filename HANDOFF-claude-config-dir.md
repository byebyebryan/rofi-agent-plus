# Handoff: Claude adapter must not set CLAUDE_CONFIG_DIR for the default home

Status: diagnosed, fix written and tested, **not committed**. This note is the only part on
`main`. Whoever lands the fix should delete this file in the same change.

An uncommitted copy of the change sits on host `snap` in
`~/code/rofi-agent-plus/.claude/worktrees/claude-default-config-dir`, a detached HEAD at the
commit that added this note. The full code is also below.

## Problem

`rofi_agent_plus/providers/claude.py` `entry_environment()` always launched `claude agents` with
`CLAUDE_CONFIG_DIR=$HOME/.claude`, the provider's default home.

Claude Code picks its main config file (per-folder workspace trust, user-scope MCP servers,
onboarding state) based on that variable:

- unset: `$HOME/.claude.json`
- set: `$CLAUDE_CONFIG_DIR/.claude.json`, even when the value is the default `$HOME/.claude`

So every Claude target launched from the picker, plus the daemon it starts and every background
session that daemon spawns, used `~/.claude/.claude.json`. That file is a near-empty profile. It
was first written on 2026-10-22, when 0.15.0a8/a9 landed.

| file | trusted folders | user MCP servers |
|---|---|---|
| `~/.claude.json` | 10 (incl. `/home/bryan`, `~/code`, `~/code/caos`) | mcp-gateway, snap-bridge |
| `~/.claude/.claude.json` | 0 | none |

Observed on `snap` with Claude Code 2.1.310 and Agent Plus 0.15.0a15:

- The agents view asked for home-folder trust on every launch.
- Resuming background sessions failed with "Workspace not trusted", e.g. `2026-recap-editor` in
  `~/code/caos`. All 24 session folders are untrusted against `~/.claude/.claude.json`, and all
  of them are trusted against `~/.claude.json`.
- Resumed sessions lost the mcp-gateway and snap-bridge MCP servers.

Evidence: `/proc/<pid>/environ` of the picker-launched `claude agents` and `claude daemon run`
had `CLAUDE_CONFIG_DIR=/home/bryan/.claude`. Their parent python (`rofi_agent_plus`) and kitty
did not have the variable.

On 2026-10-26 09:35, `claude agents` was launched from a plain kitty shell and the daemon
restarted. All daemon processes then had the variable unset, `2026-recap-editor` resumed, and
the MCP servers came back. That confirms the cause.

## Fix

In `rofi_agent_plus/providers/claude.py`, bump `ADAPTER_REVISION` to `"claude-agents-v2"`,
because the launch environment changes, and replace `entry_environment` with:

```python
def entry_environment(base: Mapping[str, str], home: str) -> dict[str, str]:
    selected = dict(base)
    # Claude Code keeps its global state (workspace trust, user MCP servers) in
    # $HOME/.claude.json only while CLAUDE_CONFIG_DIR is unset; setting it, even
    # to $HOME/.claude, switches to $CLAUDE_CONFIG_DIR/.claude.json instead.
    if home == _native_home(selected):
        selected.pop("CLAUDE_CONFIG_DIR", None)
    else:
        selected["CLAUDE_CONFIG_DIR"] = home
    return selected


def _native_home(env: Mapping[str, str]) -> str | None:
    user_home = env.get("HOME")
    if not user_home or not Path(user_home).is_absolute():
        return None
    return str((Path(user_home) / ".claude").resolve(strict=False))
```

How the rule behaves:

- It keys off the child's `HOME`, because that is what Claude Code itself resolves.
- If the selected home is `$HOME/.claude`, Claude finds the same folder natively, so the variable
  is removed, including an inherited value.
- Any other home, or a missing or relative `HOME`, keeps the old behavior. So the existing harness
  case (`HOME=/not-the-owning-home`) is unchanged.

Add this test to `tests/test_direct_host.py` as a method of `DirectHostTests`, after
`test_native_status_requires_recognized_positive_evidence`. It is shown dedented because ruff
formats code blocks in Markdown, so indent it one level when pasting:

```python
def test_claude_native_home_leaves_config_dir_unset(self) -> None:
    native = str(self.harness.claude_home.resolve())
    base = {"HOME": str(self.harness.home), "CLAUDE_CONFIG_DIR": "/inherited/claude"}
    selected = claude.entry_environment(base, native)
    self.assertNotIn("CLAUDE_CONFIG_DIR", selected)
    self.assertEqual(selected["HOME"], str(self.harness.home))
    self.assertEqual(base["CLAUDE_CONFIG_DIR"], "/inherited/claude")
    work = str(self.harness.home / ".claude-work")
    self.assertEqual(claude.entry_environment(base, work)["CLAUDE_CONFIG_DIR"], work)
    for other in ({"HOME": "/not-the-owning-home"}, {"HOME": "relative"}, {}):
        with self.subTest(env=other):
            self.assertEqual(claude.entry_environment(other, native)["CLAUDE_CONFIG_DIR"], native)
```

`scripts/check` passed with this change on `snap`: 156 tests OK, ruff lint and format clean,
contract and artifact gates pass.

## Open questions

- The `ADAPTER_REVISION` bump changes the spec revision, so saved entry references become stale
  once and need re-picking. Drop the bump if that's unwanted.
- `launch_spec()` still declares `"environment": {"CLAUDE_CONFIG_DIR": home}` and
  `"clearedEnvironment": []`, so it doesn't describe the default-home case. It can't know the
  child's `HOME`. Decide whether the spec should say so, or whether the revision bump is enough.
- `_capability_probe` and `_daemon_probe` also go through `entry_environment`, so the default-home
  probes now run without the variable. That should be fine, but confirm that
  `claude daemon status` sees the same daemon either way.

## After shipping

Launch the agents view from the picker. Then confirm that the `claude agents` and
`claude daemon run` processes have no `CLAUDE_CONFIG_DIR` in `/proc/<pid>/environ`. If a daemon
started by the old launcher is still running, run `claude daemon stop --any` first; it ends
background sessions. `~/.claude/.claude.json` is unused after that and can be deleted.
