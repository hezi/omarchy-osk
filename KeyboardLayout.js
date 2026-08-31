// Key layout for the Omarchy on-screen keyboard.
//
// Two layers: "base" (a compact US keyboard, Esc on the top row) and
// "symbols" (function keys, navigation, a number block and the symbols
// without having to shift). Printable keys carry the text they type (plain
// and shifted); named keys carry an X keysym name plus the xkb keycode
// (evdev + 8) ydotool needs for chords.
//
// Widths are in "units"; every row adds up to 16 so the rows align.

.pragma library

// Nerd Font (Material Design) glyphs used for a few key caps.
var GLYPH = {
  keyboardClose: String.fromCodePoint(0xF030F),
  microphone: String.fromCodePoint(0xF036C)
};

function ch(base, shifted, code, w) {
  return {
    id: base, label: base, shiftLabel: shifted,
    keysym: base.charCodeAt(0), shiftKeysym: shifted.charCodeAt(0),
    code: code, w: w || 1, letter: false
  };
}

// A symbol that types itself whether or not shift is on (symbols layer).
function sym(c, code, w) {
  return { id: c, label: c, shiftLabel: c, code: code, w: w || 1, letter: false, sym: true };
}

function letter(c, code) {
  var up = c.toUpperCase();
  return {
    id: c, label: c, shiftLabel: up,
    keysym: c.charCodeAt(0), shiftKeysym: up.charCodeAt(0),
    code: code, w: 1, letter: true
  };
}

// A named (non-printing) key. `name` is the X keysym name; `code` is the xkb
// keycode (evdev + 8).
function special(id, label, name, code, w, opts) {
  opts = opts || {};
  return {
    id: id, label: label, name: name, code: code,
    w: w || 1, special: true, repeat: !!opts.repeat, letter: false
  };
}

function modifier(id, label, w) {
  return { id: id, label: label, mod: true, w: w || 1, special: true };
}

function action(id, label, w) {
  return { id: id, label: label, action: true, w: w || 1, special: true };
}

// Width of the dictation key; the space bar grows by this much when the key
// is hidden (no voxtype, or on the lock screen).
var dictationUnits = 1.25;

// Bottom row, shared by both layers apart from the layer-switch label.
function bottomRow(layerLabel) {
  return [
    modifier("ctrl", "ctrl", 1.25),
    modifier("super", "super", 1.25),
    modifier("alt", "alt", 1.25),
    action("layer", layerLabel, 1.25),
    action("dictate", GLYPH.microphone, dictationUnits),
    special("space", "", "space", 65, 4.5),
    action("hide", GLYPH.keyboardClose, 1.25),
    special("left", "←", "Left", 113, 1, { repeat: true }),
    special("up", "↑", "Up", 111, 1, { repeat: true }),
    special("down", "↓", "Down", 116, 1, { repeat: true }),
    special("right", "→", "Right", 114, 1, { repeat: true })
  ];
}

var base = [
  [
    special("esc", "esc", "Escape", 9, 1),
    ch("`", "~", 49), ch("1", "!", 10), ch("2", "@", 11), ch("3", "#", 12), ch("4", "$", 13),
    ch("5", "%", 14), ch("6", "^", 15), ch("7", "&", 16), ch("8", "*", 17), ch("9", "(", 18),
    ch("0", ")", 19), ch("-", "_", 20), ch("=", "+", 21),
    special("backspace", "⌫", "BackSpace", 22, 2, { repeat: true })
  ],
  [
    special("tab", "⇥", "Tab", 23, 2),
    letter("q", 24), letter("w", 25), letter("e", 26), letter("r", 27), letter("t", 28),
    letter("y", 29), letter("u", 30), letter("i", 31), letter("o", 32), letter("p", 33),
    ch("[", "{", 34), ch("]", "}", 35), ch("\\", "|", 51, 2)
  ],
  [
    modifier("caps", "⇪", 2.25),
    letter("a", 38), letter("s", 39), letter("d", 40), letter("f", 41), letter("g", 42),
    letter("h", 43), letter("j", 44), letter("k", 45), letter("l", 46),
    ch(";", ":", 47), ch("'", "\"", 48),
    special("enter", "⏎", "Return", 36, 2.75)
  ],
  [
    modifier("shift", "⇧", 2.75),
    letter("z", 52), letter("x", 53), letter("c", 54), letter("v", 55), letter("b", 56),
    letter("n", 57), letter("m", 58),
    ch(",", "<", 59), ch(".", ">", 60), ch("/", "?", 61),
    modifier("shift", "⇧", 3.25)
  ],
  bottomRow("?123")
];

var symbols = [
  [
    special("esc", "esc", "Escape", 9, 1),
    special("f1", "F1", "F1", 67), special("f2", "F2", "F2", 68), special("f3", "F3", "F3", 69),
    special("f4", "F4", "F4", 70), special("f5", "F5", "F5", 71), special("f6", "F6", "F6", 72),
    special("f7", "F7", "F7", 73), special("f8", "F8", "F8", 74), special("f9", "F9", "F9", 75),
    special("f10", "F10", "F10", 76), special("f11", "F11", "F11", 95), special("f12", "F12", "F12", 96),
    special("backspace", "⌫", "BackSpace", 22, 3, { repeat: true })
  ],
  [
    special("tab", "⇥", "Tab", 23, 2),
    special("insert", "ins", "Insert", 118), special("home", "home", "Home", 110),
    special("pgup", "pgup", "Page_Up", 112, 1, { repeat: true }),
    sym("7", 16), sym("8", 17), sym("9", 18), sym("/", 61), sym("*", 17),
    sym("(", 18), sym(")", 19), sym("{", 34), sym("}", 35),
    special("delete", "⌦", "Delete", 119, 2, { repeat: true })
  ],
  [
    special("end", "end", "End", 115, 1), special("pgdn", "pgdn", "Page_Down", 117, 1, { repeat: true }),
    sym("4", 13), sym("5", 14), sym("6", 15), sym("-", 20), sym("+", 21),
    sym("[", 34), sym("]", 35), sym("<", 59), sym(">", 60), sym("~", 49), sym("|", 51),
    special("enter", "⏎", "Return", 36, 3)
  ],
  [
    sym("1", 10), sym("2", 11), sym("3", 12), sym("0", 19), sym(".", 60), sym(",", 59),
    sym(";", 47), sym(":", 47), sym("!", 10), sym("?", 61), sym("@", 11), sym("#", 12),
    sym("%", 14), sym("^", 15), sym("&", 16), sym("$", 13)
  ],
  bottomRow("abc")
];

var layers = { base: base, symbols: symbols };

// Backwards compatibility: `rows` is the base layer.
var rows = base;

var unitsPerRow = 16;

// xkb keycodes of the modifier keys (evdev + 8), for ydotool chords.
var MOD_CODES = { shift: 50, ctrl: 37, alt: 64, super: 133 };
