import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "nextcloud.widget"
  ipcTarget: "nextcloud.widget"
  manageIpc: false

  property string focusSection: "header"
  property int activityIndex: 0
  property int actionIndex: 0
  property int folderIndex: 0
  property bool cursorActive: false
  property bool folderListOpen: false

  readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground
  readonly property color urgent: bar ? bar.urgent : Commons.Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool iconDimmed: nextcloud.view === "client-down" || nextcloud.view === "unknown"
  readonly property color iconColor: nextcloud.view === "error" ? urgent : (iconDimmed ? dim : foreground)
  readonly property color barIconColor: nextcloud.view === "error" ? urgent : (iconDimmed ? Qt.darker(barForeground, 1.55) : barForeground)
  readonly property bool headerHasCursor: cursorActive && focusSection === "header"
  readonly property bool showTransfer: nextcloud.alive && (nextcloud.transfer.active || nextcloud.view === "syncing")
  readonly property bool credentialsMissing: Model.isCredentialError(nextcloud.server)
  readonly property bool showRecent: nextcloud.alive && !credentialsMissing
  readonly property int settingsAction: 4
  readonly property var actions: [
    { id: "sync", label: "Sync now", glyph: "󰑐", enabled: nextcloud.canSync },
    { id: "open", label: "Open folder", glyph: "󰝰", enabled: nextcloud.folders.length > 0 },
    { id: "web", label: "Web", glyph: "󰖟", enabled: nextcloud.canOpenWeb },
    { id: "share", label: "Share…", glyph: "󰌹", enabled: nextcloud.canShare },
    { id: "settings", label: "Client settings", glyph: "󰒓", enabled: nextcloud.canOpenSettings }
  ]

  function ensureCursor() {
    if (activityIndex >= nextcloud.activity.length) activityIndex = Math.max(0, nextcloud.activity.length - 1)
    if (folderIndex >= nextcloud.folders.length) folderIndex = Math.max(0, nextcloud.folders.length - 1)
    if (nextcloud.folders.length < 2) folderListOpen = false
    if (focusSection === "activity" && nextcloud.activity.length === 0) focusSection = "header"
    if (focusSection === "folders" && !folderListOpen) focusSection = "actions"
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    ensureCursor()
    if (dx !== 0 && focusSection === "actions" && actionIndex < settingsAction) {
      actionIndex = Math.max(0, Math.min(settingsAction - 1, actionIndex + dx))
    }
    if (dy > 0) moveDown()
    else if (dy < 0) moveUp()
    ensureCursor()
    scrollCursorIntoView()
  }

  function moveDown() {
    if (focusSection === "header") {
      if (nextcloud.activity.length > 0) setActivityCursor(0)
      else setActionCursor(0)
    } else if (focusSection === "activity") {
      if (activityIndex < nextcloud.activity.length - 1) activityIndex++
      else setActionCursor(0)
    } else if (focusSection === "actions") {
      if (actionIndex < settingsAction) actionIndex = settingsAction
      else if (folderListOpen) setFolderCursor(0)
    } else if (focusSection === "folders" && folderIndex < nextcloud.folders.length - 1) {
      folderIndex++
    }
  }

  function moveUp() {
    if (focusSection === "activity") {
      if (activityIndex > 0) activityIndex--
      else setHeaderCursor()
    } else if (focusSection === "actions") {
      if (actionIndex === settingsAction) actionIndex = 0
      else if (nextcloud.activity.length > 0) setActivityCursor(nextcloud.activity.length - 1)
      else setHeaderCursor()
    } else if (focusSection === "folders") {
      if (folderIndex > 0) folderIndex--
      else setActionCursor(settingsAction)
    }
  }

  function activateCursor() {
    ensureCursor()
    if (focusSection === "header") nextcloud.togglePause()
    else if (focusSection === "activity") openItem(selectedFile())
    else if (focusSection === "actions") runAction(actions[actionIndex])
    else if (focusSection === "folders") openFolder(nextcloud.folders[folderIndex])
  }

  function selectedFile() {
    if (!cursorActive || focusSection !== "activity" || nextcloud.activity.length === 0) return null
    return nextcloud.activity[Math.max(0, Math.min(activityIndex, nextcloud.activity.length - 1))]
  }

  function runAction(action) {
    if (!action || !action.enabled) return
    if (action.id === "sync") nextcloud.syncNow()
    else if (action.id === "open") openFolders()
    else if (action.id === "web") openWeb()
    else if (action.id === "share") pickShareFile()
    else if (action.id === "settings") nextcloud.openSettings()
  }

  function openFolders() {
    if (nextcloud.folders.length === 1) {
      openFolder(nextcloud.folders[0])
      return
    }
    folderListOpen = !folderListOpen
    folderIndex = 0
    if (folderListOpen && cursorActive) setFolderCursor(0)
    else ensureCursor()
  }

  function openFolder(folder) {
    if (!folder || !folder.path) return
    nextcloud.openPath(folder.path)
    close()
  }

  // The deleted file is gone, so its folder is the nearest thing to open.
  function openItem(item) {
    if (!item || !item.path) return
    if (item.kind === "delete") nextcloud.openPath(Model.dirname(item.path))
    else nextcloud.openPath(item.path)
    close()
  }

  function openSelected() {
    var item = selectedFile()
    if (!item) {
      runAction(actions[1])
    } else if (item.path) {
      if (item.kind === "delete") nextcloud.openPath(Model.dirname(item.path))
      else nextcloud.revealPath(item.path)
      close()
    }
  }

  // With no file under the cursor there is nothing to act on but the picker.
  function shareSelected() {
    var item = selectedFile()
    if (item) nextcloud.shareFile(item)
    else pickShareFile()
  }

  function openWeb() {
    if (!nextcloud.canOpenWeb) return
    nextcloud.openWeb()
    close()
  }

  // The file picker takes over from here, so get the panel out of the way.
  function pickShareFile() {
    if (!nextcloud.canShare) return
    nextcloud.pickShareFile()
    close()
  }

  function handleClose() {
    if (folderListOpen) {
      folderListOpen = false
      ensureCursor()
    } else {
      close()
    }
  }

  function scrollItemIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function() {
      if (!item) return
      var margin = Style.space(6)
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var viewTop = panelFlick.contentY
      var viewBottom = viewTop + panelFlick.height
      var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < viewTop + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > viewBottom - margin) panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  function scrollCursorIntoView() {
    if (focusSection === "header") panelFlick.contentY = 0
    else if (focusSection === "activity" && activityIndex < activityColumn.children.length) scrollItemIntoView(activityColumn.children[activityIndex])
    else if (focusSection === "actions") scrollItemIntoView(actionIndex === settingsAction ? settingsChip : actionRow)
    else if (focusSection === "folders" && folderIndex < folderColumn.children.length) scrollItemIntoView(folderColumn.children[folderIndex])
  }

  function setHeaderCursor() {
    cursorActive = true
    focusSection = "header"
  }

  function setActivityCursor(index) {
    cursorActive = true
    focusSection = "activity"
    activityIndex = index
  }

  function setActionCursor(index) {
    cursorActive = true
    focusSection = "actions"
    actionIndex = index
  }

  function setFolderCursor(index) {
    cursorActive = true
    focusSection = "folders"
    folderIndex = index
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    folderListOpen = false
    if (panelFlick) panelFlick.contentY = 0
    nextcloud.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service {
    id: nextcloud
    settings: root.settings
  }

  Connections {
    target: nextcloud
    function onActivityChanged() { root.ensureCursor() }
    function onStatusChanged() { root.ensureCursor() }
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { nextcloud.refresh(); return "ok" }
    function togglePause(): string { nextcloud.togglePause(); return "ok" }
    function status(): string { return nextcloud.titleText }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: nextcloud.tooltipText
    iconComponent: Component {
      Item {
        NextcloudIcon {
          anchors.centerIn: parent
          iconSize: Style.space(14)
          color: root.barIconColor
          iconState: nextcloud.view
          badge: nextcloud.badge
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) nextcloud.togglePause()
      else if (buttonCode === Qt.MiddleButton) nextcloud.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.handleClose()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "o" || t === "O") root.openSelected()
        else if (t === "s" || t === "S") root.shareSelected()
        else if (t === "p" || t === "P") nextcloud.togglePause()
        else if (t === "y" || t === "Y") nextcloud.syncNow()
        else if (t === "w" || t === "W") root.openWeb()
        else if (t === "r" || t === "R") nextcloud.refresh()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          Item {
            id: header
            width: parent.width
            implicitHeight: hero.implicitHeight
            // Exposed for the hero's trailingControl, whose `root` resolves to
            // PanelHero (not this Panel) — reach panel state via `header`.
            readonly property bool ringVisible: root.headerHasCursor
            function focusHero() { root.setHeaderCursor() }

            PanelHero {
              id: hero
              width: parent.width
              title: nextcloud.titleText
              meta: nextcloud.metaText
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconComponent: Component {
                NextcloudIcon {
                  iconSize: Style.font.display
                  color: root.iconColor
                  iconState: nextcloud.view
                }
              }

              trailingControl: Component {
                PanelActionButton {
                  iconText: nextcloud.paused ? "󰐊" : "󰏤"
                  tooltipText: nextcloud.paused ? "Resume syncing" : "Pause syncing"
                  enabled: nextcloud.canPause
                  hasCursor: header.ringVisible
                  foreground: hero.foreground
                  fontFamily: hero.fontFamily
                  onHovered: function(on) { if (on) header.focusHero() }
                  onClicked: nextcloud.togglePause()
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: nextcloud.actionStatus !== "" || nextcloud.lastError !== ""
            width: parent.width
            text: nextcloud.actionStatus !== "" ? nextcloud.actionStatus : nextcloud.lastError
            color: nextcloud.lastError !== "" && nextcloud.actionStatus === "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          PanelSeparator {
            visible: root.showTransfer
            foreground: root.foreground
          }

          Column {
            visible: root.showTransfer
            width: parent.width
            spacing: Style.space(8)

            Row {
              spacing: Style.space(14)
              visible: nextcloud.transfer.upBps !== null || nextcloud.transfer.downBps !== null

              DetailText {
                visible: nextcloud.transfer.upBps !== null
                text: "↑ " + Model.formatSpeed(nextcloud.transfer.upBps)
                color: root.foreground
              }

              DetailText {
                visible: nextcloud.transfer.downBps !== null
                text: "↓ " + Model.formatSpeed(nextcloud.transfer.downBps)
                color: root.foreground
              }
            }

            RowLayout {
              width: parent.width
              spacing: Style.space(10)

              Meter {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                value: nextcloud.transfer.progress
                animated: root.opened
              }

              DetailText {
                visible: text !== ""
                text: Model.transferLine(nextcloud.transfer)
                Layout.alignment: Qt.AlignVCenter
              }
            }

            DetailText {
              width: parent.width
              visible: text !== ""
              text: Model.fileLine(nextcloud.transfer)
              elide: Text.ElideMiddle
            }
          }

          PanelSeparator {
            visible: root.showRecent
            foreground: root.foreground
          }

          Column {
            visible: root.showRecent
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              text: "RECENT"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            DetailText {
              visible: nextcloud.activity.length === 0
              width: parent.width
              text: nextcloud.server.ok ? "No recent activity" : "Activity unavailable"
              horizontalAlignment: Text.AlignHCenter
            }

            Column {
              id: activityColumn
              visible: nextcloud.activity.length > 0
              width: parent.width
              spacing: Style.space(2)

              Repeater {
                model: nextcloud.activity
                ActivityRow {
                  required property var modelData
                  required property int index
                  width: activityColumn.width
                  item: modelData
                  rowIndex: index
                }
              }
            }
          }

          PanelSeparator {
            visible: nextcloud.alive
            foreground: root.foreground
          }

          // Greys out as a block on a server error; sync status above is
          // unaffected. Missing credentials collapse it to the one line.
          Column {
            visible: nextcloud.alive
            width: parent.width
            spacing: Style.space(6)
            opacity: nextcloud.server.ok ? 1.0 : 0.6

            DetailText {
              visible: root.credentialsMissing
              text: "Can't read credentials"
            }

            Text {
              textFormat: Text.PlainText
              visible: !root.credentialsMissing && text !== ""
              width: parent.width
              text: nextcloud.server.account || ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              elide: Text.ElideMiddle
            }

            Meter {
              visible: !root.credentialsMissing && nextcloud.quotaPercent >= 0
              width: parent.width
              value: Math.min(1, Math.max(0, nextcloud.quotaPercent / 100))
              barColor: nextcloud.quotaHigh ? root.urgent : root.foreground
            }

            DetailText {
              visible: !root.credentialsMissing && text !== ""
              text: Model.quotaLine(nextcloud.server)
            }

            DetailText {
              visible: !root.credentialsMissing && text !== ""
              text: Model.notificationsLine(nextcloud.server.notifications)
            }

            DetailText {
              visible: !root.credentialsMissing && !nextcloud.server.ok && text !== ""
              width: parent.width
              text: nextcloud.server.error || ""
              wrapMode: Text.WordWrap
            }
          }

          PanelSeparator {
            foreground: root.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            RowLayout {
              id: actionRow
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: root.actions.slice(0, root.settingsAction)
                ActionChip {
                  required property var modelData
                  required property int index
                  Layout.fillWidth: true
                  Layout.preferredWidth: implicitWidth
                  action: modelData
                  rowIndex: index
                }
              }
            }

            ActionChip {
              id: settingsChip
              width: parent.width
              action: root.actions[root.settingsAction]
              rowIndex: root.settingsAction
            }

            Column {
              id: folderColumn
              visible: root.folderListOpen
              width: parent.width
              spacing: Style.space(2)

              Repeater {
                model: root.folderListOpen ? nextcloud.folders : []
                FolderRow {
                  required property var modelData
                  required property int index
                  width: folderColumn.width
                  folder: modelData
                  rowIndex: index
                }
              }
            }
          }
        }
      }
    }
  }

  component DetailText: Text {
    textFormat: Text.PlainText
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    elide: Text.ElideRight
  }

  // A thin bar. A null value means no estimate yet, so a block slides along it
  // rather than showing a made-up amount.
  component Meter: Item {
    id: meter
    property var value: null
    property bool animated: true
    property color barColor: root.foreground
    property real slide: 0
    readonly property bool indeterminate: value === null

    implicitWidth: Style.space(120)
    implicitHeight: Math.max(3, Style.space(4))
    clip: true

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius > 0 ? height / 2 : 0
      color: Util.alpha(root.foreground, 0.16)
    }

    Rectangle {
      x: meter.indeterminate ? meter.slide * meter.width * 0.75 : 0
      width: meter.width * (meter.indeterminate ? 0.25 : Math.max(0, Math.min(1, Number(meter.value))))
      height: parent.height
      radius: Style.cornerRadius > 0 ? height / 2 : 0
      color: meter.barColor
    }

    SequentialAnimation on slide {
      running: meter.indeterminate && meter.animated && meter.visible
      loops: Animation.Infinite
      NumberAnimation { from: 0; to: 1; duration: 900; easing.type: Easing.InOutQuad }
      NumberAnimation { from: 1; to: 0; duration: 900; easing.type: Easing.InOutQuad }
    }
  }

  component ActivityRow: CursorSurface {
    id: activityRow
    property var item: null
    property int rowIndex: 0
    readonly property bool pinned: item ? Model.isPinned(item) : false

    hasCursor: root.cursorActive && root.focusSection === "activity" && root.activityIndex === rowIndex
    foreground: root.foreground
    implicitHeight: activityInner.implicitHeight + Style.spacing.lg

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setActivityCursor(activityRow.rowIndex)
      onClicked: root.openItem(activityRow.item)
    }

    RowLayout {
      id: activityInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: Model.activityGlyph(activityRow.item ? activityRow.item.kind : "")
        color: activityRow.pinned ? root.urgent : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: activityRow.pinned
        horizontalAlignment: Text.AlignHCenter
        Layout.preferredWidth: Style.space(14)
        Layout.alignment: Qt.AlignVCenter
      }

      Text {
        textFormat: Text.PlainText
        text: activityRow.item ? activityRow.item.name : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideMiddle
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignVCenter
      }

      DetailText {
        visible: text !== ""
        text: activityRow.item && activityRow.item.folder ? activityRow.item.folder : ""
        Layout.maximumWidth: Style.space(110)
        Layout.alignment: Qt.AlignVCenter
      }

      DetailText {
        visible: text !== ""
        text: Model.formatAge(activityRow.item ? activityRow.item.time : null, nextcloud.nowSec)
        horizontalAlignment: Text.AlignRight
        Layout.minimumWidth: Style.space(28)
        Layout.alignment: Qt.AlignVCenter
      }
    }
  }

  component ActionChip: CursorSurface {
    id: chip
    property var action: null
    property int rowIndex: 0
    readonly property bool usable: action ? action.enabled === true : false

    hasCursor: root.cursorActive && root.focusSection === "actions" && root.actionIndex === rowIndex
    current: action && action.id === "open" && root.folderListOpen
    bordered: true
    foreground: root.foreground
    opacity: usable ? 1.0 : 0.45
    implicitWidth: chipInner.implicitWidth + Style.space(12)
    implicitHeight: chipInner.implicitHeight + Style.spacing.lg

    Row {
      id: chipInner
      anchors.centerIn: parent
      spacing: Style.space(5)

      Text {
        textFormat: Text.PlainText
        text: chip.action ? chip.action.glyph : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        textFormat: Text.PlainText
        text: chip.action ? chip.action.label : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: chip.usable ? Qt.PointingHandCursor : Qt.ArrowCursor
      onEntered: root.setActionCursor(chip.rowIndex)
      onClicked: root.runAction(chip.action)
    }
  }

  component FolderRow: CursorSurface {
    id: folderRow
    property var folder: null
    property int rowIndex: 0

    hasCursor: root.cursorActive && root.focusSection === "folders" && root.folderIndex === rowIndex
    foreground: root.foreground
    implicitHeight: folderInner.implicitHeight + Style.spacing.lg

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setFolderCursor(folderRow.rowIndex)
      onClicked: root.openFolder(folderRow.folder)
    }

    RowLayout {
      id: folderInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: "󰝰"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        Layout.alignment: Qt.AlignVCenter
      }

      Text {
        textFormat: Text.PlainText
        text: folderRow.folder ? folderRow.folder.name : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideMiddle
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignVCenter
      }

      DetailText {
        visible: folderRow.folder ? folderRow.folder.paused : false
        text: "Paused"
        Layout.alignment: Qt.AlignVCenter
      }
    }
  }
}
