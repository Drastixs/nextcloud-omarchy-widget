# PRD: Nextcloud widget for the Omarchy bar

Status: draft v1 · 2026-10-31

## Problem

The Nextcloud desktop client's tray icon is a generic system-tray item. It looks
out of place in the Omarchy bar, opens a heavy Qt window, and doesn't show at a
glance what's syncing. The built-in Omarchy Tailscale widget is the model to
follow: a native bar icon whose glyph shows state, with a keyboard-friendly
dropdown panel.

## Goals

- A native Omarchy bar widget (`bar-widget` plugin, QML) that replaces the
  Nextcloud tray icon in daily use.
- Answer "is everything synced, and if not, what's happening?" at a glance.
- Common actions in one or two clicks or keys: pause, sync now, open folder,
  open web UI, copy a share link.

## Non-goals (v1)

- Replacing the sync engine. The official `nextcloud` client keeps syncing; this
  widget is a front-end to it.
- Per-folder rows with per-folder controls (cut from v1; may come back in v2).
- Conflict resolution UI. Conflicts are surfaced in activity only.
- Account setup or login. Done once in the official client.

## Architecture

```
┌──────────── Omarchy shell (quickshell) ────────────┐
│ Panel.qml  ◄── Service.qml ◄── helper (python3)    │
└────────────────────────────────────┬───────────────┘
                                     │
         ┌───────────────────────────┼──────────────────────────┐
         ▼                           ▼                          ▼
 $XDG_RUNTIME_DIR/Nextcloud/socket  ~/.config/Nextcloud/   Server OCS/WebDAV API
 (live file/folder status)          nextcloud.cfg          (quota, activity,
                                    (accounts, folders,     notifications, shares)
                                     paused flags)
```

The layout copies `/usr/share/omarchy/shell/plugins/panels/{tailscale,dropbox}`:
`manifest.json`, `Panel.qml`, `Service.qml`, `NextcloudIcon.qml`, `Model.js`,
plus a `status.py` helper that returns one JSON blob per poll.

### Data sources (checked on this machine)

| Need | Source | Notes |
|---|---|---|
| Folder list, server URL, paused flags | `nextcloud.cfg` | `0\Folders\N\localPath`, `paused`, `0\url` |
| Live status per folder | Client socket `RETRIEVE_FOLDER_STATUS:<path>` | Returns `STATUS:OK\|SYNC\|NEW\|ERROR\|WARNING:<path>`. Confirmed working (client 3.16.7, socket protocol 1.1). |
| Client running? | socket connect / `pgrep nextcloud` | |
| Progress (current file, files left, speed) | **Unknown**: the socket doesn't expose it | See open question 1 |
| Recent activity | OCS Activity API (`/ocs/v2.php/apps/activity/api/v2/activity`) | Needs credentials |
| Quota | WebDAV `PROPFIND` `quota-used-bytes` / OCS `cloud/user` | Needs credentials |
| Notifications | OCS `apps/notifications/api/v2/notifications` | Needs credentials |
| Share link | OCS `apps/files_sharing/api/v1/shares` (POST, `shareType=3`) | Needs credentials |
| Pause / resume | **Unknown**: no socket command | See open question 2 |
| Force sync | Socket has no "sync now"; touching a file or restarting the client works | See open question 2 |

Credentials: the client stores its app password in the system keyring
(libsecret). The helper reads it with `secret-tool`, or the user gives the widget
its own app password stored in the keyring. Credentials never go in the
plugin config.

## Bar icon

Monochrome, Tailscale-style. The glyph shows state:

| State | Glyph | Trigger |
|---|---|---|
| Synced | cloud | every folder `OK` |
| Syncing | cloud + animated arrows | any folder `SYNC`/`NEW` |
| Paused | cloud + pause bars | every folder `paused=true` |
| Warning | cloud + dot | any `WARNING`, or quota > 90% |
| Error | cloud + `!` in the theme's urgent colour | any `ERROR` |
| Offline / client not running | cloud with a slash, dimmed | socket unreachable |

- Left click: open the panel.
- Right click: pause or resume all syncing.
- Tooltip: one line, e.g. "Nextcloud: syncing 12 files" or "Up to date · 2m ago".
- Optional unread-notification badge (setting, default on).

## Dropdown panel

Top to bottom:

1. **Header**: state word and summary line ("Up to date", "Syncing…",
   "Paused", "3 errors"), with a pause/resume toggle on the right like
   Tailscale's on/off switch.
2. **Sync progress** (only while syncing): current file name, "N files left",
   transfer speed, and a progress bar. Depends on open question 1.
3. **Recent activity**: the last 8 changed files. Each row shows an
   upload/download/delete icon, file name, folder, and relative time. Enter opens
   the file and `o` opens its containing folder. Errors and conflicts are pinned
   at the top in the urgent colour.
4. **Server**: account (`user@nextcloud.je-marshall.uk`), quota bar
   ("42.1 GB of 100 GB"), and notification count with a click to open the web
   notifications.
5. **Actions row**: Sync now · Open folder · Open web UI · Share link… ·
   Client settings.

### Share link flow

`Share link…` opens a file picker (`omarchy` file chooser / portal) limited
to synced folders. The widget creates a public link through OCS, copies it with
`wl-copy`, and sends a notification. Selecting an activity row and pressing `s`
shares that file directly.

### Keyboard (matches Tailscale's conventions)

| Key | Action |
|---|---|
| `j`/`k`, arrows | move cursor |
| `enter`/`space` | activate row |
| `o` | open containing folder |
| `s` | copy share link for selected file |
| `p` | pause/resume |
| `y` | sync now |
| `w` | open web UI |
| `r` | refresh |
| `esc` | close |

## Settings (manifest schema)

- `refreshIntervalSec` (default 5 while syncing, 30 when idle)
- `activityCount` (default 8)
- `showNotificationBadge` (default true)
- `quotaWarnPercent` (default 90)
- `hideTrayIcon`: hint text explaining how to stop the official client's own
  tray icon (`nextcloud --background` with no tray is not supported upstream;
  see open question 3)

## Open questions

1. **Progress data.** The socket gives per-path status but no bytes or
   file counts. Options: (a) tail the client log at `~/.config/Nextcloud/logs/`
   if debug logging gives usable progress lines, (b) watch the client's sync
   journal DB (`.sync_*.db`) in each folder, (c) show "Syncing N items" by
   counting `SYNC`-status paths, with no speed. Prototype (c) first and look into (a).
2. **Pause / force sync.** The client has no CLI or socket command for these.
   Options: edit `paused=` in `nextcloud.cfg` and restart the client (works, but
   restarting is heavy); check whether the client exposes D-Bus (it registers
   `com.nextcloudgmbh.Nextcloud` for single-instance use; method coverage
   unknown); or treat "Sync now" as restarting the client. Needs a spike.
3. **Hiding the official tray icon.** If the widget replaces it, the client's
   own tray item still appears in the bar's tray section. Either hide it with a
   tray-filter rule in the Omarchy shell, or accept duplication.
4. **Multiple accounts.** There's one account today. Should v1 assume one, or
   render an account switcher like Tailscale's connection switcher?

## Milestones

1. Spike: answer open questions 1 and 2 (half a day).
2. Read-only widget: icon states, header, recent activity from the API, quota.
3. Actions: pause/resume, sync now, open shortcuts.
4. Share link flow.
5. Polish: keyboard, tooltips, settings schema, README, screenshots.
