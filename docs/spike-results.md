# Spike results (PRD plan milestone 1)

Run 2026-11-01 on the real client: desktop 35.0.0 (socket protocol 1.1), server 35.0.1.
No client restart. Sanitised raw captures are in `tests/fixtures/`. Examples below use
`alice`, `nextcloud.example.com`, and `/home/alice`.

## Outcome

- **Sync now is kept.** It is pause then resume on each folder. Resume starts a real sync
  run, even on an idle folder, and it picks up server changes straight away.
- **`ss` speed is good enough.** Total bytes matched the transfer within 1%. Smoothed
  readings stayed inside the 20% target in every window measured.
- **Files N of M and current file are available** from the info log, without debug logging.
- **Quota, activity, notifications, and shares all work** with the keyring credential.
- **Uploads that need one request longer than 60 s fail on this server.** The planned 200 MB
  test could not complete, so tests used 20 MB. See the next section.

## Deviation: the 200 MB test could not finish

Three client attempts each failed at the same point. Upload ran at about 0.47 MB/s.

| Attempt | Request sent | Failed | Duration |
|---|---|---|---|
| 1 | 21:35:01.547 | 21:36:02.114 | 60.57 s |
| 2 | 21:39:52.583 | 21:40:53.211 | 60.63 s |
| 3 | 21:43:49.322 | 21:44:49.889 | 60.57 s |

```
PUT .../remote.php/dav/uploads/alice/2952557221/00001 - server replied: status code 499
"Client Closed Request"
```

- The client used `PropagateUploadFileNG` (chunked). All three failed on chunk `00001`, so
  no chunk ever completed.
- At 0.47 MB/s, 60 s carries about 28 MB. The chunk was therefore larger than that, but the
  log does not state its size.
- After three failures the client blacklisted the file: `Setting blacklist entry ... 625`,
  then `skipped due to earlier error, trying again in 10 minutes`.
- The almost identical durations point to a fixed 60 s limit somewhere on the server path.
  This is inferred from client-side timing. The server and any proxy were not inspected,
  and no client or server setting was changed.
- A 20 MB file took 43.7 s and used `PropagateUploadFileV1` (a single PUT). The size where
  the client switches between the two was not determined.

**Impact beyond the widget:** at this rate any file above roughly 28 MB may fail to upload
through the client. Only the 200 MB case was tested. Fixing it is outside this project, but
the widget must show the failure properly (see "Error states").

Tests actually run instead:

| Test | Size | Result |
|---|---|---|
| Upload via client | 200 MB | Failed 3 times; 83 MB acked in total, none stored |
| Upload via client | 20 MB | OK, 43.66 s |
| Upload via client | 5 × 1 MB | OK, about 11 s |
| Server-side PUT with curl | 20 MB | OK, 44.06 s, then downloaded by the client |
| Download via client | 20 MB | OK, 12.28 s |
| Delete via client | 12 files | OK |

## Speed accuracy

True rate is size divided by the PUT or GET duration from the log. `ss` figures sum
per-socket `bytes_sent` or `bytes_received` deltas.

| Direction | Duration | True rate | `ss` average | `ss` total / size | Smoothed error |
|---|---|---|---|---|---|
| Up, 20 MB | 43.66 s | 0.458 MB/s | 0.461 MB/s | 1.007 | -12% to +10%; mostly within ±3% once settled |
| Down, 20 MB | 12.28 s | 1.628 MB/s | 1.631 MB/s | 1.002 | -19% to +8% |

- Smoothing was simulated offline from the 1 s samples: 2 s deltas, averaged over 5.
- The download gave only four smoothed readings, so that range is thin evidence. Its worst
  value, -19%, was the final window, at the tail of the transfer.
- The 200 MB attempts ran at a steady 0.469 MB/s on `ss`, but they have no true rate to
  compare with because they never finished.
- Single 1 s readings ranged from 0 to 2.23 MB/s on the download, so smoothing is needed.
- Use `bytes_sent`, not `bytes_acked`, which stalled to 0.03 MB/s for a moment during upload.
- The client held 2 to 4 sockets. Between transfers most 1 s samples showed 0.00 MB/s.

