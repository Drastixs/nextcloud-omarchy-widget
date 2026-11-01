# PRD: Nextcloud widget for the Omarchy bar

Status: draft v3 · 2026-11-01 · spike complete, see `docs/spike-results.md`

## Problem

The Nextcloud desktop client's tray icon is a generic system-tray item. It opens
a heavy Qt window and doesn't show at a glance what's syncing. The built-in
Omarchy Tailscale widget is the model to follow: a native bar icon whose glyph
shows state, with a keyboard-friendly dropdown panel.

## Goals

- A native Omarchy `bar-widget` plugin that replaces the Nextcloud tray icon.
- Answer "is everything synced, and if not, how much is left and how fast?" at a
  glance.
- Common actions in one or two clicks or keys: pause, sync now, open folder,
  open web UI, copy a share link.

## Decisions

| Topic | Decision |
|---|---|
| Sync engine | The official client keeps syncing; the widget is a front-end |
| Accounts | One account only |
| Official tray icon | Hidden, using the Omarchy tray's `hidden` setting |
| Transfer numbers | Good estimates; no debug logging |
| Where speed shows | Panel only. The bar shows just the animated icon |
| Progress detail | Up/down split, files N of M, current file name |
| Share links | Read-only, no expiry, copied straight away |

## Non-goals (v1)

- Replacing the sync engine.
- Per-folder rows and per-folder controls.
- Conflict resolution UI. Conflicts appear in activity only.
- Multiple accounts, login, and account setup.
- Speed in the bar, or a speed graph.

## Architecture

```
Omarchy shell (quickshell)
  Panel.qml + NextcloudIcon.qml + Model.js
        ▲ reads JSON lines, sends commands
  Service.qml
        ▲
  helper/nextcloud-status (long-running python3, stdlib only)
        ├─ D-Bus, CloudProviders API   state, progress text, pause, recent files
        ├─ client socket               folder and file status
        ├─ ss -tin                     up/down speed
        ├─ client log (info level)     file names, if the spike proves it works
        ├─ nextcloud.cfg               folders, server URL
        └─ server OCS/WebDAV           quota, activity, notifications, shares
```

The layout copies `/usr/share/omarchy/shell/plugins/panels/{tailscale,dropbox}`.
The helper is long-running rather than polled because it listens for D-Bus
`PropertiesChanged` and needs consecutive samples to work out speed.

Install: symlink the repo's `plugin/` into `~/.config/omarchy/plugins/`, then add
the widget to the bar.

## Data sources

Tested on this machine with client 35.0.0 (socket protocol 1.1) and server 35.0.1.
Exact strings and commands are in `docs/spike-results.md`.

| Need | Source | Status |
|---|---|---|
| Folders, server URL | `nextcloud.cfg` | Confirmed |
| Folder status | Socket `RETRIEVE_FOLDER_STATUS:<path>`, plus pushed `STATUS:` lines | Confirmed. Folder: `OK`, `SYNC`, `IGNORE`. File: `NOP`, `OK`, `SYNC`, `ERROR`, `IGNORE`. `NEW`, `WARNING`, and folder `ERROR` not seen |
| Connection and sync text | D-Bus `com.nextcloudgmbh.Nextcloud`, `/Folder/N`, `CloudProviders.Account` `Status`, `StatusDetails` | Confirmed while connected. `Status`: 1 idle, 2 syncing, 3 error or paused. Disconnected text not seen |
| Pause | D-Bus `org.gtk.Actions` `Activate("pause")` on each folder | Confirmed: applies within 0.2 s, no restart, `Changed` signal, `paused=` written to `nextcloud.cfg`. Across restart not tested |
| Sync now | Pause then resume on each unpaused folder | Confirmed: server file arrived in 0.3 to 0.5 s, against 14.7 to 54.3 s with no action |
| Recent files | D-Bus `org.gtk.Menus`, "Recently changed" | Menu confirmed; only holds about 6 items. Not retested |
| Speed | `ss -tinHp` `bytes_sent`/`bytes_received` deltas for the client's sockets | Confirmed on 20 MB up and down: totals within 1%, smoothed readings within 20%. Larger transfers not measured |
| File names, N of M | Client log at info level | Confirmed: start and finish lines per file |
| Quota, activity, notifications, shares | Server OCS/WebDAV | Confirmed, all working |
| Credentials | Keyring entry `<user>_app-password:<url>/:0@Nextcloud` (base64, decode once) | Confirmed with HTTP 200. Select by label |
| Hide tray icon | `hidden: ["Nextcloud"]` on the `omarchy.tray` bar entry | Item id and setting confirmed; applied in milestone 3 |

### Things that shape the design

- **Pause is a toggle, per folder.** Read each folder's state with `Describe`
  first, then flip only the ones that differ, or a mixed state gets inverted.
- **Paused folders report `Status` 3, the same as error.** The icon must check the
  pause state before treating `Status` as an error.
