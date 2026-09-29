# Agent Plus session client: Ghostty split research

Date: 2026-09-26

Status: read-only source research plus two disposable Ghostty windows. This is
not a client design decision, implementation, or deployment.

## Question

Can Ghostty display an always-visible session rail beside a direct attachment
to an existing ordinary tmux session? This would retain the current
agent -> one tmux -> Ghostty rendering path. The previous
[ordinary-tmux presentation spike](agent-plus-session-client-ordinary-tmux-results.md)
used a second tmux server to create the rail; the operator reports that
WSNav's same two-tmux topology has very annoying visible flicker during active
native TUI use.

## Findings

The installed Ghostty is `1.3.1-arch2`, using GTK on Linux. Its local action
list and key bindings include `new_split:right`, `goto_split:left/right`, and
pixel-based `resize_split` actions. The [Ghostty configuration reference](https://ghostty.org/docs/config/reference#command)
says `-e` sets `initial-command` for the first surface, while `command` runs
in later surfaces. In a dedicated instance, that permits the rail as the first
surface and a viewer supervisor as the command for the second split.

In a disposable window with only `/usr/bin/sleep` panes, the exact window's
`org.gtk.Actions` D-Bus object listed `split-right`. Activating it created a
right split and started the configured second command. The window was targeted
through its dedicated GTK class, process identity, unique bus name, and window
object path; the action worked even while the test window was not focused.
Both test panes and the window were then stopped, and the screenshot removed.
A second disposable window repeated split creation with inert Python probes.
Neither pane received `GHOSTTY_SURFACE_ID` or `GHOSTTY_WINDOW_ID` in this
installed build. Its panes, window, and probe files were removed.

This establishes a possible one-window launch route. The viewer supervisor
could accept exact session selections over a local socket, detach/stop only
its current `tmux attach` or SSH viewer, then attach the chosen session in the
same Ghostty pane. Existing tmux servers would continue to own agent processes.
That switching behavior is a design hypothesis; it was not exercised here.

## Limits and next proof

- Ghostty 1.3.1 has no documented `+split` CLI. The GTK window action works in
  this build but is an internal action rather than a supported external
  automation contract. The [upstream split automation request](https://github.com/ghostty-org/ghostty/issues/12556)
  identifies this missing public interface.
- The new split began at half the window width. Ghostty's documented
  `resize_split` action changes pixels, not terminal columns; the test window's
  exported GTK actions did not include a resize action. Keeping the rail at
  approximately 32 columns through window/font changes is unproven. The
  [keybinding action reference](https://ghostty.org/docs/config/keybind/reference#resize_split)
  documents the pixel-based operation.
- The installed build exposed no surface ID to the test processes. The window
  GTK action list did not provide `goto_split` or a targeted focus action.
  Ghostty's normal focus key bindings work for a person, but programmatic
  focus from the rail or Rofi picker has no verified contract here.
- Ghostty's Linux [window-save-state option](https://ghostty.org/docs/config/reference#window-save-state)
  has no effect, so startup must recreate the split and its width. No session
  selection, SSH loss, native TUI activity, small-screen use, or flicker check
  was run in these inert windows.

The narrow next spike would create the rail and a persistent viewer supervisor
in a disposable Ghostty window, attach only marked ordinary tmux fixtures, and
exercise A -> B -> A, exact-target refusal, resize, focus, viewer loss, and a
native TUI during typing/streaming. It should measure the rail width at 80x24
and after resizing, and ask the operator whether the visible artifact is gone.
Do not rely on the internal D-Bus action as a production contract before that
specific compatibility and ownership question is resolved.
