<img src="assets/icon.svg" width="64" height="64" alt="">

# nextcloud-omarchy-widget

A Tailscale-style Nextcloud widget for the Omarchy bar. A single-colour cloud
shows sync state, and clicking it opens a keyboard-friendly panel. The official
Nextcloud client keeps doing the syncing; this is a front-end to it.

## Features

- Bar icon for synced, syncing, paused, warning, error, disconnected, and client
  not running
- Live up/down speed, estimated amount and time left, file count, and current file
- Recent activity, with errors and conflicts pinned at the top
- Account, storage quota, and notification count
- Pause/resume, Sync now, open folder, open web UI, client settings
- Copy a public read-only share link

Left click opens the panel, right click pauses or resumes, and middle click
refreshes.

## Requirements

- Omarchy with the quickshell bar
- `nextcloud-client` running, logged in to one account
- `python3`, `busctl`, `ss`, `secret-tool`, `wl-copy`

## Install

```bash
git clone https://github.com/Drastixs/nextcloud-omarchy-widget.git
cd nextcloud-omarchy-widget
scripts/install.sh
```

This symlinks the plugin into `~/.config/omarchy/plugins`, adds the widget to the
right side of the bar, and hides the official tray icon. `shell.json` is backed
up first. `scripts/uninstall.sh` reverses it.

## Keyboard

| Key | Action |
|---|---|
| `j`/`k`, arrows | move cursor |
| `enter`/`space` | activate row |
| `o` | open containing folder |
| `s` | copy share link |
| `p` | pause/resume |
| `y` | sync now |
| `w` | open web UI |
| `r` | refresh |
| `esc` | close |

## Accuracy

Speed comes from the client's socket byte counters. Amount left is speed × the
client's own time estimate, so it's shown as `~`, and rows the client has no data
for are hidden rather than guessed. Status text is parsed in English only, so a
translated client reads as disconnected.

## Development

```bash
python3 -m unittest discover tests
```

- [PRD.md](PRD.md): product requirements
- [docs/helper-protocol.md](docs/helper-protocol.md): helper and widget contract
- [docs/spike-results.md](docs/spike-results.md): observed client behaviour

## License

MIT
