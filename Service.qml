import QtQuick
import Quickshell
import Quickshell.Io

// Owns the single `airpods daemon` process (one AirPods control session per
// shell) and mirrors its state. Bar widgets on every monitor share it.
Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string cli: String(Qt.resolvedUrl("bin/airpods")).replace(/^file:\/\//, "")

  property var pods: ({ status: "starting" })
  property string lastError: ""
  property int serial: 0

  readonly property string status: pods.status || "starting"
  readonly property bool connected: status === "connected"
  readonly property bool hasControl: connected && !!pods.control
  readonly property var device: pods.device || null
  readonly property var battery: pods.battery || ({})
  readonly property var pairing: pods.pairing || null

  function send(cmd, args) {
    if (!daemon.running) return false
    daemon.write(JSON.stringify(Object.assign({ cmd: cmd, id: ++serial }, args || {})) + "\n")
    return true
  }

  function handleLine(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    if (msg.type === "state") {
      root.pods = msg.state || {}
    } else if (msg.type === "result" && !msg.ok) {
      root.lastError = msg.error || "command failed"
      clearError.restart()
    }
  }

  Process {
    id: daemon
    command: [root.cli, "daemon"]
    running: true
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.handleLine(line) } }
    onRunningChanged: {
      if (running) return
      root.pods = { status: "offline" }
      restart.restart()
    }
  }

  Timer {
    id: restart
    interval: 10000
    onTriggered: daemon.running = true
  }

  Timer {
    id: clearError
    interval: 6000
    onTriggered: root.lastError = ""
  }
}
