import QtQuick
import qs.Commons
import "KeyboardLayout.js" as Layout

// The keyboard surface itself: rows of keys, modifier state, drag handle.
// It knows nothing about how keys are delivered - it only emits `keyAction`
// with one of:
//   { kind: "text",  text: "H" }                       printable, already shifted
//   { kind: "key",   name: "Return", code: 36 }         named key, no modifiers
//   { kind: "chord", code: 54, mods: ["ctrl"], label: "c", name: undefined }
// and `dismissRequested` for the hide key / a downward drag on the handle,
// `dictationRequested` for the microphone key (shown when `showDictation`).
//
// Two layers (`keyLayer`: "base" | "symbols"), switched with the ?123 / abc key.
// Many layouts (`keyLayout`: an id from KeyboardLayout.layouts), switched with
// the globe key, which opens a picker.
// Dragging a finger along the space bar moves the text cursor.
//
// Used by Osk.qml (tablet keyboard, injects into apps) and by the lock
// screen clone (feeds the password field directly). Styled purely from the
// live Omarchy theme tokens. Set `width`; height follows from it.
Rectangle {
  id: root

  signal keyAction(var action)
  signal dismissRequested()
  signal dictationRequested()
  signal layoutChanged(string id)

  // ---- layouts & layers -----------------------------------------------------------
  property string keyLayout: Layout.defaultLayout   // a KeyboardLayout id
  property string keyLayer: "base"                  // base | symbols
  readonly property var currentLayout: Layout.layouts[keyLayout] || Layout.layouts[Layout.defaultLayout]
  readonly property var rows: currentLayout[keyLayer] || currentLayout.base
  readonly property var layoutList: Layout.layoutList
  property bool pickerOpen: false
  property bool showDictation: false

  function selectLayout(id) {
    if (!Layout.layouts[id]) return
    keyLayout = id
    keyLayer = "base"
    pickerOpen = false
    resetModifiers()
    layoutChanged(id)
  }

  // ---- modifier state ----------------------------------------------------------
  property bool shift: false
  property bool capsLock: false
  property bool ctrl: false
  property bool alt: false
  property bool superMod: false
  property real lastShiftTap: 0

  function resetModifiers() {
    shift = false; capsLock = false; ctrl = false; alt = false; superMod = false
  }

  // Space-bar drag: one cursor step per `step` px.
  function moveCursor(dir) {
    keyAction({ kind: "key", name: dir > 0 ? "Right" : "Left", code: dir > 0 ? 114 : 113 })
  }

  // ---- geometry ------------------------------------------------------------------
  readonly property int padX: Style.space(6)
  readonly property int padY: Style.space(6)
  readonly property int gap: Style.space(5)
  readonly property int handleHeight: Style.space(12)
  // A row of n units has n - 1 gaps between them, so this fits exactly.
  readonly property real unit: Math.max(1, (width - 2 * padX - (Layout.unitsPerRow - 1) * gap) / Layout.unitsPerRow)
  readonly property int rowHeight: Math.max(40, Math.min(58, Math.round(unit * 0.95)))

  implicitHeight: handleHeight + padY * 2 + rows.length * rowHeight + (rows.length - 1) * gap
  color: Color.popups.background

  function keyWidth(units) {
    return Math.round(units * unit + (units - 1) * gap)
  }

  // ---- key dispatch ----------------------------------------------------------------
  function pressKey(def) {
    if (def.action) {
      if (def.id === "hide") dismissRequested()
      else if (def.id === "layer") keyLayer = (keyLayer === "base") ? "symbols" : "base"
      else if (def.id === "globe") pickerOpen = !pickerOpen
      else if (def.id === "dictate") dictationRequested()
      return
    }
    if (def.mod) {
      pressModifier(def.id)
      return
    }

    var chordMods = []
    if (ctrl) chordMods.push("ctrl")
    if (alt) chordMods.push("alt")
    if (superMod) chordMods.push("super")
    var useShift = shift || (capsLock && def.letter)

    if (chordMods.length > 0) {
      // Ctrl/Alt/Super held: a real chord on the base key (Ctrl+C, Super+Enter).
      if (shift) chordMods.push("shift")
      keyAction({ kind: "chord", code: def.code, mods: chordMods, label: def.label, name: def.name })
    } else if (def.name !== undefined) {
      if (shift) keyAction({ kind: "chord", code: def.code, mods: ["shift"], name: def.name })
      else if (def.name === "space") keyAction({ kind: "text", text: " " })
      else keyAction({ kind: "key", name: def.name, code: def.code })
    } else {
      keyAction({ kind: "text", text: useShift ? def.shiftLabel : def.label })
    }

    // One-shot modifiers release after a key; caps lock persists.
    shift = false
    ctrl = false
    alt = false
    superMod = false
  }

  function pressModifier(id) {
    if (id === "shift") {
      var now = Date.now()
      if (capsLock) {
        capsLock = false; shift = false
      } else if (now - lastShiftTap < 350) {
        capsLock = true; shift = false
      } else {
        shift = !shift
      }
      lastShiftTap = now
    } else if (id === "caps") {
      capsLock = !capsLock; shift = false
    } else if (id === "ctrl") {
      ctrl = !ctrl
    } else if (id === "alt") {
      alt = !alt
    } else if (id === "super") {
      superMod = !superMod
    }
  }

  function modifierActive(id) {
    if (id === "shift") return shift || capsLock
    if (id === "caps") return capsLock
    if (id === "ctrl") return ctrl
    if (id === "alt") return alt
    if (id === "super") return superMod
    return false
  }

  function labelFor(def) {
    if (def.letter) return (shift || capsLock) ? def.shiftLabel : def.label
    if (def.shiftLabel !== undefined && !def.special) return shift ? def.shiftLabel : def.label
    return def.label
  }

  // ---- chrome -----------------------------------------------------------------------
  // Top edge line, in the theme's popup border colour.
  Rectangle {
    anchors { top: parent.top; left: parent.left; right: parent.right }
    height: 1
    color: Color.popups.border
  }

  // Drag handle: swipe it down to dismiss.
  Item {
    id: handle
    anchors { top: parent.top; left: parent.left; right: parent.right }
    height: root.handleHeight

    Rectangle {
      anchors.centerIn: parent
      width: Style.space(40)
      height: Style.space(4)
      radius: height / 2
      color: Util.alpha(Color.popups.text, 0.35)
    }

    MouseArea {
      anchors.fill: parent
      property real startY: 0
      onPressed: function(mouse) { startY = mouse.y }
      onPositionChanged: function(mouse) {
        if (pressed && mouse.y - startY > Style.space(40)) {
          root.dismissRequested()
          startY = mouse.y
        }
      }
    }
  }

  Column {
    anchors { top: handle.bottom; horizontalCenter: parent.horizontalCenter }
    anchors.topMargin: root.padY
    spacing: root.gap

    Repeater {
      model: root.rows

      Row {
        required property var modelData
        spacing: root.gap
        anchors.horizontalCenter: parent.horizontalCenter

        Repeater {
          model: parent.modelData

          KeyCap {
            required property var modelData
            def: modelData
          }
        }
      }
    }
  }

  // ---- layout picker ---------------------------------------------------------------
  // Covers the keys while open; a tap outside a row closes it. One row per
  // layout, "Language · Variant", grouped visually by language.
  Rectangle {
    id: picker
    visible: root.pickerOpen
    anchors { top: handle.bottom; left: parent.left; right: parent.right; bottom: parent.bottom }
    color: Color.popups.background

    MouseArea { anchors.fill: parent; onClicked: root.pickerOpen = false }

    Text {
      anchors { top: parent.top; horizontalCenter: parent.horizontalCenter }
      anchors.topMargin: root.gap
      text: "Keyboard layout"
      color: Util.alpha(Color.popups.text, 0.6)
      font.family: Style.font.family
      font.pixelSize: Math.round(root.rowHeight * 0.28)
      font.bold: true
    }

    Flickable {
      anchors { top: parent.top; left: parent.left; right: parent.right; bottom: parent.bottom }
      anchors.topMargin: Math.round(root.rowHeight * 0.55)
      anchors.margins: root.padX
      contentHeight: pickerCol.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: pickerCol
        width: parent.width
        spacing: root.gap

        Repeater {
          model: root.layoutList

          Rectangle {
            required property var modelData
            width: parent.width
            height: Math.round(root.rowHeight * 0.9)
            radius: Style.cornerRadius
            readonly property bool current: modelData.id === root.keyLayout
            color: current ? Util.alpha(Color.accent, 0.28)
                 : lrTap.pressed ? Util.alpha(Color.popups.text, 0.20)
                 : Util.alpha(Color.popups.text, 0.07)
            border.width: current ? Math.max(1, Style.space(1)) : 0
            border.color: Color.accent

            Row {
              anchors { left: parent.left; verticalCenter: parent.verticalCenter }
              anchors.leftMargin: Style.space(12)
              spacing: Style.space(10)

              Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: Math.round(root.rowHeight * 0.7); height: width
                radius: Style.cornerRadius
                color: Util.alpha(Color.popups.text, 0.10)
                Text {
                  anchors.centerIn: parent
                  text: modelData.label
                  color: Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Math.round(root.rowHeight * 0.26)
                  font.bold: true
                }
              }
              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.language + "  ·  " + modelData.variant
                color: current ? Color.accent : Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Math.round(root.rowHeight * 0.30)
              }
            }

            TapHandler {
              id: lrTap
              gesturePolicy: TapHandler.WithinBounds
              onTapped: root.selectLayout(modelData.id)
            }
          }
        }
      }
    }
  }

  // A single key. TapHandler tracks touch points independently, so two thumbs
  // can overlap presses the way they do on a real keyboard.
  component KeyCap: Rectangle {
    id: key
    property var def
    readonly property bool isMod: !!def.mod
    readonly property bool isSpecial: !!def.special
    readonly property bool active: isMod && root.modifierActive(def.id)
    readonly property bool isSpace: def.id === "space"
    property bool spaceDown: false
    readonly property bool down: tap.pressed || spaceDown
    // Word labels (esc, ctrl, alt, super) read better small; glyphs and characters large.
    readonly property bool wordLabel: def.label.length > 2

    // The microphone key only exists when dictation is available; the space
    // bar takes its room otherwise.
    visible: !(def.id === "dictate" && !root.showDictation)
    width: root.keyWidth(def.w + (isSpace && !root.showDictation ? Layout.dictationUnits : 0))
    height: root.rowHeight
    radius: Style.cornerRadius
    color: down ? Util.alpha(Color.popups.text, 0.28)
         : active ? Util.alpha(Color.accent, 0.30)
         : isSpecial ? Util.alpha(Color.popups.text, 0.05)
         : Util.alpha(Color.popups.text, 0.11)
    border.width: active ? Math.max(1, Style.space(1)) : 0
    border.color: Color.accent
    scale: down ? 0.96 : 1.0

    Behavior on color { ColorAnimation { duration: 60 } }
    Behavior on scale { NumberAnimation { duration: 60 } }

    Text {
      anchors.centerIn: parent
      text: root.labelFor(key.def)
      color: key.active ? Color.accent : (key.isSpecial ? Util.alpha(Color.popups.text, 0.8) : Color.popups.text)
      font.family: Style.font.family
      font.pixelSize: key.wordLabel ? Math.round(root.rowHeight * 0.27) : Math.round(root.rowHeight * 0.40)
      font.bold: key.isMod
    }

    // Shifted symbol hint on number / punctuation keys, like a hardware cap.
    Text {
      visible: key.def.shiftLabel !== undefined && !key.def.letter && !key.isSpecial && !key.def.sym && !root.shift
      anchors { top: parent.top; right: parent.right }
      anchors.topMargin: Style.space(3)
      anchors.rightMargin: Style.space(5)
      text: key.def.shiftLabel || ""
      color: Color.muted
      font.family: Style.font.family
      font.pixelSize: Math.round(root.rowHeight * 0.22)
    }

    // Space bar: a tap types a space, a horizontal drag moves the cursor
    // (like holding the space bar on a phone keyboard).
    MouseArea {
      anchors.fill: parent
      enabled: key.isSpace
      visible: key.isSpace
      property real startX: 0
      property real travelled: 0
      property bool moved: false
      readonly property real step: root.rowHeight * 0.55
      onPressed: function(mouse) { startX = mouse.x; travelled = 0; moved = false; key.spaceDown = true }
      onPositionChanged: function(mouse) {
        if (!pressed) return
        var dx = mouse.x - startX
        while (dx - travelled >= step) { travelled += step; moved = true; root.moveCursor(1) }
        while (travelled - dx >= step) { travelled -= step; moved = true; root.moveCursor(-1) }
      }
      onReleased: { key.spaceDown = false; if (!moved) root.pressKey(key.def) }
      onCanceled: key.spaceDown = false
    }

    TapHandler {
      id: tap
      enabled: !key.isSpace
      gesturePolicy: TapHandler.WithinBounds
      onPressedChanged: {
        if (pressed) {
          root.pressKey(key.def)
          if (key.def.repeat) repeatDelay.restart()
        } else {
          repeatDelay.stop()
          repeater.stop()
        }
      }
    }

    Timer {
      id: repeatDelay
      interval: 380
      onTriggered: repeater.start()
    }

    Timer {
      id: repeater
      interval: 55
      repeat: true
      onTriggered: root.pressKey(key.def)
    }
  }
}
