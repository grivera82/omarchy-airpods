import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Ui
import qs.Commons

// AirPods panel. The Bluetooth/AAP session lives in Service.qml (one per
// session); this widget renders its state and sends commands through it.
Panel {
  id: root
  moduleName: "grivera.airpods"
  ipcTarget: "grivera.airpods"

  readonly property var svc: root.bar && root.bar.shell ? root.bar.shell.serviceFor("grivera.airpods") : null
  readonly property var pods: svc ? svc.pods : ({})
  readonly property string status: svc ? svc.status : "offline"
  readonly property bool connected: svc ? svc.connected : false
  readonly property bool hasControl: svc ? svc.hasControl : false
  readonly property var device: svc ? svc.device : null
  readonly property var battery: svc ? svc.battery : ({})
  readonly property var pairing: svc ? svc.pairing : null
  readonly property var modes: device && device.modes ? device.modes : []
  readonly property bool busy: status === "connecting"

  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.4)
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.fontFamily

  readonly property string glyphOn: "󱡏"
  readonly property string glyphOff: "󱡑"
  readonly property string glyphBolt: "󰉁"

  readonly property var modeInfo: ({
    "off": { label: "Off", glyph: "󰋋" },
    "transparency": { label: "Transparency", glyph: "󰟅" },
    "adaptive": { label: "Adaptive", glyph: "󱑽" },
    "anc": { label: "Noise Cancel", glyph: "󰟎" }
  })

  readonly property var batteryParts: {
    var order = battery.single ? ["single", "case"] : ["left", "right", "case"]
    var out = []
    for (var i = 0; i < order.length; i++)
      if (battery[order[i]]) out.push({ part: order[i], level: battery[order[i]].level, charging: battery[order[i]].charging })
    return out
  }

  readonly property int lowestBud: {
    var low = 101
    for (var i = 0; i < batteryParts.length; i++)
      if (batteryParts[i].part !== "case" && !batteryParts[i].charging) low = Math.min(low, batteryParts[i].level)
    return low
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function partLabel(part) {
    return ({ left: "Left", right: "Right", case: "Case", single: "AirPods" })[part] || part
  }

  function batterySummary() {
    var bits = []
    for (var i = 0; i < batteryParts.length; i++) {
      var p = batteryParts[i]
      bits.push(partLabel(p.part).charAt(0) + " " + p.level + "%" + (p.charging ? "+" : ""))
    }
    return bits.join(" · ")
  }

  function earSummary() {
    var ear = pods.ear
    if (!ear) return ""
    var inEar = ear.filter(function(e) { return e === "ear" }).length
    var inCase = ear.filter(function(e) { return e === "case" }).length
    if (inEar === 2) return "Both in ear"
    if (inEar === 1) return "One in ear" + (inCase ? ", one in case" : "")
    if (inCase === 2) return "In case"
    return "Not in ear"
  }

  function statusLine() {
    if (!svc) return "SERVICE NOT LOADED"
    if (svc.lastError) return svc.lastError.toUpperCase()
    if (pairing) {
      if (pairing.stage === "scanning") return "LOOKING FOR AIRPODS…"
      if (pairing.stage === "pairing") return "PAIRING…"
      if (pairing.stage === "failed") return "PAIRING FAILED"
    }
    switch (status) {
    case "starting": return "STARTING…"
    case "unpaired": return "NOT PAIRED"
    case "connecting": return "CONNECTING…"
    case "disconnected": return "DISCONNECTED"
    case "offline": return "BACKEND STOPPED"
    }
    var bits = [device && device.model ? device.model : "Connected"]
    if (pods.noiseMode && modeInfo[pods.noiseMode] && modes.length > 1) bits.push(modeInfo[pods.noiseMode].label)
    return bits.join(" · ").toUpperCase()
  }

  function setMode(mode) {
    if (svc && hasControl) svc.send("mode", { mode: mode })
  }

  function toggleConnection() {
    if (svc && device) svc.send(connected ? "disconnect" : "connect")
  }

  // ---- bar button ----
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.connected ? root.glyphOn : root.glyphOff
    active: root.connected && root.lowestBud <= 10
    tooltipText: {
      if (!root.connected) return (root.device ? root.device.name : "AirPods") + ": " + root.statusLine().toLowerCase()
      var line = root.device.name
      if (root.batteryParts.length) line += "\n" + root.batterySummary()
      if (root.pods.noiseMode && root.modes.length > 1) line += "\n" + root.modeInfo[root.pods.noiseMode].label
      return line
    }
    onPressed: function(b) {
      if (b === Qt.RightButton) { if (root.svc && root.hasControl && root.modes.length > 1) root.svc.send("mode", { mode: "cycle" }) }
      else if (b === Qt.MiddleButton) root.toggleConnection()
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
    contentWidth: panel.fittedContentWidth(Style.space(340))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var i = parseInt(t)
        if (i >= 1 && i <= root.modes.length) root.setMode(root.modes[i - 1])
        else if (t === "c") root.toggleConnection()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(14)

        // ---------- Hero ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, connectSwitch.implicitHeight)

          Text {
            id: heroIcon
            textFormat: Text.PlainText
            text: root.connected ? root.glyphOn : root.glyphOff
            color: root.connected ? Color.accent : root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: connectSwitch.visible ? connectSwitch.left : parent.right
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: root.device ? root.device.name : "AirPods"
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              textFormat: Text.PlainText
              text: root.statusLine()
              color: root.svc && root.svc.lastError ? root.bar.urgent : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }
          }

          ToggleSwitch {
            id: connectSwitch
            visible: !!root.device
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            foreground: root.fg
            checked: root.connected
            busy: root.busy
            interactive: !root.busy
            cursorRing: false
            onToggled: root.toggleConnection()
          }
        }

        // ---------- Battery ----------
        Column {
          visible: root.connected && root.batteryParts.length > 0
          width: parent.width
          spacing: Style.space(8)

          PanelSeparator { foreground: root.fg }

          Row {
            id: batteryRow
            width: parent.width
            spacing: Style.space(10)

            Repeater {
              model: root.batteryParts
              BatteryCell {
                required property var modelData
                width: (batteryRow.width - batteryRow.spacing * (root.batteryParts.length - 1)) / root.batteryParts.length
                part: modelData.part
                level: modelData.level
                charging: modelData.charging
              }
            }
          }

          Text {
            visible: text !== ""
            text: root.earSummary()
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        // ---------- Noise control ----------
        Column {
          visible: root.hasControl && root.modes.length > 1
          width: parent.width
          spacing: Style.space(8)

          PanelSeparator { foreground: root.fg }

          PanelSectionHeader {
            text: "NOISE CONTROL"
            foreground: root.fg
            fontFamily: root.fontFamily
          }

          Row {
            id: modeRow
            width: parent.width
            spacing: Style.space(6)

            Repeater {
              model: root.modes
              ModeTile {
                required property var modelData
                required property int index
                width: (modeRow.width - modeRow.spacing * (root.modes.length - 1)) / root.modes.length
                mode: modelData
                shortcut: index + 1
              }
            }
          }
        }

        // ---------- Toggles ----------
        Column {
          visible: !!root.device
          width: parent.width
          spacing: Style.space(4)

          PanelSeparator { foreground: root.fg }

          ToggleRow {
            visible: root.hasControl && root.modes.indexOf("adaptive") !== -1
            label: "Conversational Awareness"
            hint: "Lower media and boost voices when you speak"
            checked: !!root.pods.conversationalAwareness
            onToggled: root.svc.send("conversational", { state: root.pods.conversationalAwareness ? "off" : "on" })
          }

          ToggleRow {
            label: "Pause when removed"
            hint: "Pause media when an AirPod leaves your ear"
            checked: !!root.pods.autoPause
            onToggled: root.svc.send("autopause", { state: root.pods.autoPause ? "off" : "on" })
          }
        }

        // ---------- Pairing ----------
        Column {
          visible: root.status === "unpaired" || !!root.pairing
          width: parent.width
          spacing: Style.space(8)

          PanelSeparator { foreground: root.fg }

          Text {
            width: parent.width
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            text: {
              if (root.pairing && root.pairing.stage === "failed")
                return (root.pairing.error || "Pairing failed") + ". Try again with the case open next to the computer."
              if (root.pairing && root.pairing.stage === "pairing")
                return "Pairing with " + (root.pairing.name || "AirPods") + "…"
              if (root.pairing)
                return "Open the case lid and hold the button on the back until the light flashes white."
              return "Pair your AirPods: open the case lid, hold the button on the back until the light flashes white, then click Pair."
            }
          }

          Button {
            text: root.pairing && (root.pairing.stage === "scanning" || root.pairing.stage === "pairing") ? "Cancel" : "Pair"
            foreground: root.fg
            fontFamily: root.fontFamily
            fontSize: Style.font.caption
            bordered: true
            onClicked: {
              if (root.pairing && root.pairing.stage !== "failed") root.svc.send("pair_cancel")
              else root.svc.send("pair")
            }
          }
        }

        Text {
          visible: root.connected && !root.hasControl && !!root.pods.controlError
          width: parent.width
          wrapMode: Text.Wrap
          text: "Control channel unavailable (" + root.pods.controlError + "). Retrying…"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Item { width: parent.width; height: Style.space(2) }
      }
    }
  }

  component BatteryCell: Column {
    id: cell
    property string part: ""
    property int level: 0
    property bool charging: false
    readonly property color barColor: charging ? Color.accent
      : level <= 10 ? (root.bar ? root.bar.urgent : Color.urgent) : root.fg
    spacing: Style.space(4)

    Text {
      text: root.partLabel(cell.part).toUpperCase()
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.letterSpacing: 1.2
    }

    Text {
      text: cell.level + "%" + (cell.charging ? " " + root.glyphBolt : "")
      color: root.fg
      font.family: root.fontFamily
      font.pixelSize: Style.font.title
      font.bold: true
    }

    Rectangle {
      width: parent.width
      height: Math.max(3, Style.space(4))
      radius: height / 2
      color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.15)

      Rectangle {
        width: parent.width * Math.max(0, Math.min(1, cell.level / 100))
        height: parent.height
        radius: parent.radius
        color: cell.barColor
      }
    }
  }

  component ModeTile: Rectangle {
    id: tile
    property string mode: ""
    property int shortcut: 0
    readonly property bool current: root.pods.noiseMode === mode
    readonly property var info: root.modeInfo[mode] || { label: mode, glyph: "" }

    height: tileColumn.implicitHeight + Style.space(16)
    radius: Style.cornerRadius
    color: current ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.22)
      : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, mouse.containsMouse ? 0.12 : 0.05)
    border.width: 1
    border.color: current ? Color.accent : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.18)

    Column {
      id: tileColumn
      anchors.centerIn: parent
      width: parent.width - Style.space(6)
      spacing: Style.space(4)

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: tile.info.glyph
        color: tile.current ? Color.accent : root.fg
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
      }

      Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        text: tile.info.label
        color: tile.current ? root.fg : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        fontSizeMode: Text.HorizontalFit
        minimumPixelSize: Math.round(Style.font.caption * 0.7)
        elide: Text.ElideRight
      }
    }

    MouseArea {
      id: mouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: root.setMode(tile.mode)
    }

    PanelToolTip {
      visible: mouse.containsMouse
      text: tile.info.label + " (" + tile.shortcut + ")"
    }
  }

  component ToggleRow: Item {
    id: row
    property string label: ""
    property string hint: ""
    property bool checked: false
    signal toggled()

    width: parent ? parent.width : 0
    implicitHeight: Math.max(rowLabels.implicitHeight, rowSwitch.implicitHeight) + Style.space(8)

    Column {
      id: rowLabels
      anchors.left: parent.left
      anchors.right: rowSwitch.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(2)

      Text {
        text: row.label
        color: root.fg
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
        width: parent.width
      }

      Text {
        text: row.hint
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
        width: parent.width
      }
    }

    ToggleSwitch {
      id: rowSwitch
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      foreground: root.fg
      checked: row.checked
      cursorRing: false
      onToggled: row.toggled()
    }
  }
}
