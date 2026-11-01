import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})

  // The helper's latest full state, normalized. Unknown numbers stay null.
  property var status: Model.emptyState()
  readonly property var client: status.client
  readonly property var folders: status.folders
  readonly property var transfer: status.transfer
  readonly property var server: status.server

  property bool helperRunning: false
  property bool helperMissing: false
  property string helperError: ""
  // Set by the first valid state after a start. Until then the state is unknown,
  // so a stale reading can never survive a helper restart as "synced".
  property bool helperReady: false
  readonly property bool alive: helperRunning && helperReady

  // Optimistic pause so the icon reacts the instant you click, rather than
  // waiting for the next state line. _desired is -1 while we just follow the
  // real state, or 0/1 while a resume or pause is still catching up.
  property int _desired: -1
  readonly property bool allPaused: Model.allPaused(folders)
  readonly property bool paused: _desired === -1 ? allPaused : (_desired === 1)

  property string actionStatus: ""
  property string lastError: ""
  property double nowSec: Date.now() / 1000

  readonly property int activityCount: intSetting("activityCount", 8, 1, 25)
  readonly property bool showNotificationBadge: setting("showNotificationBadge", true) !== false
  readonly property int quotaWarnPercent: intSetting("quotaWarnPercent", 90, 50, 100)
  readonly property int serverRefreshSec: intSetting("serverRefreshSec", 60, 15, 3600)

  readonly property string view: Model.displayState(status, alive, _desired, quotaWarnPercent)
  readonly property string titleText: Model.titleText(view, status, helperMissing, helperError)
  readonly property string metaText: Model.metaText(view, status, helperMissing, helperError, nowSec)
  readonly property string tooltipText: Model.tooltipText(titleText, view, status, nowSec)
  readonly property var activity: Model.recentActivity(status.activity, activityCount)
  readonly property real quotaPercent: { var p = Model.quotaPercent(server); return p === null ? -1 : p }
  readonly property bool quotaHigh: quotaPercent >= quotaWarnPercent
  readonly property bool badge: showNotificationBadge && server.notifications !== null && server.notifications > 0

  readonly property bool canPause: alive && client.running && client.apiAvailable && folders.length > 0
  readonly property bool canSync: canPause && client.connected && !paused && view !== "syncing"
  readonly property bool canShare: alive && folders.length > 0 && !sharePicker.running
  readonly property bool canOpenSettings: alive && client.running
  readonly property bool canOpenWeb: /^https?:\/\//.test(server.url || "")
  readonly property string pluginDir: Model.localPath(Qt.resolvedUrl("."))

  property bool _stopping: false
  property bool _restarting: false
  property int _backoffMs: 1000
  property double _startedMs: 0
  property string _lastActionKey: ""
  property string _pendingCommand: ""

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, min, max) {
    var n = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(n)) n = fallback
    if (n < min) n = min
    if (n > max) n = max
    return n
  }

  function startHelper() {
    if (_stopping || helperProcess.running) return
    helperError = ""
    _startedMs = Date.now()
    helperProcess.running = true
  }

  function applyLine(line) {
    var parsed = Model.parseState(line)
    if (!parsed.ok) {
      console.warn("nextcloud-widget", parsed.error)
      if (parsed.mismatch) {
        helperReady = false
        status = Model.emptyState()
        helperError = parsed.error
      }
      return
    }
    status = parsed.state
    helperError = ""
    helperMissing = false
    helperReady = true
    // Reality caught up to the pending pause/resume — stop overriding.
    if (_desired !== -1 && allPaused === (_desired === 1)) {
      _desired = -1
      desiredTimer.stop()
    }
    handleLastAction(status.lastAction)
  }

  // A state line repeats lastAction until the next command, so only a change
  // counts as a new result.
  function handleLastAction(action) {
    var key = action ? JSON.stringify(action) : ""
    if (key === _lastActionKey) return
    _lastActionKey = key
    if (!action) return

    var command = String(action.command || "")
    var ok = action.ok === true
    var message = String(action.message || "")
    if (_pendingCommand === command) {
      _pendingCommand = ""
      pendingTimer.stop()
    }
    if (!ok && (command === "pause" || command === "resume")) _desired = -1
    // The helper announces a copied link itself; only a failure is left to say.
    if (command === "share" && !ok) notify("Could not share file", message, true)
    if (!ok) flash(message || "Nextcloud command failed", true)
    else if (message !== "") flash(message, false)
    else if (actionStatus !== "" && !actionStatusTimer.running) actionStatus = ""
  }

  function flash(message, isError) {
    actionStatus = isError ? "" : message
    lastError = isError ? message : ""
    actionStatusTimer.restart()
  }

  function notify(headline, description, critical) {
    var command = ["omarchy-notification-send", "-g", "󰅟"]
    if (critical) command.push("-u", "critical")
    command.push(headline, description)
    Quickshell.execDetached(command)
  }

  function send(command) {
    if (!alive) {
      flash("Nextcloud helper isn't running", true)
      return false
    }
    helperProcess.write(command + "\n")
    return true
  }

  function refresh() {
    if (alive) send("refresh")
  }

  function togglePause() {
    if (!canPause) return
    var next = paused ? 0 : 1
    if (!send(next === 1 ? "pause" : "resume")) return
    _desired = next
    desiredTimer.restart()
  }

  function syncNow() {
    if (canSync) send("sync")
  }

  function openSettings() {
    if (canOpenSettings) send("settings")
  }

  function openWeb() {
    if (canOpenWeb) Quickshell.execDetached(["omarchy-launch-browser", server.url])
  }

  function openPath(path) {
    if (path) Quickshell.execDetached(["uwsm-app", "--", "xdg-open", path])
  }

  function revealPath(path) {
    if (path) Quickshell.execDetached(["uwsm-app", "--", "nautilus", "--select", Model.fileUri(path)])
  }

  function shareFile(item) {
    if (!item) return
    if (item.kind === "delete") {
      flash("Deleted files can't be shared", true)
      return
    }
    sharePath(item.path)
  }

  function sharePath(path) {
    var refusal = Model.shareRefusal(path, folders)
    if (refusal !== "") {
      flash(refusal, true)
      notify("Could not share file", refusal, false)
      return
    }
    if (!send("share " + path)) return
    actionStatus = "Creating share link…"
    lastError = ""
    actionStatusTimer.stop()
    _pendingCommand = "share"
    pendingTimer.restart()
  }

  function pickShareFile() {
    if (canShare) sharePicker.running = true
  }

  function elideStatus(text) {
    var value = String(text || "").replace(/\s+/g, " ").trim()
    return value.length > 140 ? value.substring(0, 137) + "…" : value
  }

  // The helper takes its settings as arguments, so a change means a fresh one.
  onActivityCountChanged: settingsRestart.restart()
  onQuotaWarnPercentChanged: settingsRestart.restart()
  onServerRefreshSecChanged: settingsRestart.restart()
  Component.onDestruction: _stopping = true

  Timer {
    // Waits out the settings the host assigns just after creation, so the
    // helper's first start already carries them.
    id: settingsRestart
    interval: 200
    running: true
    onTriggered: {
      if (!helperProcess.running) {
        root.startHelper()
        return
      }
      root._restarting = true
      helperProcess.running = false
    }
  }

  Timer {
    id: restartTimer
    repeat: false
    onTriggered: root.startHelper()
  }

  Timer {
    // A pause that never lands must not leave the icon lying about it.
    id: desiredTimer
    interval: 5000
    repeat: false
    onTriggered: root._desired = -1
  }

  Timer {
    id: pendingTimer
    interval: 20000
    repeat: false
    onTriggered: {
      root._pendingCommand = ""
      root.flash("No reply from the Nextcloud helper", true)
    }
  }

  Timer {
    id: actionStatusTimer
    interval: 4000
    repeat: false
    onTriggered: {
      root.actionStatus = ""
      root.lastError = ""
    }
  }

  Timer {
    interval: 20000
    repeat: true
    running: true
    onTriggered: root.nowSec = Date.now() / 1000
  }

  Process {
    id: helperProcess
    running: false
    stdinEnabled: true
    command: Model.helperCommand(root.pluginDir, root.activityCount, root.quotaWarnPercent, root.serverRefreshSec)
    stdout: SplitParser { onRead: function(data) { root.applyLine(data) } }
    stderr: SplitParser { onRead: function(data) { console.warn("nextcloud-status:", root.elideStatus(data)) } }
    onStarted: root.helperRunning = true
    onExited: function(exitCode) {
      root.helperRunning = false
      root.helperReady = false
      root._desired = -1
      root._pendingCommand = ""
      root._lastActionKey = ""
      pendingTimer.stop()
      root.status = Model.emptyState()
      if (root._stopping) return
      if (root._restarting) {
        root._restarting = false
        root.startHelper()
        return
      }
      root.helperMissing = exitCode === 127
      // A run that lasted has earned a quick retry; a crash loop backs off.
      if (Date.now() - root._startedMs > 30000) root._backoffMs = 1000
      restartTimer.interval = root._backoffMs
      restartTimer.restart()
      root._backoffMs = Model.nextBackoffMs(root._backoffMs)
    }
  }

  Process {
    id: sharePicker
    running: false
    command: ["omarchy-file-select", "--title", "Share a Nextcloud file"]
    stdout: StdioCollector { id: sharePickerStdout; waitForEnd: true }
    onExited: function(exitCode) {
      var path = String(sharePickerStdout.text || "").replace(/\n$/, "")
      if (exitCode === 0 && path !== "") root.sharePath(path)
      else if (exitCode > 1) root.notify("Could not share file", "The file chooser did not open", true)
    }
  }
}
