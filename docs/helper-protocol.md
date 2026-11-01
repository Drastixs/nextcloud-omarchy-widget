# Helper protocol

`helper/nextcloud-status` is a long-running python3 process (stdlib only) started by
`plugin/Service.qml`. It writes one JSON object per line to stdout, a full state each
time anything changes, and reads one command per line on stdin. Logs go to stderr.
Credentials never appear on stdout, stderr, or any command line.

## State (stdout)

```json
{
  "schemaVersion": 1,
  "state": "client-down|disconnected|paused|error|syncing|warning|synced",
  "summary": "Up to date",
  "lastSyncedAt": 1793460000,
  "client": { "running": true, "connected": true, "apiAvailable": true },
  "folders": [
    { "name": "Documents", "path": "/abs/path", "status": "OK", "paused": false }
  ],
  "transfer": {
    "active": false,
    "upBps": 0,
    "downBps": 0,
    "totalBytes": null,
    "etaSec": null,
    "bytesLeft": null,
    "progress": null,
    "filesDone": null,
    "filesTotal": null,
    "currentFile": null,
    "lastFinishedFile": null
  },
  "activity": [
    { "kind": "upload|download|delete|conflict|error", "name": "a.md", "folder": "Documents",
      "path": "/abs/path/a.md", "time": 1793460000 }
  ],
  "server": {
    "ok": true,
    "account": "user@host",
    "url": "https://host",
    "quotaUsed": 0,
    "quotaTotal": null,
    "notifications": 0,
    "error": null
  },
  "lastAction": { "seq": 1, "command": "share", "ok": true, "message": "Link copied" }
}
```

Unknown numbers are `null`, never guessed. `state` follows the precedence in PRD.md.
`folders[].status` is `null` while the socket is unavailable. `activity[].kind` is `null` when
the direction isn't known, and `time` is `null` on rows from the D-Bus recent list.
`transfer.currentFile` is display text and can end in ` +2 more`.
`lastAction` is the result of the most recent command, or `null`. `seq` goes up by one
for each result, so two identical results can be told apart.

## Commands (stdin)

`pause`, `resume`, `sync`, `refresh`, `share <absolute path>`, `settings` (open the
client's settings), `quit`.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--server-refresh SEC` | 60 | How often quota, activity, and notifications are fetched |
| `--activity-count N` | 8 | Activity rows to emit |
| `--quota-warn-percent N` | 90 | Quota percentage that sets the warning state |
| `--config-dir DIR` | `~/.config/Nextcloud` | Directory holding `nextcloud.cfg` and `logs/` |
