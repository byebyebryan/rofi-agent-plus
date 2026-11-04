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

Environments sort by picker uses during the rolling last 168 hours, highest
first, across hosts and providers. Equal counts keep the fixed Host Mesh,
provider and native-home order; numbered windows stay together within their
environment. If every listed environment has zero weekly uses, the list falls
back to most recently used first. Never-used environments keep the fixed order.
The last successfully opened/focused window is highlighted independently.

An accepted Open/New window launch or successful focus counts once; repeated
callbacks for the same action do not add uses. Filtering, discovery, refreshes
and activity inside native clients do not count. Each desktop learns its own
history, starting fresh with this version. The launcher freezes ranking for
the dialog, so concurrent uses and events aging out cannot move rows while
you navigate. Reopening captures current usage again.

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
