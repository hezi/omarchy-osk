import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons

// On-screen keyboard for tablet mode.
//
// Visibility has two sources:
//   * fcitx5 - through osk-bridge.py, which owns fcitx5's virtual-keyboard
//     bus name. fcitx5 asks us to show whenever a text field takes focus and
//     to hide when it loses it. Honoured only when `autoShow` allows it
//     ("tablet" = only while the hinge is folded back, "always", "never").
//   * the user - an upward swipe from the bottom edge, the IPC target
//     (`omarchy-shell osk show|hide|toggle`) or a keybinding. A keyboard
//     opened this way is "pinned" and stays until dismissed.
//
// The keys themselves live in KeyboardView.qml (shared with the lock screen).
// Delivery goes through the bridge: wtype for text and named keys, ydotool
// for chords (so Ctrl+C reaches the app and Super+Enter reaches Hyprland).
Item {
  id: root

  // ---- settings (persisted in ~/.config/omarchy/osk.json) ------------------
  property string autoShow: "tablet"      // tablet | always | never
  property bool swipeEnabled: true
  readonly property string settingsPath: Quickshell.env("HOME") + "/.config/omarchy/osk.json"

  // ---- visibility state ------------------------------------------------------
  property bool imWantsKeyboard: false    // fcitx5: a text field is focused
  property bool pinned: false             // opened by swipe / IPC
  property bool dismissed: false          // hide key pressed while the field kept focus
  property bool tabletMode: false
  onTabletModeChanged: if (tabletMode) dismissed = false
  readonly property bool autoAllowed: autoShow === "always" || (autoShow === "tablet" && tabletMode)
  // fcitx5 forgets on-screen-keyboard mode whenever a real key is typed; the
  // bridge re-arms it while auto-show is allowed (see osk-bridge.py).
  onAutoAllowedChanged: bridge.sendCmd({ cmd: "autoAllowed", value: autoAllowed })
  readonly property bool shown: pinned || (imWantsKeyboard && autoAllowed && !dismissed)
  property bool mapped: false             // window stays mapped through the slide-out

  // ---- visibility control ----------------------------------------------------
  function open() { pinned = true; dismissed = false }
  function close() { pinned = false; dismissed = true }
  function toggle() { if (shown) close(); else open() }

  onShownChanged: {
    if (shown) {
      unmapTimer.stop()
      mapped = true
    } else {
      unmapTimer.restart()
      keyboard.resetModifiers()
    }
    bridge.sendCmd({ cmd: "visible", value: shown })
  }

  Timer {
    id: unmapTimer
    interval: 220
    onTriggered: root.mapped = false
  }

  // ---- key delivery ------------------------------------------------------------
  function handleAction(a) {
    switch (a.kind) {
    case "text":  bridge.sendCmd({ cmd: "type", text: a.text }); break
    case "key":   bridge.sendCmd({ cmd: "key", name: a.name, mods: [] }); break
    case "chord": bridge.sendCmd({ cmd: "chord", code: a.code, mods: a.mods }); break
    }
  }

  // ---- dictation (voxtype) -------------------------------------------------------
  // The keyboard grows a microphone key when voxtype is installed; it toggles
  // recording the same way the Pause key does in laptop mode.
  property bool dictationAvailable: false
  Process {
    command: ["sh", "-c", "command -v voxtype >/dev/null 2>&1 && echo yes || echo no"]
    running: true
    stdout: SplitParser { onRead: function(line) { root.dictationAvailable = String(line).trim() === "yes" } }
  }
  function toggleDictation() {
    if (dictationAvailable) Util.execArgv(["voxtype", "record", "toggle"])
  }

  // ---- bridge to fcitx5 / injectors ---------------------------------------------
  readonly property string bridgePath: Qt.resolvedUrl("osk-bridge.py").toString().replace(/^file:\/\//, "")
  property bool bridgeReady: false

  Process {
    id: bridge
    command: ["python3", root.bridgePath]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(data) { root.onBridgeLine(data) }
    }
    onExited: function(code, status) {
      root.bridgeReady = false
      restartTimer.restart()
    }
    function sendCmd(obj) {
      if (running) write(JSON.stringify(obj) + "\n")
    }
  }

  Timer {
    id: restartTimer
    interval: 2000
    onTriggered: bridge.running = true
  }

  function onBridgeLine(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    switch (msg.event) {
    case "ready":
      bridgeReady = true
      bridge.sendCmd({ cmd: "visible", value: shown })
      bridge.sendCmd({ cmd: "autoAllowed", value: autoAllowed })
      break
    case "show":
      imWantsKeyboard = true
      dismissed = false
      break
    case "hide":
      imWantsKeyboard = false
      break
    case "name_lost":
      bridgeReady = false
      break
    }
  }

  // ---- tablet-mode state from ~/bin/tablet-mode.sh ----------------------------------
  FileView {
    id: tabletFile
    path: "/tmp/tablet-mode.state"
    watchChanges: true
    printErrors: false
    onLoaded: root.tabletMode = text().trim() === "tablet"
    onFileChanged: reload()
  }

  // The state file may not exist yet when the shell starts, and inotify can
  // miss a replace-by-rename; a slow poll keeps the two in sync regardless.
  Timer {
    interval: 2000
    running: true
    repeat: true
    onTriggered: tabletFile.reload()
  }

  // ---- persisted settings ---------------------------------------------------------------
  FileView {
    id: settingsFile
    path: root.settingsPath
    watchChanges: true
    printErrors: false
    onLoaded: root.applySettings(text())
    onFileChanged: reload()
  }

  function applySettings(raw) {
    try {
      var s = JSON.parse(raw)
      if (s.autoShow === "tablet" || s.autoShow === "always" || s.autoShow === "never") autoShow = s.autoShow
      if (typeof s.swipe === "boolean") swipeEnabled = s.swipe
    } catch (e) {}
  }

  function saveSettings() {
    var json = JSON.stringify({ autoShow: autoShow, swipe: swipeEnabled }, null, 2)
    Util.execArgv(["sh", "-c", 'printf "%s\\n" "$1" > "$2"', "_", json, settingsPath])
  }

  // ---- IPC: omarchy-shell osk <method> -------------------------------------------------------
  IpcHandler {
    target: "osk"
    function show(): string { root.open(); return "ok" }
    function hide(): string { root.close(); return "ok" }
    function toggle(): string { root.toggle(); return root.shown ? "shown" : "hidden" }
    function state(): string {
      return JSON.stringify({
        shown: root.shown, pinned: root.pinned, tabletMode: root.tabletMode,
        textFieldFocused: root.imWantsKeyboard, autoShow: root.autoShow,
        swipe: root.swipeEnabled, bridge: root.bridgeReady, dictation: root.dictationAvailable,
        layer: keyboard.keyLayer
      })
    }
    function setAutoShow(mode: string): string {
      if (mode !== "tablet" && mode !== "always" && mode !== "never") return "usage: setAutoShow tablet|always|never"
      root.autoShow = mode
      root.saveSettings()
      return "ok"
    }
    // Inject through the keyboard's own path - handy for scripts and tests.
    function typeText(text: string): string { bridge.sendCmd({ cmd: "type", text: text }); return "ok" }
    function pressKey(name: string): string { bridge.sendCmd({ cmd: "key", name: name, mods: [] }); return "ok" }
    function setSwipe(enabled: string): string {
      root.swipeEnabled = (enabled === "1" || enabled === "true" || enabled === "on")
      root.saveSettings()
      return "ok"
    }
  }

  // ---- the keyboard surface -------------------------------------------------------------------
  PanelWindow {
    id: panel
    visible: root.mapped
    anchors { left: true; right: true; bottom: true }
    implicitHeight: keyboard.implicitHeight
    color: "transparent"
    // Reserve space (windows tile above the keyboard, like iPad) only while
    // shown; during the slide-out the desktop can already reclaim it.
    exclusionMode: root.shown ? ExclusionMode.Auto : ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-osk"
    // Overlay, not Top: fullscreen windows render above the Top layer and
    // would cover the keyboard. The keyboard must always be the topmost thing.
    WlrLayershell.layer: WlrLayer.Overlay
    // Never take keyboard focus: the text field being typed into must keep it.
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    KeyboardView {
      id: keyboard
      width: parent.width
      height: implicitHeight
      y: root.shown ? 0 : height
      showDictation: root.dictationAvailable
      onKeyAction: function(a) { root.handleAction(a) }
      onDismissRequested: root.close()
      onDictationRequested: root.toggleDictation()

      Behavior on y {
        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
      }
    }
  }

  // ---- bottom-edge swipe strip -------------------------------------------------------------------
  // A thin invisible layer along the bottom edge. Dragging upward from it opens
  // the keyboard (pinned). Only mapped while the keyboard is hidden.
  PanelWindow {
    id: swipeStrip
    visible: root.swipeEnabled && !root.mapped
    anchors { left: true; right: true; bottom: true }
    implicitHeight: Style.space(10)
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-osk-edge"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    MouseArea {
      anchors.fill: parent
      property real startY: 0
      property bool fired: false
      onPressed: function(mouse) { startY = mouse.y; fired = false }
      onPositionChanged: function(mouse) {
        if (!fired && pressed && startY - mouse.y > Style.space(40)) {
          fired = true
          root.open()
        }
      }
    }
  }
}
