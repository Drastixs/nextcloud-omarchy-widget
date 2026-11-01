import QtQuick
import QtQuick.Shapes
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// The state glyph, drawn natively like the Tailscale and Dropbox marks. Each
// state's mark is a hole cut from a filled cloud with odd-even fill, so it stays
// readable on any bar, transparent ones included, without knowing the backdrop.
Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Commons.Color.foreground
  property color badgeColor: Commons.Color.accent
  property string iconState: "unknown"
  property bool badge: false

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  // 24-unit outlines. The slash runs past the cloud, so odd-even fill paints it
  // solid outside the silhouette and cuts it through the middle.
  readonly property string cloudPath: "M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z"
  readonly property string slashMark: "M3.4 1.6L22.4 20.6L20.6 22.4L1.6 3.4z"
  readonly property string pauseMark: "M8.25 8.5h2.75v9h-2.75zM13 8.5h2.75v9H13z"
  readonly property string alertMark: "M10.6 7.25h2.8v6.75h-2.8zM10.6 15.25h2.8v2.75h-2.8z"
  readonly property string dotMark: "M12 9.75a3.25 3.25 0 1 0 0 6.5a3.25 3.25 0 1 0 0-6.5z"
  readonly property string upMark: "M12 7.25l4.75 5h-3.25v5.75h-3v-5.75H7.25z"
  readonly property string downMark: "M12 18l-4.75-5h3.25V7.25h3V13h3.25z"

  property bool arrowFrame: false

  readonly property string mark: {
    if (iconState === "client-down" || iconState === "disconnected" || iconState === "unknown") return slashMark
    if (iconState === "paused") return pauseMark
    if (iconState === "error") return alertMark
    if (iconState === "syncing") return arrowFrame ? downMark : upMark
    if (iconState === "warning") return dotMark
    return ""
  }

  Timer {
    interval: 650
    repeat: true
    running: root.visible && root.iconState === "syncing"
    onTriggered: root.arrowFrame = !root.arrowFrame
    onRunningChanged: if (!running) root.arrowFrame = false
  }

  Shape {
    anchors.fill: parent
    antialiasing: true
    layer.enabled: true
    layer.samples: 4

    ShapePath {
      fillColor: root.color
      fillRule: ShapePath.OddEvenFill
      strokeWidth: 0
      strokeColor: "transparent"
      scale: Qt.size(root.iconSize / 24, root.iconSize / 24)
      PathSvg { path: root.cloudPath + root.mark }
    }
  }

  // Unread server notifications.
  BorderSurface {
    visible: root.badge
    width: Math.max(6, root.iconSize * 0.36)
    height: width
    radius: width / 2
    color: root.badgeColor
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.rightMargin: -Math.round(width * 0.2)
    anchors.topMargin: -Math.round(width * 0.1)
    borderSpec: Border.flat(Commons.Color.popups.background, 1)
  }
}
