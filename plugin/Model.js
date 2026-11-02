// Pure helpers for the Nextcloud widget. Nothing here touches QML, so the
// parsing and formatting can be exercised with plain node.

var STATES = ["client-down", "disconnected", "paused", "error", "syncing", "warning", "synced"]

function num(value) {
  return typeof value === "number" && isFinite(value) ? value : null
}

function text(value) {
  return typeof value === "string" && value !== "" ? value : null
}

// Everything the panel reads exists from the first frame, and every number is
// null rather than 0 until the helper has actually reported it.
function emptyState() {
  return {
    state: "",
    summary: null,
    lastSyncedAt: null,
    client: { running: false, connected: false, apiAvailable: true },
    folders: [],
    transfer: normalizeTransfer(null),
    activity: [],
    server: normalizeServer(null),
    lastAction: null
  }
}

function normalizeTransfer(raw) {
  var t = raw || {}
  return {
    active: t.active === true,
    upBps: num(t.upBps),
    downBps: num(t.downBps),
    totalBytes: num(t.totalBytes),
    etaSec: num(t.etaSec),
    bytesLeft: num(t.bytesLeft),
    progress: num(t.progress),
    filesDone: num(t.filesDone),
    filesTotal: num(t.filesTotal),
    currentFile: text(t.currentFile),
    currentFileExtra: num(t.currentFileExtra),
    lastFinishedFile: text(t.lastFinishedFile)
  }
}

function normalizeServer(raw) {
  var s = raw || {}
  return {
    ok: s.ok === true,
    account: text(s.account),
    url: text(s.url),
    quotaUsed: num(s.quotaUsed),
    quotaTotal: num(s.quotaTotal),
    notifications: num(s.notifications),
    error: text(s.error)
  }
}

function normalizeList(raw, normalize) {
  var result = []
  if (!Array.isArray(raw)) return result
  for (var i = 0; i < raw.length; i++) {
    if (raw[i] && typeof raw[i] === "object") result.push(normalize(raw[i]))
  }
  return result
}

function normalizeFolder(raw) {
  return {
    name: String(raw.name || raw.path || "Folder"),
    path: text(raw.path),
    status: text(raw.status),
    paused: raw.paused === true
  }
}

function normalizeActivity(raw) {
  return {
    kind: text(raw.kind) || "",
    name: String(raw.name || "Untitled"),
    folder: text(raw.folder),
    path: text(raw.path),
    time: num(raw.time)
  }
}

function parseState(raw) {
  var data
  try {
    data = JSON.parse(String(raw || ""))
  } catch (e) {
    return { ok: false, error: "Unreadable line from helper" }
  }
  if (!data || typeof data !== "object") return { ok: false, error: "Unreadable line from helper" }
  if (data.schemaVersion !== 1) {
    return { ok: false, mismatch: true, error: "Helper protocol version does not match this widget" }
  }
  var client = data.client || {}
  return {
    ok: true,
    state: {
      state: STATES.indexOf(data.state) !== -1 ? data.state : "",
      summary: text(data.summary),
      lastSyncedAt: num(data.lastSyncedAt),
      client: {
        running: client.running === true,
        connected: client.connected === true,
        apiAvailable: client.apiAvailable !== false
      },
      folders: normalizeList(data.folders, normalizeFolder),
      transfer: normalizeTransfer(data.transfer),
      activity: normalizeList(data.activity, normalizeActivity),
      server: normalizeServer(data.server),
      lastAction: data.lastAction && typeof data.lastAction === "object" ? data.lastAction : null
    }
  }
}

function allPaused(folders) {
  if (!folders || folders.length === 0) return false
  for (var i = 0; i < folders.length; i++) {
    if (folders[i].paused !== true) return false
  }
  return true
}

function quotaPercent(server) {
  var used = server ? server.quotaUsed : null
  var total = server ? server.quotaTotal : null
  if (used === null || total === null || total <= 0) return null
  return used / total * 100
}

// The state the icon shows. "unknown" means there is no trustworthy reading,
// which must never fall through to "synced". pausedTarget is the pending
// optimistic pause: -1 none, 0 resuming, 1 pausing.
function displayState(status, alive, pausedTarget, quotaWarnPercent) {
  var state = status.state
  if (!alive || state === "") return "unknown"
  if (state === "client-down" || state === "disconnected") return state
  if (pausedTarget === 1) return "paused"
  if (pausedTarget === 0 && state === "paused") return "syncing"
  if (state === "synced") {
    var percent = quotaPercent(status.server)
    if (percent !== null && percent >= quotaWarnPercent) return "warning"
  }
  return state
}

