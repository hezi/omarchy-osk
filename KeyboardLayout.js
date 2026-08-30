// Key layout for the Omarchy on-screen keyboard.
//
// Printable keys carry the text they type (plain and shifted); named keys
// carry an X keysym name. Injection is done by wtype through the bridge, so
// no keycodes are needed here. The `code` values on printable keys are kept
// only as documentation of the physical key they mirror.
//
// Widths are in "units"; every row adds up to 15 so the rows align.

.pragma library

// Nerd Font (Material Design) glyphs used for a few key caps.
var GLYPH = {
  keyboardClose: String.fromCodePoint(0xF030F)
};

function ch(base, shifted, code, w) {
  return {
    id: base, label: base, shiftLabel: shifted,
    keysym: base.charCodeAt(0), shiftKeysym: shifted.charCodeAt(0),
    code: code, w: w || 1, letter: false
  };
}

function letter(c, code) {
  var up = c.toUpperCase();
  return {
    id: c, label: c, shiftLabel: up,
    keysym: c.charCodeAt(0), shiftKeysym: up.charCodeAt(0),
    code: code, w: 1, letter: true
  };
}

// A named (non-printing) key. `name` is the X keysym name wtype understands;
// `code` is the xkb keycode (evdev + 8) ydotool needs for chords.
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

var rows = [
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
  [
    modifier("ctrl", "ctrl", 1.25),
    modifier("super", "super", 1.25),
    modifier("alt", "alt", 1.25),
    action("hide", GLYPH.keyboardClose, 1.25),
    special("space", "", "space", 65, 7, { repeat: true }),
    special("left", "←", "Left", 113, 1, { repeat: true }),
    special("up", "↑", "Up", 111, 1, { repeat: true }),
    special("down", "↓", "Down", 116, 1, { repeat: true }),
    special("right", "→", "Right", 114, 1, { repeat: true })
  ]
];

var unitsPerRow = 16;
var maxKeysPerRow = 15;

// xkb keycodes of the modifier keys (evdev + 8), for ydotool chords.
var MOD_CODES = { shift: 50, ctrl: 37, alt: 64, super: 133 };

