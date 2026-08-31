// Key layouts for the Omarchy on-screen keyboard.
//
// A *layout* is one language + variant (e.g. "Español · Full"). Each layout has
// its own layers: "base" (letters) and "symbols" (numbers / F-keys / symbols).
// The globe key switches layout; the ?123 key switches layer within a layout.
//
// Every printable key carries the text it types (plain + shifted) AND `code`,
// the xkb keycode (evdev + 8) of the PHYSICAL key it sits on. Injection uses
// the text (ydotool for US-layout characters, wtype for everything else — ñ,
// ä, Cyrillic, …); chords (Ctrl+C) use `code`, i.e. the physical position, so
// they keep working whatever letter the layout paints there.
//
// Adding a language is pure data: append to LAYOUTS below. `latin()` builds a
// QWERTY-family layout from three letter strings (+ optional extra keys);
// `nonlatin()` does the same for a script that reuses the ASCII number/symbol
// layer (Cyrillic, Greek, …). Rows always total 16 units so they line up.

.pragma library

var GLYPH = {
  keyboardClose: String.fromCodePoint(0xF030F),
  microphone: String.fromCodePoint(0xF036C),
  globe: String.fromCodePoint(0xF0AF9)   // mdi translate/globe
};

// ---- physical key codes (xkb = evdev + 8), by QWERTY position ----------------
// Three home rows; a layout supplies the character for each position and we zip
// it with these codes so chords land on the right physical key.
var ROW1_CODES = [24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35]; // q..p then [ ]
var ROW2_CODES = [38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48];     // a..l then ; '
var ROW3_CODES = [52, 53, 54, 55, 56, 57, 58, 59, 60];             // z..m then , .

// ---- key constructors --------------------------------------------------------
function ch(base, shifted, code, w) {
  return { id: base, label: base, shiftLabel: shifted, code: code, w: w || 1, letter: false };
}
function sym(c, code, w) {
  return { id: c, label: c, shiftLabel: c, code: code, w: w || 1, letter: false, sym: true };
}
function letter(lower, upper, code) {
  return { id: lower, label: lower, shiftLabel: upper || lower.toUpperCase(), code: code, w: 1, letter: true };
}
function special(id, label, name, code, w, opts) {
  opts = opts || {};
  return { id: id, label: label, name: name, code: code, w: w || 1, special: true, repeat: !!opts.repeat, letter: false };
}
function modifier(id, label, w) { return { id: id, label: label, mod: true, w: w || 1, special: true }; }
function action(id, label, w) { return { id: id, label: label, action: true, w: w || 1, special: true }; }

// Build a row of letters from a string of chars (lower) and the position codes.
// `upper` may be a matching string or null (auto-uppercase).
function letters(lower, upper, codes) {
  var out = [];
  for (var i = 0; i < lower.length; i++) {
    out.push(letter(lower[i], upper ? upper[i] : null, codes[i]));
  }
  return out;
}

// ---- shared pieces -----------------------------------------------------------
var dictationUnits = 1.25;

// Bottom row, shared by every layout. `langLabel` rides the globe key so the
// current language is visible; it opens the layout picker.
function bottomRow(layerLabel, langLabel, multiLayout) {
  var row = [
    modifier("ctrl", "ctrl", 1.25),
    modifier("super", "super", 1.25),
    modifier("alt", "alt", 1.25),
    action("layer", layerLabel, 1.25)
  ];
  if (multiLayout) row.push(action("globe", langLabel || GLYPH.globe, 1.25));
  row.push(action("dictate", GLYPH.microphone, dictationUnits));
  row.push(special("space", "", "space", 65, multiLayout ? 3.25 : 4.5));
  row.push(action("hide", GLYPH.keyboardClose, 1.25));
  row.push(special("left", "←", "Left", 113, 1, { repeat: true }));
  row.push(special("up", "↑", "Up", 111, 1, { repeat: true }));
  row.push(special("down", "↓", "Down", 116, 1, { repeat: true }));
  row.push(special("right", "→", "Right", 114, 1, { repeat: true }));
  return row;
}