function stateLabel(state, status) {
  if (state === "client-down") return "Nextcloud isn't running"
  if (state === "disconnected") return "Disconnected"
  if (state === "paused") return "Paused"
  if (state === "error") return "Sync problem"
  if (state === "syncing") return "Syncing…"
  if (state === "warning") return status && status.state === "synced" ? "Storage almost full" : "Needs attention"
  if (state === "synced") return "Up to date"
  return "Status unavailable"
}

function titleText(state, status, helperMissing, helperError) {
  if (helperMissing) return "Helper not found"
  if (helperError !== "") return "Helper problem"
  if (state === status.state && status.summary !== null) return status.summary
  return stateLabel(state, status)
}

function metaText(state, status, helperMissing, helperError, now) {
  if (helperMissing) return "Install nextcloud-status with the plugin"
  if (helperError !== "") return helperError
  if (state === "unknown") return "Waiting for helper"
  if (status.client.running && !status.client.apiAvailable) return "Client API unavailable"
  var age = formatAge(status.lastSyncedAt, now)
  if (age === "") return "Nextcloud"
  return age === "now" ? "Synced just now" : "Synced " + age + " ago"
}

function tooltipText(title, state, status, now) {
  var age = state === "synced" || state === "warning" ? formatAge(status.lastSyncedAt, now) : ""
  if (age === "") return title
  return title + " · " + (age === "now" ? "just now" : age + " ago")
}