- **Pause doesn't stop a running transfer.** A PUT in flight finished normally, and
  `StatusDetails` showed `Syncing` again while the folder was paused. The socket
  still says `OK` when paused, so only `Describe` gives the real state.
- **`Status` 3 is not a reliable error signal.** Of three failed uploads, only the
  first showed `Connected - Error`, for about 12 s. All three ended at `Status` 1
  with `Connected - Success, some files were ignored.`, folder `IGNORE`, and file
  `ERROR`.
- **D-Bus folder numbers don't match `nextcloud.cfg` numbers.** Match on path.
- **`StatusDetails` is translated text.** Parsing needs fixed patterns for the
  English strings, and shows "Syncing…" with no numbers if they don't match.
- **The status text is prefixed `Connected - ` and has these shapes:**
  - `Syncing 191 MB (6 minutes 22 seconds left)`: the size is the run's total, not
    what remains, and there is no file count. Sizes are 1024-based, so 200,000,000
    bytes reads `191 MB`.
  - `Syncing 2 of 12  (0 seconds left)`: file counts with two spaces before `(`.
    Seen on deletes, where the total size is 0.
  - `Syncing 19 MB`, or plain `Syncing`: no estimate.
  - `Syncing 0 of 0`: discovery or no-op runs, so treat it as having no numbers.
  - A run's first `(0 seconds left)` is false, and the last seconds also read 0.
- **There is no "sync now" call, but pause then resume does the job.** It starts a
  real run even on an idle folder. Skip it while `Status` is 2, and skip folders the
  user paused.
- **Uploads hit a 60 s limit on this server.** A 200 MB upload failed three times at
  60.6 s with HTTP 499 on its first chunk, then the file was blacklisted for 10
  minutes. At the current 0.47 MB/s, that limit is about 28 MB per request. The
  limit's location is unknown, and fixing it is outside this project.
- **The sync journal can't be read.** `sqlite3` gets "database is locked" while
  the client runs, so it's not a data source.
- **The server is reached over the tailnet**, so Tailscale being down looks like
  "disconnected" and shouldn't be shown as a Nextcloud fault.

## How the progress numbers work

| Shown | How it's worked out | When it's unavailable |
|---|---|---|
| ↑ / ↓ speed | `bytes_sent`/`bytes_received` deltas over about 2s, smoothed over about 5 samples. New sockets start from zero; closed ones are dropped so counters never go negative. Includes the client's background polling; below 10 KB/s reads as 0. | Never while the client runs |
| ETA | From `(… left)` in the status text | Hidden until the client trusts it |
| Amount left | Combined speed × ETA, shown with `~` | Hidden when ETA is missing; total size shown instead |
| Progress bar | `elapsed / (elapsed + ETA)` for this sync run | Indeterminate bar when ETA is missing |
| Files N of M | Client log, or status text when it's in the file-count shape | See below |
| Current file | See below | See below |

**Files N of M and current file** come from the info log, because the D-Bus text
omits them whenever a sync has a known size. The log gives usable names and both
start and finish lines, with no debug logging:

- M: `discovered` lines since `Sync started`, for `ItemTypeFile`, excluding
  `UPDATE_METADATA`.
- N: `Completed propagation of …` lines.
- Current file: `Starting … propagation of …` with no matching completion. Up to 3
  files transfer at once, so the panel shows the latest and "+2 more".

The log rotates about once a minute while active, so the helper must follow the
newest file. If it starts mid-run it can't know M, and hides the count for that
run. If the log can't be read, the panel shows "Last finished: <file>" from the
recent-files menu, and hides the file count unless the client reports one.

## Bar icon

Monochrome, Tailscale-style. Checked in this order, first match wins:

| State | Glyph | Trigger |
|---|---|---|
| Client not running | cloud with slash, dimmed | D-Bus name and socket missing |
| Disconnected | cloud with slash | `StatusDetails` doesn't start with `Connected` (disconnected text not yet seen) |
| Paused | cloud + pause bars | every folder paused, from `Describe` |
| Error | cloud + `!`, urgent colour | `Status` 3 on an unpaused folder, any folder `IGNORE`, or any file `ERROR` pushed on the socket |
| Syncing | cloud + animated arrows | `Status` 2, or any folder `SYNC` |
| Warning | cloud + dot | any `WARNING`, or quota over the limit |
| Synced | cloud | otherwise |

- Left click opens the panel. Right click pauses or resumes everything.
- Tooltip: one line, such as "Up to date · 2m ago".
- Unread notification badge, on by default.

## Dropdown panel