// The full ASCII symbols/number layer (shared: numbers and symbols are the same
// whatever the letters are).
function symbolsLayer(langLabel, multiLayout) {
  return [
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
    bottomRow("abc", langLabel, multiLayout)
  ];
}

// ---- layout builders ---------------------------------------------------------
function rowWidth(row) { var t = 0; for (var i = 0; i < row.length; i++) t += (row[i].w || 1); return t; }

var NUMBER_ROW = [
  special("esc", "esc", "Escape", 9, 1),
  ch("`", "~", 49), ch("1", "!", 10), ch("2", "@", 11), ch("3", "#", 12), ch("4", "$", 13),
  ch("5", "%", 14), ch("6", "^", 15), ch("7", "&", 16), ch("8", "*", 17), ch("9", "(", 18),
  ch("0", ")", 19), ch("-", "_", 20), ch("=", "+", 21),
  special("backspace", "\u234C", "BackSpace", 22, 2, { repeat: true })
];

// A "full" QWERTY-family layout: number row + three letter rows + symbols. The
// flanking keys (\, enter, right shift) take whatever width is left so every
// row totals 16 regardless of how many letters the language packs in.
//   rows  = [top, home, bottom] lowercase strings
//   up    = optional matching uppercase strings
//   extra = optional { r3: [keys] } to override the bottom row's punctuation
function fullLayout(id, language, variant, langLabel, rows, up, extra) {
  extra = extra || {};

  function topRow(lower, upper) {
    var ks = [special("tab", "\u21E5", "Tab", 23, 2)].concat(letters(lower, upper, ROW1_CODES));
    var rem = 16 - 2 - lower.length;                       // room for [ ] \
    if (rem >= 4)      ks.push(ch("[", "{", 34, 1), ch("]", "}", 35, 1), ch("\\", "|", 51, rem - 2));
    else if (rem === 3) ks.push(ch("[", "{", 34, 1), ch("]", "}", 35, 1), ch("\\", "|", 51, 1));
    else if (rem === 2) ks.push(ch("[", "{", 34, 1), ch("]", "}", 35, 1));
    else if (rem === 1) ks.push(ch("\\", "|", 51, 1));
    return ks;
  }

  function homeRow(lower, upper) {
    var trailing = [ch(";", ":", 47), ch("'", "\"", 48)];
    var trW = 2, enterW = 16 - 2.25 - lower.length - trW;
    if (enterW < 1.5) { trailing = []; enterW = 16 - 2.25 - lower.length; }
    return [modifier("caps", "\u21EA", 2.25)]
      .concat(letters(lower, upper, ROW2_CODES)).concat(trailing)
      .concat([special("enter", "\u23CE", "Return", 36, enterW)]);
  }

  function shiftRow(lower, upper) {
    var trailing = extra.r3 !== undefined ? extra.r3 : [ch(",", "<", 59), ch(".", ">", 60), ch("/", "?", 61)];
    var trW = rowWidth(trailing);
    var rShiftW = Math.max(1, 16 - 2.75 - lower.length - trW);
    return [modifier("shift", "\u21E7", 2.75)]
      .concat(letters(lower, upper, ROW3_CODES)).concat(trailing)
      .concat([modifier("shift", "\u21E7", rShiftW)]);
  }

  var base = [NUMBER_ROW, topRow(rows[0], up && up[0]), homeRow(rows[1], up && up[1]),
              shiftRow(rows[2], up && up[2]), bottomRow("?123", langLabel, true)];
  return { id: id, language: language, variant: variant, label: langLabel,
           base: base, symbols: symbolsLayer(langLabel, true) };
}