function formatBytes(bytes) {
  var value = num(bytes)
  if (value === null) return ""
  // 1024-based with these labels, the same way the client reports sizes.
  var units = ["B", "KB", "MB", "GB", "TB", "PB"]
  var unit = 0
  while (Math.abs(value) >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  var rendered = unit === 0 || Math.abs(value) >= 100 ? String(Math.round(value)) : value.toFixed(1)
  return rendered.replace(/\.0$/, "") + " " + units[unit]
}

function formatSpeed(bps) {
  var size = formatBytes(bps)
  return size === "" ? "" : size + "/s"
}

function formatDuration(seconds) {
  var value = num(seconds)
  if (value === null) return ""
  value = Math.max(0, Math.round(value))
  if (value < 60) return value + "s"
  var minutes = Math.round(value / 60)
  if (minutes < 60) return minutes + "m"
  var hours = Math.floor(minutes / 60)
  var rest = minutes % 60
  return rest === 0 ? hours + "h" : hours + "h " + rest + "m"
}

function formatAge(time, now) {
  var value = num(time)
  if (value === null) return ""
  var seconds = Math.max(0, now - value)
  if (seconds < 60) return "now"
  if (seconds < 3600) return Math.floor(seconds / 60) + "m"
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h"
  return Math.floor(seconds / 86400) + "d"
}

// "~820 MB left · 4m". The amount left needs the ETA behind it, so without one
// the run's total size stands in, as the PRD specifies.
function transferLine(transfer) {
  var parts = []
  var eta = formatDuration(transfer.etaSec)
  if (transfer.bytesLeft !== null) {
    parts.push("~" + formatBytes(transfer.bytesLeft) + " left")
    if (eta !== "") parts.push(eta)
  } else if (eta !== "") {
    parts.push(eta + " left")
  } else if (transfer.totalBytes !== null && transfer.totalBytes > 0) {
    parts.push(formatBytes(transfer.totalBytes) + " total")
  }
  return parts.join(" · ")
}

function fileLine(transfer) {
  var parts = []
  if (transfer.filesDone !== null && transfer.filesTotal !== null && transfer.filesTotal > 0) {
    parts.push("File " + transfer.filesDone + " of " + transfer.filesTotal)
  }
  if (transfer.currentFile !== null) {
    var extra = transfer.currentFileExtra
    parts.push(transfer.currentFile + (extra !== null && extra > 0 ? " +" + extra + " more" : ""))
  } else if (transfer.lastFinishedFile !== null) {
    parts.push("Last finished: " + transfer.lastFinishedFile)
  }
  return parts.join(" · ")
}

function quotaLine(server) {
  if (server.quotaUsed === null) return ""
  if (server.quotaTotal === null || server.quotaTotal <= 0) return formatBytes(server.quotaUsed) + " used"
  return formatBytes(server.quotaUsed) + " of " + formatBytes(server.quotaTotal)
}

function notificationsLine(count) {
  if (count === null || count <= 0) return ""
  return count === 1 ? "1 notification" : count + " notifications"
}

function isCredentialError(server) {
  return !server.ok && /credential/i.test(server.error || "")
}

function isPinned(item) {
  return item.kind === "error" || item.kind === "conflict"
}

// Errors and conflicts first, then newest first, cut to the configured length.
function recentActivity(items, limit) {
  var sorted = (items || []).slice()
  sorted.sort(function(a, b) {
    var pinned = (isPinned(b) ? 1 : 0) - (isPinned(a) ? 1 : 0)
    if (pinned !== 0) return pinned
    return (b.time === null ? -1 : b.time) - (a.time === null ? -1 : a.time)
  })
  return sorted.slice(0, Math.max(0, limit))
}

function activityGlyph(kind) {
  if (kind === "error" || kind === "conflict") return "!"
  if (kind === "upload") return "↑"
  if (kind === "download") return "↓"
  if (kind === "delete") return "×"
  return "·"
}

function basename(path) {
  var parts = String(path || "").split("/")
  return parts[parts.length - 1] || ""
}

function dirname(path) {
  var value = String(path || "")
  var slash = value.lastIndexOf("/")
  return slash > 0 ? value.substring(0, slash) : ""
}

function fileUri(path) {
  var parts = String(path || "").split("/")
  for (var i = 0; i < parts.length; i++) parts[i] = encodeURIComponent(parts[i])
  return "file://" + parts.join("/")
}

function inSyncedFolder(path, folders) {
  for (var i = 0; i < folders.length; i++) {
    var root = String(folders[i].path || "").replace(/\/+$/, "")
    if (root !== "" && path.indexOf(root + "/") === 0) return true
  }
  return false
}

// The reason a path can't be shared, or "" when it can. A newline would let a
// file name smuggle a second command into the helper's stdin.
function shareRefusal(path, folders) {
  var value = String(path || "")
  if (value === "") return "No file selected"
  if (value.charAt(0) !== "/" || /[\r\n]/.test(value) || /(^|\/)\.\.(\/|$)/.test(value)) {
    return "That file name can't be shared"
  }
  if (!inSyncedFolder(value, folders)) return "Only files in synced folders can be shared"
  return ""
}

// file:///a%20b/plugin/ -> /a b/plugin
function localPath(url) {
  var value = String(url || "").replace(/^file:\/\//, "")
  return decodeURIComponent(value).replace(/\/+$/, "")
}

// The plugin directory is usually a symlink from ~/.config/omarchy/plugins into
// the repo, and Qt reports the link's path, not the target. readlink -f finds
// the real directory, so a helper bundled with a copied plugin and the repo's
// sibling helper/ both resolve. exec makes the helper the Process itself, so
// stdin, exit codes, and SIGTERM all reach it directly. 127 means not found.
var HELPER_LAUNCHER = 'real=$(readlink -f -- "$1") || exit 127; shift; '
  + 'for candidate in "$real/helper/nextcloud-status" "$real/nextcloud-status" "$real/../helper/nextcloud-status"; do '
  + '[ -f "$candidate" ] || continue; '
  + '[ -x "$candidate" ] && exec "$candidate" "$@"; '
  + 'exec python3 "$candidate" "$@"; '
  + 'done; '
  + 'echo "nextcloud-status not found near $real" >&2; exit 127'

function helperCommand(pluginDir, activityCount, quotaWarnPercent, serverRefreshSec) {
  return ["bash", "-c", HELPER_LAUNCHER, "nextcloud-status", pluginDir,
    "--activity-count", String(activityCount),
    "--quota-warn-percent", String(quotaWarnPercent),
    "--server-refresh", String(serverRefreshSec)]
}

function nextBackoffMs(currentMs) {
  return Math.min(currentMs * 2, 60000)
}

if (typeof module !== "undefined") {
  module.exports = {
    emptyState: emptyState,
    parseState: parseState,
    allPaused: allPaused,
    quotaPercent: quotaPercent,
    displayState: displayState,
    stateLabel: stateLabel,
    titleText: titleText,
    metaText: metaText,
    tooltipText: tooltipText,
    formatBytes: formatBytes,
    formatSpeed: formatSpeed,
    formatDuration: formatDuration,
    formatAge: formatAge,
    transferLine: transferLine,
    fileLine: fileLine,
    quotaLine: quotaLine,
    notificationsLine: notificationsLine,
    isCredentialError: isCredentialError,
    isPinned: isPinned,
    recentActivity: recentActivity,
    activityGlyph: activityGlyph,
    basename: basename,
    dirname: dirname,
    fileUri: fileUri,
    shareRefusal: shareRefusal,
    localPath: localPath,
    helperCommand: helperCommand,
    nextBackoffMs: nextBackoffMs
  }
}
