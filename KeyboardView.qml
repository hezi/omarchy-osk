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
  property var enabledLayouts: Layout.defaultEnabled // ids in the globe cycle (set by Osk)
  property bool keyPreview: false
  property bool showDictation: false
  // Layouts are rebuilt for the current mic state (the bottom row's widths depend on it).
  readonly property var _layouts: Layout.make(showDictation)
  readonly property var currentLayout: _layouts[keyLayout] || _layouts[Layout.defaultLayout]
  readonly property var rows: currentLayout[keyLayer] || currentLayout.base
  readonly property var switcherList: (enabledLayouts || []).map(function(id) {
    for (var i = 0; i < Layout.catalogList.length; i++)
      if (Layout.catalogList[i].id === id) return Layout.catalogList[i]
    return null
  }).filter(function(x) { return x !== null })
  property bool pickerOpen: false
  property bool settingsOpen: false
  signal rosterEdited(var ids)
  signal prefToggled(string name, bool on)

  // ---- split (iPad-style thumb) mode ----------------------------------------------
  property bool splitMode: false
  readonly property real splitGap: 2.5
  function splitRow(row) {
    var total = 0, i
    for (i = 0; i < row.length; i++) total += (row[i].w || 1)
    var half = total / 2, acc = 0, out = [], inserted = false
    for (i = 0; i < row.length; i++) {
      var k = row[i], w = k.w || 1
      if (!inserted && k.id === "space" && acc < half && acc + w > half) {
        var lw = half - acc, rw = w - lw
        var l = {}; for (var p in k) l[p] = k[p]; l.w = lw
        var r = {}; for (var q in k) r[q] = k[q]; r.w = rw
        out.push(l, { gap: true, w: splitGap }, r)
        inserted = true; acc += w; continue
      }
      if (!inserted && acc >= half) { out.push({ gap: true, w: splitGap }); inserted = true }
      out.push(k); acc += w
    }
    return out
  }
  readonly property var displayRows: splitMode ? rows.map(splitRow) : rows

  // Catalog grouped by language, as a flat list of {header} / {layout} rows.
  readonly property var catalogGrouped: {
    var out = [], groups = {}, order = [], list = Layout.catalogList
    for (var i = 0; i < list.length; i++) {
      var lg = list[i].language
      if (!(lg in groups)) { groups[lg] = []; order.push(lg) }
      groups[lg].push(list[i])
    }
    for (var g = 0; g < order.length; g++) {
      out.push({ header: order[g] })
      var arr = groups[order[g]]
      for (var k = 0; k < arr.length; k++) out.push({ layout: arr[k] })
    }
    return out
  }
  function toggleEnabledLayout(id) {
    var l = (enabledLayouts || []).slice()
    var i = l.indexOf(id)
    if (i >= 0) l.splice(i, 1); else l.push(id)
    if (!l.length) l = [id]
    if (l.indexOf(keyLayout) < 0) selectLayout(l[0])
    rosterEdited(l)
  }
  function moveEnabledLayout(id, dir) {
    var l = (enabledLayouts || []).slice()
    var i = l.indexOf(id), j = i + dir
    if (i < 0 || j < 0 || j >= l.length) return
    var t = l[i]; l[i] = l[j]; l[j] = t
    rosterEdited(l)
  }

  function selectLayout(id) {
    if (!_layouts[id]) return
    keyLayout = id
    keyLayer = "base"
    pickerOpen = false
    resetModifiers()
    layoutChanged(id)
  }
  // Globe short-tap: advance to the next enabled layout (open the switcher if
  // there is nothing to cycle).
  function cycleLayout() {
    var l = enabledLayouts || []
    if (l.length < 2) { pickerOpen = true; return }
    var i = l.indexOf(keyLayout)
    selectLayout(l[(i + 1) % l.length])
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
  // ---- long-press alternates ------------------------------------------------------
  property var altKeys: []          // characters offered by the open popup
  property real altX: 0
  property real altY: 0
  property bool altOpen: false
  function openAlts(keyItem, def) {
    if (!def.alts) return
    // the base character was typed on press - retract it, the user is choosing
    keyAction({ kind: "key", name: "BackSpace", code: 22 })
    var chars = def.alts.split("")
    altKeys = chars
    var pos = keyItem.mapToItem(root, keyItem.width / 2, 0)
    var w = chars.length * rowHeight * 0.95 + (chars.length - 1) * gap
    altX = Math.max(padX, Math.min(pos.x - w / 2, width - padX - w))
    altY = pos.y - rowHeight * 1.05
    altOpen = true
  }
  function pickAlt(c) {
    var t = (shift || capsLock) ? c.toUpperCase() : c
    keyAction({ kind: "text", text: t })
    shift = false
    altOpen = false
  }

  function moveCursor(dir) {
    keyAction({ kind: "key", name: dir > 0 ? "Right" : "Left", code: dir > 0 ? 114 : 113 })
  }

  // ---- geometry ------------------------------------------------------------------
  readonly property int padX: Style.space(6)
  readonly property int padY: Style.space(6)
  readonly property int gap: Style.space(5)
  readonly property int handleHeight: Style.space(12)
  // A row of n units has n - 1 gaps between them, so this fits exactly.
  readonly property real effUnits: Layout.unitsPerRow + (splitMode ? splitGap : 0)
  readonly property real unit: Math.max(1, (width - 2 * padX - (effUnits - 1) * gap) / effUnits)
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
      else if (def.id === "globe") cycleLayout()
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
      model: root.displayRows

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

  // ---- alternates popup -------------------------------------------------------------
  MouseArea {
    anchors.fill: parent
    visible: root.altOpen
    z: 60
    onPressed: root.altOpen = false
  }
  Row {
    visible: root.altOpen
    x: root.altX
    y: root.altY
    z: 61
    spacing: root.gap
    Repeater {
      model: root.altKeys
      Rectangle {
        required property var modelData
        width: Math.round(root.rowHeight * 0.95)
        height: Math.round(root.rowHeight * 0.95)
        radius: Style.cornerRadius
        color: altTap.pressed ? Util.alpha(Color.accent, 0.5) : Util.alpha(Color.popups.text, 0.92)
        Text {
          anchors.centerIn: parent
          text: (root.shift || root.capsLock) ? modelData.toUpperCase() : modelData
          color: Color.popups.background
          font.family: Style.font.family
          font.pixelSize: Math.round(root.rowHeight * 0.42)
          font.bold: true
        }
        TapHandler { id: altTap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: root.pickAlt(modelData) }
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
          model: root.switcherList

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

        Rectangle {
          width: parent.width
          height: Math.round(root.rowHeight * 0.9)
          radius: Style.cornerRadius
          color: manageTap.pressed ? Util.alpha(Color.popups.text, 0.18) : Util.alpha(Color.popups.text, 0.05)
          Text {
            anchors.centerIn: parent
            text: "\u2699  Manage layouts\u2026"
            color: Color.accent
            font.family: Style.font.family
            font.pixelSize: Math.round(root.rowHeight * 0.30)
          }
          TapHandler { id: manageTap; onTapped: { root.pickerOpen = false; root.settingsOpen = true } }
        }
      }
    }
  }

  // ---- settings: manage the layout roster -----------------------------------------
  Rectangle {
    id: settingsPanel
    visible: root.settingsOpen
    anchors { top: handle.bottom; left: parent.left; right: parent.right; bottom: parent.bottom }
    color: Color.popups.background

    Item {
      id: setHead
      anchors { top: parent.top; left: parent.left; right: parent.right }
      anchors.margins: root.padX
      height: Math.round(root.rowHeight * 0.95)
      Text {
        anchors { left: parent.left; verticalCenter: parent.verticalCenter }
        text: "Layouts \u00b7 tap to add to the globe cycle"
        color: Util.alpha(Color.popups.text, 0.7)
        font.family: Style.font.family
        font.pixelSize: Math.round(root.rowHeight * 0.28)
        font.bold: true
      }
      Rectangle {
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        width: Math.round(root.rowHeight * 1.7); height: Math.round(root.rowHeight * 0.75)
        radius: Style.cornerRadius
        color: doneTap.pressed ? Util.alpha(Color.accent, 0.45) : Util.alpha(Color.accent, 0.28)
        Text { anchors.centerIn: parent; text: "Done"; color: Color.accent; font.bold: true
          font.family: Style.font.family; font.pixelSize: Math.round(root.rowHeight * 0.28) }
        TapHandler { id: doneTap; onTapped: root.settingsOpen = false }
      }
    }

    Row {
      id: prefRow
      anchors { top: setHead.bottom; left: parent.left; right: parent.right }
      anchors.margins: root.padX
      height: Math.round(root.rowHeight * 0.8)
      spacing: root.gap
      PrefToggle { label: "Split keyboard"; on: root.splitMode; onToggledPref: root.prefToggled("split", !on) }
      PrefToggle { label: "Key preview"; on: root.keyPreview; onToggledPref: root.prefToggled("preview", !on) }
    }

    Flickable {
      anchors { top: prefRow.bottom; left: parent.left; right: parent.right; bottom: parent.bottom }
      anchors.margins: root.padX
      anchors.topMargin: Math.round(root.gap * 1.2)
      contentHeight: setCol.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      Column {
        id: setCol
        width: parent.width
        spacing: Math.round(root.gap * 0.7)
        Repeater {
          model: root.catalogGrouped
          Item {
            id: gitem
            required property var modelData
            readonly property bool isHeader: modelData.header !== undefined
            readonly property var lay: modelData.layout || null
            readonly property int pos: (root.enabledLayouts && lay) ? root.enabledLayouts.indexOf(lay.id) : -1
            readonly property bool on: pos >= 0
            width: setCol.width
            height: isHeader ? Math.round(root.rowHeight * 0.72) : Math.round(root.rowHeight * 0.9)

            Text {
              visible: gitem.isHeader
              anchors { left: parent.left; bottom: parent.bottom; leftMargin: Style.space(4); bottomMargin: Style.space(2) }
              text: gitem.modelData.header || ""
              color: Util.alpha(Color.popups.text, 0.5)
              font.family: Style.font.family
              font.pixelSize: Math.round(root.rowHeight * 0.25)
              font.bold: true
            }

            Rectangle {
              visible: !gitem.isHeader
              anchors.fill: parent
              radius: Style.cornerRadius
              color: gitem.on ? Util.alpha(Color.accent, 0.14) : Util.alpha(Color.popups.text, 0.05)

              Rectangle {
                id: badge
                anchors { left: parent.left; verticalCenter: parent.verticalCenter; leftMargin: Style.space(10) }
                width: Math.round(root.rowHeight * 0.62); height: width; radius: Style.cornerRadius
                color: Util.alpha(Color.popups.text, 0.10)
                Text { anchors.centerIn: parent; text: gitem.lay ? gitem.lay.label : ""; color: Color.popups.text
                  font.family: Style.font.family; font.pixelSize: Math.round(root.rowHeight * 0.24); font.bold: true }
              }
              Text {
                anchors { left: badge.right; verticalCenter: parent.verticalCenter; leftMargin: Style.space(10) }
                text: (gitem.lay ? gitem.lay.variant : "") + (gitem.on ? "   #" + (gitem.pos + 1) : "")
                color: gitem.on ? Color.accent : Color.popups.text
                font.family: Style.font.family; font.pixelSize: Math.round(root.rowHeight * 0.28)
              }

              Row {
                anchors { right: parent.right; verticalCenter: parent.verticalCenter; rightMargin: Style.space(8) }
                spacing: Style.space(6)
                Rectangle {
                  visible: gitem.on; width: Math.round(root.rowHeight * 0.6); height: width; radius: Style.cornerRadius
                  color: upTap.pressed ? Util.alpha(Color.popups.text, 0.22) : Util.alpha(Color.popups.text, 0.08)
                  Text { anchors.centerIn: parent; text: "\u25b2"; color: Color.popups.text; font.pixelSize: Math.round(root.rowHeight * 0.2) }
                  TapHandler { id: upTap; onTapped: root.moveEnabledLayout(gitem.lay.id, -1) }
                }
                Rectangle {
                  visible: gitem.on; width: Math.round(root.rowHeight * 0.6); height: width; radius: Style.cornerRadius
                  color: dnTap.pressed ? Util.alpha(Color.popups.text, 0.22) : Util.alpha(Color.popups.text, 0.08)
                  Text { anchors.centerIn: parent; text: "\u25bc"; color: Color.popups.text; font.pixelSize: Math.round(root.rowHeight * 0.2) }
                  TapHandler { id: dnTap; onTapped: root.moveEnabledLayout(gitem.lay.id, 1) }
                }
                Rectangle {
                  id: pill
                  anchors.verticalCenter: parent.verticalCenter
                  width: Math.round(root.rowHeight * 1.0); height: Math.round(root.rowHeight * 0.52); radius: height / 2
                  color: gitem.on ? Color.accent : Util.alpha(Color.popups.text, 0.22)
                  Behavior on color { ColorAnimation { duration: 90 } }
                  Rectangle {
                    width: parent.height - 4; height: parent.height - 4; radius: height / 2; y: 2
                    x: gitem.on ? parent.width - width - 2 : 2
                    color: Color.popups.background
                    Behavior on x { NumberAnimation { duration: 90 } }
                  }
                  TapHandler { onTapped: if (gitem.lay) root.toggleEnabledLayout(gitem.lay.id) }
                }
              }
            }
          }
        }
      }
    }
  }

  component PrefToggle: Rectangle {
    property string label: ""
    property bool on: false
    signal toggledPref()
    width: (parent.width - parent.spacing) / 2
    height: parent.height
    radius: Style.cornerRadius
    color: on ? Util.alpha(Color.accent, 0.14) : Util.alpha(Color.popups.text, 0.05)
    Text {
      anchors { left: parent.left; verticalCenter: parent.verticalCenter; leftMargin: Style.space(10) }
      text: label
      color: on ? Color.accent : Color.popups.text
      font.family: Style.font.family
      font.pixelSize: Math.round(root.rowHeight * 0.28)
    }
    Rectangle {
      id: prefPill
      anchors { right: parent.right; verticalCenter: parent.verticalCenter; rightMargin: Style.space(8) }
      width: Math.round(root.rowHeight * 0.9); height: Math.round(root.rowHeight * 0.48); radius: height / 2
      color: parent.on ? Color.accent : Util.alpha(Color.popups.text, 0.22)
      Rectangle {
        width: parent.height - 4; height: parent.height - 4; radius: height / 2; y: 2
        x: prefPill.parent.on ? parent.width - width - 2 : 2
        color: Color.popups.background
        Behavior on x { NumberAnimation { duration: 90 } }
      }
    }
    TapHandler { onTapped: parent.toggledPref() }
  }

  // A single key. TapHandler tracks touch points independently, so two thumbs
  // can overlap presses the way they do on a real keyboard.
  component KeyCap: Rectangle {
    id: key
    property var def
    readonly property bool isGap: !!def.gap
    readonly property bool hasAlts: !!def.alts
    readonly property bool isMod: !!def.mod
    readonly property bool isSpecial: !!def.special
    readonly property bool active: isMod && root.modifierActive(def.id)
    readonly property bool isSpace: def.id === "space"
    property bool spaceDown: false
    readonly property bool down: tap.pressed || spaceDown
    // Word labels (esc, ctrl, alt, super) read better small; glyphs and characters large.
    readonly property bool wordLabel: def.label.length > 2

    readonly property bool isGlobe: def.id === "globe"
    readonly property bool isChar: !isGap && !isSpecial && !isMod && !isSpace && def.label !== "" && def.label.length <= 2
    opacity: isGap ? 0 : 1
    width: root.keyWidth(def.w)
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

    // iOS-style preview bubble above the finger while a character key is held.
    Rectangle {
      visible: key.down && root.keyPreview && key.isChar
      width: Math.max(parent.width, root.rowHeight * 0.9)
      height: root.rowHeight * 0.95
      radius: Style.cornerRadius
      color: Util.alpha(Color.popups.text, 0.92)
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.top
      anchors.bottomMargin: Style.space(6)
      z: 50
      Text {
        anchors.centerIn: parent
        text: root.labelFor(key.def)
        color: Color.popups.background
        font.family: Style.font.family
        font.pixelSize: Math.round(root.rowHeight * 0.5)
        font.bold: true
      }
    }

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
      enabled: key.isSpace && !key.isGap
      visible: key.isSpace && !key.isGap
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

    // Globe key is press-and-hold aware: a tap cycles layouts (fired on release),
    // a hold opens the switcher. Every other key fires on press, as before.
    property bool globeHeld: false
    TapHandler {
      id: tap
      enabled: !key.isSpace && !key.isGap
      gesturePolicy: TapHandler.WithinBounds
      onPressedChanged: {
        if (pressed) {
          if (key.isGlobe) { key.globeHeld = false; globeHold.restart() }
          else {
            root.pressKey(key.def)
            if (key.def.repeat) repeatDelay.restart()
            if (key.hasAlts) altHold.restart()
          }
        } else {
          if (key.isGlobe) {
            globeHold.stop()
            if (!key.globeHeld) root.cycleLayout()
          }
          altHold.stop()
          repeatDelay.stop()
          repeater.stop()
        }
      }
    }

    Timer {
      id: globeHold
      interval: 420
      onTriggered: { key.globeHeld = true; root.pickerOpen = true }
    }

    // Long-press on a key with alternates: retract the just-typed base
    // character and open the alternates popup above the key.
    Timer {
      id: altHold
      interval: 380
      onTriggered: root.openAlts(key, key.def)
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