// A "simplified" phone-style layout: letters only, bigger targets, numbers and
// symbols on the ?123 layer. Flanking keys fill each row to 16.
function simpleLayout(id, language, variant, langLabel, rows, up) {
  function fillRow(inner, leftW, rightId, rightLabel, rightName, rightCode, rightRepeat) {
    var used = rowWidth(inner) + leftW;
    var rW = Math.max(1.5, 16 - used - leftW);
    return { leftW: leftW, rW: rW };
  }
  function r0(lower, upper) {
    var mid = letters(lower, upper, ROW1_CODES);
    var side = (16 - rowWidth(mid)) / 2;
    return [special("tab", "\u21E5", "Tab", 23, side)].concat(mid)
      .concat([special("backspace", "\u234C", "BackSpace", 22, side, { repeat: true })]);
  }
  function r1(lower, upper) {
    var mid = letters(lower, upper, ROW2_CODES);
    var side = (16 - rowWidth(mid)) / 2;
    return [special("esc", "esc", "Escape", 9, side)].concat(mid)
      .concat([special("enter", "\u23CE", "Return", 36, side)]);
  }
  function r2(lower, upper) {
    var mid = letters(lower, upper, ROW3_CODES).concat([ch(",", ";", 59), ch(".", ":", 60)]);
    var side = (16 - rowWidth(mid)) / 2;
    return [modifier("shift", "\u21E7", side)].concat(mid).concat([modifier("shift", "\u21E7", side)]);
  }
  var base = [r0(rows[0], up && up[0]), r1(rows[1], up && up[1]), r2(rows[2], up && up[2]),
              bottomRow("?123", langLabel, true)];
  return { id: id, language: language, variant: variant, label: langLabel,
           base: base, symbols: symbolsLayer(langLabel, true) };
}

// ---- the catalog -------------------------------------------------------------
var LAYOUTS = [
  fullLayout("en-full", "English", "Full", "EN",
    ["qwertyuiop", "asdfghjkl", "zxcvbnm"]),

  simpleLayout("en-simple", "English", "Simplified", "EN",
    ["qwertyuiop", "asdfghjkl", "zxcvbnm"]),

  // Spanish: QWERTY + ñ on the home row.
  fullLayout("es-full", "Espa\u00F1ol", "Full", "ES",
    ["qwertyuiop", "asdfghjkl\u00F1", "zxcvbnm"]),

  // German QWERTZ + umlauts + ß.
  fullLayout("de-full", "Deutsch", "Full", "DE",
    ["qwertzuiop\u00FC", "asdfghjkl\u00F6\u00E4", "yxcvbnm"], null,
    { r3: [ch("\u00DF", "?", 20), ch(",", ";", 59), ch(".", ":", 60)] }),

  // Russian YTsUKEN - non-Latin, typed via wtype; chords land on the physical
  // QWERTY position under each key.
  fullLayout("ru-full", "\u0420\u0443\u0441\u0441\u043A\u0438\u0439", "Full", "\u0420\u0423",
    ["\u0439\u0446\u0443\u043A\u0435\u043D\u0433\u0448\u0449\u0437\u0445\u044A",
     "\u0444\u044B\u0432\u0430\u043F\u0440\u043E\u043B\u0434\u0436\u044D",
     "\u044F\u0447\u0441\u043C\u0438\u0442\u044C\u0431\u044E"])
];

// Map id -> layout, and an ordered list for the picker.
var layouts = {};
for (var _i = 0; _i < LAYOUTS.length; _i++) layouts[LAYOUTS[_i].id] = LAYOUTS[_i];
var layoutList = LAYOUTS.map(function (l) {
  return { id: l.id, language: l.language, variant: l.variant, label: l.label };
});
var defaultLayout = "en-full";

// Back-compat: `layers`/`base`/`rows` used to be the whole keyboard.
var base = layouts["en-full"].base;
var layers = { base: base, symbols: layouts["en-full"].symbols };
var rows = base;

var unitsPerRow = 16;
var MOD_CODES = { shift: 50, ctrl: 37, alt: 64, super: 133 };