```
┌──────────────────────────────────────────┐
│ Syncing…                          [ ⏸ ]  │  header + pause toggle
├──────────────────────────────────────────┤
│ ↑ 1.2 MB/s   ↓ 4.5 MB/s                  │  only while syncing
│ ▓▓▓▓▓▓▓▓░░░░░░░░  ~820 MB left · 4m      │
│ File 3 of 10 · thesis-draft.pdf          │
├──────────────────────────────────────────┤
│ RECENT                                   │
│ ! budget (conflicted copy).xlsx    now   │  errors/conflicts pinned
│ ↑ notes.md        Documents        2m    │
│ ↓ photo.png       Screenshots      1h    │
├──────────────────────────────────────────┤
│ <user>@<server>                          │
│ ▓▓▓▓▓░░░░░░░  42.1 GB of 100 GB          │
│ 3 notifications                          │
├──────────────────────────────────────────┤
│ Sync now · Open folder · Web · Share…    │
│ Client settings                          │
└──────────────────────────────────────────┘
```

- **Open folder:** opens the only folder directly; with several, a sub-list.
- **Activity:** last 8 items from the server's activity API, shown only for
  files inside synced folders. If that API is missing, the D-Bus recent list.
  One entry can cover several files, so expand `objects` into rows. Server paths
  map to local ones through `targetPath`. The API says created or deleted, not
  upload or download, and has no client errors, so pinned errors and ↑/↓ arrows
  come from the log.
- **Quota:** `-3` means no limit, as on this server. Show used space with no bar.
- **Share…:** opens a file picker limited to synced folders. Pressing `s` on an
  activity row shares that file directly. Either way the widget creates a public
  read-only link with no expiry (`permissions=1`, which the server returns as
  17), copies it with `wl-copy`, and sends a notification. Files outside synced folders are refused with a message.
- **Error states:** each section fails alone. A server error greys out the
  server section and leaves sync status working, and missing credentials show a
  single "Can't read credentials" line.

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

- `activityCount` (default 8)
- `showNotificationBadge` (default true)
- `quotaWarnPercent` (default 90)
- `serverRefreshSec`: quota, activity, notifications (default 60)

## Security

- Credentials are read from the keyring by the helper only, stay in memory, and
  are never logged, written to plugin config, or passed on a command line.
- Requests go only to the server URL in `nextcloud.cfg`, over HTTPS.
- Public repo: no server names, usernames, or paths in code, docs, or
  screenshots.

## Plan

### 1. Spike (half a day): done 2026-11-01

Results are in `docs/spike-results.md`. The 200 MB file could not upload because of
the 60 s limit, so tests used 20 MB files. Resuming does start a sync, so Sync now
stays in v1.

Uses a temporary 200 MB test file in a synced folder, removed afterwards, and
briefly pauses and resumes sync.

1. Upload and download the test file, recording `StatusDetails`, `ss` counters,
   and the client log. Compare measured speed with the real transfer rate.
2. Check that the log gives usable file names at info level, and whether it
   signals a start or only a finish.
3. Trigger `pause` on and off. Confirm state changes without a restart.
4. Check whether resuming starts a sync. If it does, Sync now is pause then
   resume. If not, Sync now is removed from v1.
5. Call quota, activity, notifications, and share creation with the keyring
   credential.

Exit: every "not yet" above has an answer and the data table is updated. The hide
tray row moves to milestone 3, and states with the client down, disconnected, or
restarted remain untested until the definition of done checks below.

### 2. Helper (1 day)

- JSON-lines output: one full state object per change, plus commands on stdin
  (`pause`, `resume`, `sync`, `share <path>`, `refresh`).
- Unit tests with captured status strings, `ss` output, and `nextcloud.cfg`
  fixtures, covering every pattern in "How the progress numbers work".

### 3. Read-only widget (1 day)

- Manifest, `Service.qml`, icon with all 7 states, header, progress, recent
  files, server sections.
- Hide the official tray icon.

### 4. Actions (half a day)

- Pause/resume in panel and on right click, Sync now, open shortcuts.

### 5. Share links (half a day)

- Picker, OCS call, clipboard, notification.

### 6. Polish (half a day)

- Keyboard, tooltip, settings, README with install steps, sanitised screenshots.

## Definition of done

- The official tray icon is gone and the widget has run for a week of normal use
  with no wrong "Synced" state.
- During a large transfer, speed is within 20% of the real rate. Large uploads
  depend on the server's 60 s limit being lifted; until then this is checked on
  files small enough to finish.
- Client quit, Tailscale down, and server down each show the right state and
  recover without restarting the shell.
- Pause survives a client restart and matches the client's own state.

## Risks

- **D-Bus progress text or log lines change** between client updates. Parsing
  failures fall back to plain "Syncing…", and fixtures make breakage easy to fix.
- **A failed upload looks almost idle** once blacklisted, so the helper must keep
  the socket's file `ERROR` state, or the bar could wrongly say "Synced". That would
  fail the definition of done.
- **Hiding the tray icon removes the client's menu.** Client settings stays
  reachable through the panel's `opensettings` action.
- **A client update could drop CloudProviders support.** The helper reports it
  as "client API unavailable" rather than showing a false state.