```
ss -tinHp    # rows come in pairs: match pid=<client> on the first, counters on the second
```

## D-Bus

Name `com.nextcloudgmbh.Nextcloud`. Objects `/com/nextcloudgmbh/Nextcloud/Folder/N` and
`/com/nextcloudgmbh/Nextcloud/Provider`.

**`N` is not the `nextcloud.cfg` folder number.** Here `Folder/0` is `Folders\1` and
`Folder/1` is `Folders\3`. Match on `Path`, which has no trailing slash, while `localPath`
in the config does. `Name` can contain a slash: `Pictures/Screenshots`.

```
org.freedesktop.CloudProviders.Account  properties Icon s, Name s, Path s, Status i, StatusDetails s (all emit change)
org.gtk.Actions  Activate(sava{sv}) Describe(s)->(bgav) DescribeAll()->a{s(bgav)} List()->as SetState(sva{sv}); signal Changed(asa{sb}a{sv}a{s(bgav)})
org.gtk.Menus    Start(au)->a(uuaa{sv}) End(au); signal Changed
```

Actions: `logout quit pause openmaindialog opensettings openwebsite openfolder showfile
openrecentfile openhelp`.

```sh
B=com.nextcloudgmbh.Nextcloud; O=/com/nextcloudgmbh/Nextcloud/Folder/0
busctl --user get-property $B $O org.freedesktop.CloudProviders.Account Status StatusDetails
busctl --user call $B $O org.gtk.Actions Describe s pause     # (bgav) true "" 1 b false
busctl --user call $B $O org.gtk.Actions Activate sava{sv} pause 0 0    # toggles
dbus-monitor --session "type='signal',sender='$B'"
```

### Status integers

| Value | Meaning | Observed with |
|---|---|---|
| 1 | Idle | `Connected - Success`, `Connected - Success, some files were ignored.` |
| 2 | Syncing | `Connected - Syncing...` strings |
| 3 | Error **or paused** | `Connected - Error`, `Connected - Sync paused`, and `Syncing...` text while paused mid-transfer |

Status 0 was never seen. Status alone cannot separate error from paused, so read `Describe`.

### StatusDetails strings

Every string starts with `Connected - ` here. A disconnected string was not observed. Full
list: `tests/fixtures/statusdetails-strings.txt`.

| Shape | Example | Notes |
|---|---|---|
| Idle | `Connected - Success` | |
| Idle with failed file | `Connected - Success, some files were ignored.` | Only seen while a file was blacklisted |
| Size + ETA | `Connected - Syncing 191 MB (6 minutes 22 seconds left)` | Run total, not remaining |
| Size + ETA variants | `(1 second left)`, `(1 minute left)`, `(0 seconds left)` | |
| Size only | `Connected - Syncing 19 MB`, `Connected - Syncing 2 KB` | Start and end of a run |
| File count | `Connected - Syncing 2 of 12  (0 seconds left)` | **Two spaces** before `(`; seen on deletes |
| Zero count | `Connected - Syncing 0 of 0` | Discovery and no-op runs; treat as no numbers |
| Bare | `Connected - Syncing` | Seen just after resume mid-transfer |
| Checking | `Connected - Checking for changes in "<path>"` | Local discovery |
| Paused | `Connected - Sync paused` | |
| Error | `Connected - Error` | About 12 s, then the "ignored" string |

- Sizes are 1024-based but labelled MB: 200,000,000 bytes shows `191 MB`, 20,000,000 shows
  `19 MB`, and 5,000,000 shows `4.8 MB`.
- The first ETA of a run is often a false `(0 seconds left)`, and the last few seconds also
  read 0. The ETA moves around early on: 28, 36, 38, 30 s at the start of the 20 MB upload.
- `StatusDetails` changes on `Folder/0` signalled only on that object.

## Socket

Path `$XDG_RUNTIME_DIR/Nextcloud/socket`, newline-delimited. See
`tests/fixtures/socket-session.txt`.

