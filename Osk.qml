import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import qs.Commons
import "KeyboardLayout.js" as Layout

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
  property string keyLayout: Layout.defaultLayout
  property var enabledLayouts: Layout.defaultEnabled   // ids in the globe cycle
  property bool keyPreview: false
  property bool splitKeyboard: false
  property bool suggestOn: true
  property bool autocorrectOn: true
  property bool glideOn: true

  // layout id -> AnySoftKeyboard dictionary language
  function dictFor(id) {
    var base = id.replace(/-simple$/, "")
    if (base.indexOf("en-") === 0) return "english"
    if (base.indexOf("es-") === 0) return "spain"
    if (base.indexOf("de-") === 0) return "german"
    if (base.indexOf("fr-") === 0) return "french"
    if (base === "he-standard" || base === "ask-hebrew") return "hebrew"
    if (base === "ask-arabic") return "arabic"
    if (base === "ask-greek") return "greek"
    if (base === "ask-russian2") return "russian2"
    if (base === "ask-persian") return "persian"
    return "english"
  }
  function sendFeatures() {
    bridge.sendCmd({ cmd: "features", suggest: suggestOn, autocorrect: autocorrectOn, glide: glideOn })
  }
  function sendLang() { bridge.sendCmd({ cmd: "lang", value: dictFor(keyLayout) }) }
  function sendKeymap() {
    var km = keyboard.letterKeymap()
    bridge.sendCmd({ cmd: "keymap", keys: km.keys, unit: km.unit })
  }
  Timer { id: keymapDebounce; interval: 350; onTriggered: root.sendKeymap() }

  // autocorrect is poison in a terminal; follow the focused window's class
  Connections {
    target: Hyprland
    function onRawEvent(event) {
      if (String(event && event.name ? event.name : "") !== "activewindow") return
      var cls = String(event.data || "").split(",")[0]
      bridge.sendCmd({ cmd: "appclass", value: cls })
    }
  }
  // Helpers are launched by absolute path (never PATH lookup) and supervised
  // with a failure budget: exponential backoff, and after too many crashes in
  // a row the helper stays down until the shell is reloaded, rather than
  // respawning every two seconds forever.
  readonly property string pythonBin: "/usr/bin/python3"
  readonly property string bashBin: "/bin/bash"
  readonly property int helperMaxFailures: 8
  function helperBackoff(failures) { return Math.min(60000, 2000 * Math.pow(2, Math.max(0, failures - 1))) }

  // ---- visibility state ------------------------------------------------------
  property bool imWantsKeyboard: false    // fcitx5: a text field is focused
  property bool pinned: false             // opened by swipe / IPC
  property bool dismissed: false          // hide key pressed while the field kept focus
  property bool tabletMode: false
  onTabletModeChanged: if (tabletMode) dismissed = false
  onKeyLayoutChanged: {
    if (keyLayout !== "" && keyboard.keyLayout !== keyLayout) keyboard.keyLayout = keyLayout
    keymapDebounce.restart()
    sendLang()
  }
  onEnabledLayoutsChanged: keyboard.enabledLayouts = enabledLayouts
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
      keyboard.pickerOpen = false
      keyboard.settingsOpen = false
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

  // ---- first-run dependency check ------------------------------------------------
  // `omarchy plugin add` runs no install script, so on first load make sure the
  // typing stack is present; if not, check-deps.sh --nag points the user at
  // setup.sh once (a state flag keeps it from repeating every shell start).
  Process {
    id: depCheck
    command: [root.bashBin, Qt.resolvedUrl("check-deps.sh").toString().replace(/^file:\/\//, ""), "--nag"]
    running: true
  }

  // ---- dictation (voxtype) -------------------------------------------------------
  // The keyboard grows a microphone key when voxtype is installed; it toggles
  // recording the same way the Pause key does in laptop mode.
  property bool dictationAvailable: false
  property string voxtypeBin: ""          // resolved once at startup; executed by that exact path
  Process {
    command: [root.bashBin, "-c", "command -v voxtype 2>/dev/null || true"]
    running: true
    stdout: SplitParser { onRead: function(line) {
      var p = String(line).trim()
      if (p.charAt(0) === "/") { root.voxtypeBin = p; root.dictationAvailable = true }
    } }
  }
  Process { id: dictation }
  function toggleDictation() {
    if (dictationAvailable && voxtypeBin && !dictation.running) {
      dictation.command = [voxtypeBin, "record", "toggle"]
      dictation.running = true
    }
  }

  // ---- bridge to fcitx5 / injectors ---------------------------------------------
  readonly property string bridgePath: Qt.resolvedUrl("osk-bridge.py").toString().replace(/^file:\/\//, "")
  property bool bridgeReady: false

  property int bridgeFailures: 0
  property bool bridgeGaveUp: false

  Process {
    id: bridge
    command: [root.pythonBin, root.bridgePath]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(data) { root.onBridgeLine(data) }
    }
    onStarted: bridgeStable.restart()
    onExited: function(code, status) {
      root.bridgeReady = false
      bridgeStable.stop()
      root.bridgeFailures += 1
      if (root.bridgeFailures > root.helperMaxFailures) {
        if (!root.bridgeGaveUp) console.warn("osk: bridge crashed", root.bridgeFailures, "times; giving up until the shell reloads")
        root.bridgeGaveUp = true
        return
      }
      restartTimer.interval = root.helperBackoff(root.bridgeFailures)
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
  // A bridge that stays up this long is healthy: forget earlier crashes.
  Timer {
    id: bridgeStable
    interval: 30000
    onTriggered: root.bridgeFailures = 0
  }

  function onBridgeLine(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    switch (msg.event) {
    case "ready":
      bridgeReady = true
      bridge.sendCmd({ cmd: "visible", value: shown })
      bridge.sendCmd({ cmd: "autoAllowed", value: autoAllowed })
      sendFeatures()
      sendLang()
      keymapDebounce.restart()
      break
    case "suggest":
      keyboard.suggestions = msg.cands || []
      break
    case "swiped":
      keyboard.suggestions = msg.cands || []
      break
    case "autocorrected":
      keyboard.suggestions = msg.frm ? [msg.frm] : []
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

  // ---- settings + tablet-mode state (osk-store.py) -----------------------------------
  // Both files are read by a small stdlib helper - bounded, no symlink following,
  // owner-checked, inotify-driven - instead of FileView, which slurps whatever a
  // pathname resolves to. Settings are saved through the same process, so writes
  // are validated against a closed schema, serialized, and replaced atomically.
  readonly property string storePath: Qt.resolvedUrl("osk-store.py").toString().replace(/^file:\/\//, "")
  property bool storeReady: false
  property int storeFailures: 0
  property bool storeGaveUp: false
  property var pendingSave: null

  Process {
    id: store
    command: [root.pythonBin, root.storePath]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(data) { root.onStoreLine(data) }
    }
    onStarted: storeStable.restart()
    onExited: function(code, status) {
      root.storeReady = false
      storeStable.stop()
      root.storeFailures += 1
      if (root.storeFailures > root.helperMaxFailures) {
        if (!root.storeGaveUp) console.warn("osk: store helper crashed", root.storeFailures, "times; giving up until the shell reloads")
        root.storeGaveUp = true
        return
      }
      storeRestart.interval = root.helperBackoff(root.storeFailures)
      storeRestart.restart()
    }
    function send(obj) {
      if (running) write(JSON.stringify(obj) + "\n")
    }
  }
  Timer { id: storeRestart; interval: 2000; onTriggered: store.running = true }
  Timer { id: storeStable; interval: 30000; onTriggered: root.storeFailures = 0 }

  function onStoreLine(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    switch (msg.event) {
    case "ready":
      storeReady = true
      if (pendingSave) { store.send({ cmd: "save", settings: pendingSave }); pendingSave = null }
      break
    case "settings":
      applySettings(msg.settings)
      break
    case "tablet":
      tabletMode = msg.mode === "tablet"
      break
    case "error":
      console.warn("osk store:", String(msg.message).substring(0, 200))
      break
    }
  }

  function applySettings(s) {
    try {
      if (!s || typeof s !== "object") return
      if (s.autoShow === "tablet" || s.autoShow === "always" || s.autoShow === "never") autoShow = s.autoShow
      if (typeof s.swipe === "boolean") swipeEnabled = s.swipe
      if (typeof s.layout === "string" && s.layout) keyLayout = s.layout
      if (typeof s.layout === "string" && s.layout === "en-simple") s.layout = "en-qwerty-simple"
      if (Array.isArray(s.enabled)) s.enabled = s.enabled.map(function(x) { return x === "en-simple" ? "en-qwerty-simple" : x })
      if (Array.isArray(s.enabled) && s.enabled.length) {
        var valid = s.enabled.filter(function(x) {
          return typeof x === "string" && Layout.catalogList.some(function(c) { return c.id === x })
        })
        if (valid.length) enabledLayouts = valid
      }
      if (enabledLayouts.indexOf(keyLayout) < 0 && !Layout.catalogList.some(function(c) { return c.id === keyLayout }))
        keyLayout = enabledLayouts[0] || Layout.defaultLayout
      if (typeof s.keyPreview === "boolean") keyPreview = s.keyPreview
      if (typeof s.split === "boolean") splitKeyboard = s.split
      if (typeof s.suggest === "boolean") suggestOn = s.suggest
      if (typeof s.autocorrect === "boolean") autocorrectOn = s.autocorrect
      if (typeof s.glide === "boolean") glideOn = s.glide
      sendFeatures()
    } catch (e) {}
  }

  function saveSettings() {
    var settings = { autoShow: autoShow, swipe: swipeEnabled, layout: keyLayout,
      enabled: enabledLayouts, keyPreview: keyPreview, split: splitKeyboard,
      suggest: suggestOn, autocorrect: autocorrectOn, glide: glideOn }
    if (storeReady) store.send({ cmd: "save", settings: settings })
    else pendingSave = settings          // flushed when the helper comes up
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
        layer: keyboard.keyLayer, layout: keyboard.keyLayout,
        enabled: root.enabledLayouts, keyPreview: root.keyPreview, split: root.splitKeyboard,
        suggest: root.suggestOn, autocorrect: root.autocorrectOn, glide: root.glideOn
      })
    }
    function layouts(): string {
      return JSON.stringify({ catalog: Layout.catalogList, enabled: root.enabledLayouts, current: keyboard.keyLayout })
    }
    function setLayout(id: string): string {
      keyboard.selectLayout(id)
      return keyboard.keyLayout === id ? "ok" : "unknown layout: " + id
    }
    function setEnabled(csv: string): string {
      var ids = csv.split(",").map(function(x){ return x.trim() }).filter(function(x){ return x !== "" })
      var valid = ids.filter(function(id){ return Layout.catalogList.some(function(c){ return c.id === id }) })
      if (!valid.length) return "no valid layout ids"
      root.enabledLayouts = valid
      if (valid.indexOf(keyboard.keyLayout) < 0) { root.keyLayout = valid[0]; keyboard.selectLayout(valid[0]) }
      root.saveSettings()
      return "enabled: " + valid.join(", ")
    }
    function manage(): string { keyboard.settingsOpen = true; return "ok" }
    function setLayer(l: string): string {
      if (l !== "base" && l !== "symbols") return "usage: setLayer base|symbols"
      keyboard.keyLayer = l; return "ok"
    }
    function switcher(): string { keyboard.pickerOpen = true; return "ok" }
    function setSuggest(on: string): string {
      root.suggestOn = (on === "1" || on === "true" || on === "on"); root.sendFeatures(); root.saveSettings()
      return root.suggestOn ? "on" : "off"
    }
    function setAutocorrect(on: string): string {
      root.autocorrectOn = (on === "1" || on === "true" || on === "on"); root.sendFeatures(); root.saveSettings()
      return root.autocorrectOn ? "on" : "off"
    }
    function setGlide(on: string): string {
      root.glideOn = (on === "1" || on === "true" || on === "on"); root.sendFeatures(); root.saveSettings()
      return root.glideOn ? "on" : "off"
    }
    function setSplit(on: string): string {
      root.splitKeyboard = (on === "1" || on === "true" || on === "on")
      root.saveSettings()
      return root.splitKeyboard ? "on" : "off"
    }
    function setKeyPreview(on: string): string {
      root.keyPreview = (on === "1" || on === "true" || on === "on")
      root.saveSettings()
      return root.keyPreview ? "on" : "off"
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
    // Headroom above the keys keeps top-row key-preview bubbles from being
    // clipped at the window edge; input is masked to the keys themselves so
    // the transparent band never swallows taps meant for the window above.
    readonly property int headroom: keyboard.rowHeight + Style.space(12)
    implicitHeight: keyboard.implicitHeight + headroom
    color: "transparent"
    mask: Region { item: keyboard }
    // Reserve space (windows tile above the keyboard, like iPad) only while
    // shown; during the slide-out the desktop can already reclaim it.
    exclusionMode: root.shown ? ExclusionMode.Normal : ExclusionMode.Ignore
    exclusiveZone: keyboard.implicitHeight
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
      y: root.shown ? panel.headroom : panel.implicitHeight
      showDictation: root.dictationAvailable
      enabledLayouts: root.enabledLayouts
      keyPreview: root.keyPreview
      splitMode: root.splitKeyboard
      suggestEnabled: root.suggestOn
      autocorrectEnabled: root.autocorrectOn
      glideEnabled: root.glideOn
      onKeyLayerChanged: keymapDebounce.restart()
      onSplitModeChanged: keymapDebounce.restart()
      onWidthChanged: keymapDebounce.restart()
      onSuggestionPicked: function(w) { bridge.sendCmd({ cmd: "pick", word: w }) }
      onSwipeCaptured: function(p) { bridge.sendCmd({ cmd: "swipe", path: p }) }
      onKeyAction: function(a) { root.handleAction(a) }
      onDismissRequested: root.close()
      onDictationRequested: root.toggleDictation()
      onLayoutChanged: function(id) { root.keyLayout = id; root.saveSettings() }
      onRosterEdited: function(ids) { root.enabledLayouts = ids; root.saveSettings() }
      onPrefToggled: function(name, on) {
        if (name === "split") root.splitKeyboard = on
        else if (name === "preview") root.keyPreview = on
        else if (name === "suggest") root.suggestOn = on
        else if (name === "autocorrect") root.autocorrectOn = on
        else if (name === "glide") root.glideOn = on
        root.sendFeatures()
        root.saveSettings()
      }
      Component.onCompleted: if (root.keyLayout !== "") keyLayout = root.keyLayout

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
