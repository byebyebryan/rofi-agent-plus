# Agent Plus usage

## Use

| Key | Action |
| --- | --- |
| Up / Down | Select an environment or window |
| Enter | Run the visible action: Open or New window |
| Tab / Shift+Tab | Cycle Open / New window |
| Alt+R | Refresh inventory, joining any pending startup refresh |
| Left / Right, Ctrl+B / Ctrl+F | Move the search cursor |
| Escape / Ctrl+G | Close the picker |

A target with no window gets one launcher row. Once windows exist, each has its
own numbered row and title. **Open** focuses that exact window; **New window**
opens another client for the same environment, without creating a conversation.
Starting or uncertain rows remain selectable for New window; ordinary Open
refuses uncertain focus. Native terminal controls close a window.

Search covers host, provider, environment and title. Titles are sampled once
per open picker, with leading spinner/status icons removed. Reopening samples
them again. Background results never redraw a usable list while you navigate;
reopen or use Alt+R to load updated inventory. An empty list fills automatically
when its first usable results arrive.

## Inspect inventory

```sh
rofi-agent-plus list --json
rofi-agent-plus-host inventory --json
```

Both commands perform bounded passive discovery without opening a native TUI.
Unreachable hosts retain last-known targets with errors. Removed peers supply
no cached target/warning rows; registered windows can remain without granting
a new host route. See [architecture](../reference/architecture.md).