- A new connection first receives `REGISTER_PATH:<path>` for each folder.
- The client also pushes `STATUS:` and `UPDATE_VIEW:` lines unprompted, including for files
  that were not asked about. Parse by prefix, and expect unrelated lines.
- Folder values seen: `OK`, `SYNC`, `IGNORE`. File values seen: `NOP`, `OK`, `SYNC`, `ERROR`,
  `IGNORE`. `NOP` means not known to the client.
- **`ERROR` was never seen on a folder.** With a failed file the folder reported `IGNORE`.
- **The socket does not show pause.** A paused folder still reported `STATUS:OK`.

`NEW` and `WARNING` were not observed.

## Client log at info level

Files are `~/.config/Nextcloud/logs/<timestamp>_nextcloud.log.N`, rotated and gzipped. While
active it rotated about once a minute, so a reader must follow the newest file. The directory
held 187 log files during this run. Lines are `date time:ms [ level category src:line ]:\tmsg`.
No credentials appeared in the logs (searched for authorization, bearer, app password, and
token patterns). `debug default` lines also appear despite the info level.

**File names are usable, and there are start lines as well as finish lines.**

| Event | Line |
|---|---|
| Run start | `>========== Sync started for folder [Documents] of account [...` |
| Run end | `<========== Sync finished for folder [Documents] of account [...` |
| Found, with size | `Processing "<rel>" \| (db/local/remote) \| valid: ... \| size: 0/20000000/0 \| ...` |
| Planned | `discovered "<rel>" CSyncEnums::CSYNC_INSTRUCTION_NEW OCC::SyncFileItem::Up CSyncEnums::ItemTypeFile` |
| File start | `Starting CSyncEnums::CSYNC_INSTRUCTION_NEW propagation of "<rel>" by OCC::PropagateUploadFileV1(0x...)` |
| Transfer end | `PUT of "<url>" FINISHED WITH STATUS "OK" QVariant(int, 201) ...` |
| File end | `Completed propagation of "<rel>" by OCC::OwncloudPropagator(0x...) with status OCC::SyncFileItem::Success` |
| File failed | `Could not complete propagation of "<rel>" by ... with status OCC::SyncFileItem::BlacklistedError and error: "..."` |
| Download job | `... propagation of "<rel>" by OCC::PropagateDownloadFile(0x...)` |

- **N of M can be derived.** M is `discovered` lines since `Sync started`, excluding
  `UPDATE_METADATA` (each transferred file gets one on the following run, with no transfer).
  N is `Completed propagation` lines. The 5-file run gave 5 `NEW` and 5 completions.
- **Directories also get `discovered` lines**, so filter on `ItemTypeFile`.
- **Current file can be several files.** Up to 3 uploads ran at once:
  ```
  21:56:09:828 Starting m5-0    21:56:12:806 Completed m5-0
  21:56:09:833 Starting m5-1    21:56:12:813 Starting m5-3
  21:56:09:836 Starting m5-2    21:56:16:041 Completed m5-3
  ```
- Log order did not match start order: `m5-3` and `m5-4` completed before `m5-1` and `m5-2`.
- Direction comes from `SyncFileItem::Up|Down` or the propagator class.
- **Exact bytes left may be derivable** from `Processing` sizes less completed files. This is
  an idea from the fields seen and was not built or tested.
- A helper started mid-run cannot recover M. It should show no count for that run.
- Run boundaries take 2 to 60 s to appear after a change. Local changes were picked up in
  about 2 s. A server change took 14.7 s and 54.3 s in the two controls.

## Pause and resume

Tested on idle `Pictures/Screenshots`, and on `Documents` for Sync now and mid-transfer.

| Moment | `Describe` | Status | StatusDetails | `paused=` in cfg |
|---|---|---|---|---|
| Before | `(bgav) true "" 1 b false` | 1 | `Connected - Success` | `false` |
| +0.2 s after pause | `(bgav) true "" 1 b true` | 3 | `Connected - Sync paused` | `true` |
| +0.2 s after resume | `(bgav) true "" 1 b false` | 2 | `Connected - Syncing 0 of 0` | `false` |
| +1.2 s after resume | `(bgav) true "" 1 b false` | 1 | `Connected - Success` | `false` |

- All took effect within 0.2 s, with no restart, and `Activate` returned nothing.
- `org.gtk.Actions` `Changed` signals carry the new state, so polling is not needed:
  `tests/fixtures/dbus-gtk-actions-changed.txt`.
- Persistence over a client restart was not tested.

### Sync now outcome: keep it

Resume on the idle folder logged a real run, the only one for that folder in the logs kept:

```
21:59:23:228 >========== Sync started for folder [Pictures/Screenshots] ...
21:59:23:854 <========== Sync finished for folder [Pictures/Screenshots] ...
```

Time for a server-side file to appear locally, on `Documents`:

| | Run 1 | Run 2 |
|---|---|---|
| Control, no action | 14.7 s | 54.3 s |
| Pause + resume straight after the PUT | 0.3 s | 0.5 s |

The two toggles ran back to back with no deliberate delay.

### Pausing does not stop a transfer in progress

A 10 MB upload was paused about 5 s into its PUT and resumed 2 s later:

```
22:02:16 before pause  Status 2  Connected - Syncing 9.5 MB
         paused        Status 3  Connected - Sync paused
+2 s                   Status 3  Connected - Syncing 9.5 MB (5 seconds left)
22:02:18 resumed       Status 2  Connected - Syncing
```

The log shows one `Starting` and one `PUT ... FINISHED WITH STATUS "OK"`, taking 21.8 s
(0.46 MB/s, unchanged). So the transfer was not aborted or restarted.

- A folder can read paused while still transferring, and `StatusDetails` can say `Syncing`
  while `Describe` says paused. Check `Describe` first.
- Sync now should be a no-op while Status is 2.
- Sync now must skip folders the user paused, or resume would be wrong for them. Toggle
  only folders whose `Describe` state is unpaused, and always toggle twice.
- Whether pause holds back queued files was not tested.

## Error states

During the failed uploads:

| Stage | Status | StatusDetails | Socket folder | Socket file |
|---|---|---|---|---|
| Uploading | 2 | `Connected - Syncing 191 MB (...)` | `SYNC` | `SYNC` |
| Attempt 1 just failed | 3 | `Connected - Error` | `IGNORE` | `ERROR` |
| Attempts 2 and 3 failed | 1 | `Connected - Success, some files were ignored.` | `IGNORE` | `ERROR` |
| Blacklisted | 1 | `Connected - Success, some files were ignored.` | `IGNORE` | `ERROR` |

**Status 3 cannot be relied on for errors.** `Connected - Error` appeared once, for about
12 s after attempt 1. Attempts 2 and 3 went straight from Status 2 to 1, which `dbus-monitor`
confirms. After that the folder looks idle, and only the "ignored" text, folder `IGNORE`, and
file `ERROR` remain. The error reason appears only in the log.

A symlink skipped with `FileIgnored` did not cause any of this: `Connected - Success` and
folder `OK` both held with those warnings in the log. Whether other harmless causes produce
folder `IGNORE` was not tested.

## Keyring credential

Six entries exist under schema `org.qt.keychain`:

| Label | Stored form |
|---|---|
| `<user>_app-password:<url>/:0@Nextcloud` | base64 of the app password |
| `<user>:<url>/:0@Nextcloud` | the same app password, raw |
| `Nextcloud` (4 entries) | 2 repeat the above; 2 not identified |

- The labels end with the server URL plus a trailing `/`, then `:0@Nextcloud`.
- The stored value is 96 characters. It decodes to 72 alphanumeric characters, and only the
  decoded value authenticates: stored value returned 401, decoded returned 200.
- The decoded value also happens to be valid base64, so decode exactly once.
- `secret-tool search --all` prints secrets, so always capture it and never echo it. This
  build printed no attribute lines, so select by `label`, not by attribute.

```python
out = subprocess.run(["secret-tool", "search", "--all", "xdg:schema", "org.qt.keychain"],
                     capture_output=True, text=True).stdout   # parse label/secret pairs
password = base64.b64decode(secret, validate=True).decode()
```

Auth was tested two ways, neither exposing the secret in arguments: `curl --config -` with
`user = "..."` on stdin, and stdlib `urllib` with an `Authorization: Basic` header.

## Server APIs

All OCS calls send `OCS-APIRequest: true`. Bad credentials return 401.

| Call | Result |
|---|---|
| `PROPFIND /remote.php/dav/files/alice/`, `Depth: 0`, `quota-used-bytes`, `quota-available-bytes` | 207 |
| `GET /ocs/v2.php/cloud/users/alice?format=json` | 200, `quota` object |
| `GET /ocs/v2.php/apps/activity/api/v2/activity?format=json&limit=8` | 200 |
| `GET /ocs/v2.php/apps/notifications/api/v2/notifications?format=json` | 200, 3 items |
| `GET /ocs/v2.php/cloud/capabilities?format=json` | 200 |
| `POST /ocs/v2.php/apps/files_sharing/api/v1/shares?format=json` | 200 |
| `DELETE .../shares/<id>?format=json` | 200; a later GET gives 404 |

- **Quota:** used 10,839,714,349 bytes, with available `-3` and total `-3`, relative `0`.
  `-3` means no limit, so the panel needs a "used only" display.
- **Activity:** one entry can cover several files. `subject` reads `You created a, b and c`,
  and `objects` maps file id to server path. `object_name` holds only one of them. Paths are
  server paths such as `/Sync/Documents/x`, so map through `targetPath`. Types seen:
  `file_created`, `file_deleted`, `shared`.
- **Activity has no direction.** It says created or deleted, not uploaded or downloaded, and
  it does not contain client sync errors. Fixture `ocs-activity.json` is filtered to test files.
- **Notifications:** the array length is the count. Existing items were app-password notices
  and a first-run prompt.
- **Share:** body `path=/Sync/Documents/x&shareType=3&permissions=1`, where `path` is the
  server path. The response has `url`, `token`, `id`, `expiration: null`, and
  **`permissions: 17`**, not 1. Missing path gives HTTP 404 with
  `Wrong path, file/folder does not exist`.
- Public links are enabled with no password or expiry enforced on this server.
- `PROPFIND` on the `uploads/<user>/` root returns 405, but `uploads/<user>/<id>` works.

## Not measured

- A completed transfer above 20 MB in either direction, or any completed chunked upload.
- The chunk size, and the file size where the client starts chunking.
- Where the 60 s limit lives, and why upload is so slow. Whether the server peer is on a
  direct or relayed tailnet path was not determined.
- Disconnected, client-quit, Tailscale-down, and server-down states and strings.
- Pause state across a client restart.
- Whether pause holds back queued files, and Sync now triggered during a sync (the widget
  avoids that case).
- Conflicts, and socket `NEW` or `WARNING` values.
- Status changes on `Folder/1` beyond the pause test.
- The `org.gtk.Menus` recent list during transfers.
- Applying the tray `hidden` setting (belongs to milestone 3).
- The helper's real parsers. Smoothing was simulated and log parsing was checked by search.

## Fixtures

Everything is in `tests/fixtures/`. IPs became `100.64.0.1` or `192.0.2.1`, the sync root
became `/Sync/`, and the share token is a placeholder.

| File | Contents |
|---|---|
| `statusdetails-strings.txt` | Every distinct string, including ETA variants |
| `sampler-up20.jsonl`, `sampler-dl20.jsonl`, `sampler-m5.jsonl` | 1 Hz samples of status, per-socket counters, socket messages |
| `sampler-200mb-failed-first-attempt.jsonl` | First 90 s of the failed upload |
| `client-log-excerpt.log` | Run, file, and failure lines, limited to test files |
| `ss-tinHp-client-idle.txt` | Raw `ss` output, idle (raw output was not kept during transfers) |
| `socket-session.txt` | Socket messages, assembled from observed lines |
| `dbus-gtk-actions-changed.txt` | `dbus-monitor` output for pause and resume |
| `nextcloud.cfg` | Reduced config |
| `webdav-quota.xml`, `ocs-activity.json`, `ocs-notifications.json`, `ocs-share-create.json` | API responses |
