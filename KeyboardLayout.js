// Key layouts for the Omarchy on-screen keyboard — ANSI-grid rebuild.
//
// The board is 15 units wide. Modifier widths follow the real ANSI keyboard
// (Tab 1.5u, Caps 1.75u, Left-Shift 2.25u, Right-Shift 2.75u, Enter ~2.25u),
// and those stepped widths are what stagger the rows. Letters stay a true 1u;
// the right-hand key of each letter row (\ , Enter, Right-Shift) flexes to make
// the row total 15u, so a language with extra letters just shrinks that key.
//
// A *layout* is one language+variant (English QWERTY, Español, Deutsch QWERTZ…);
// the globe cycles the enabled ones. Within a layout, ?123 flips base<->symbols.
//
// Every printable key carries the text it types plus `code`, the xkb keycode
// (evdev+8) of the physical key it sits on: Latin injects via ydotool, the rest
// via wtype, and chords (Ctrl+C) use the physical position.

.pragma library

var unitsPerRow = 15;
var GLYPH = {
  close: String.fromCodePoint(0xF030F),
  mic:   String.fromCodePoint(0xF036C),
  globe: String.fromCodePoint(0xF059F)
};

// physical key codes (xkb = evdev+8), by QWERTY position (+ the [ ] ; ' , . tails)
var R1=[24,25,26,27,28,29,30,31,32,33,34,35];
var R2=[38,39,40,41,42,43,44,45,46,47,48];
var R3=[52,53,54,55,56,57,58,59,60,61];

function ch(b,s,code,w){return{id:b,label:b,shiftLabel:s,code:code,w:w||1,letter:false};}
function sym(c,code,w){return{id:c,label:c,shiftLabel:c,code:code,w:w||1,letter:false,sym:true};}
function letter(lo,up,code,w){return{id:lo,label:lo,shiftLabel:up||lo.toUpperCase(),code:code,w:w||1,letter:true};}
function special(id,label,name,code,w,opts){opts=opts||{};return{id:id,label:label,name:name,code:code,w:w||1,special:true,repeat:!!opts.repeat,letter:false};}
function modifier(id,label,w){return{id:id,label:label,mod:true,w:w||1,special:true};}
function action(id,label,w){return{id:id,label:label,action:true,w:w||1,special:true};}
function rowW(r){var t=0;for(var i=0;i<r.length;i++)t+=(r[i].w||1);return t;}
function letters(lo,up,codes,shiftMap){var o=[];for(var i=0;i<lo.length;i++){var k=letter(lo[i],up?up[i]:null,codes[i]);if(shiftMap&&shiftMap[lo[i]])k.shiftLabel=shiftMap[lo[i]];o.push(k);}return o;}
// Give every character key in a row an equal share of what the flex key does
// not take. Flex keys are clamped [floor,cap]; the LETTERS absorb the slack,
// so sparse rows get big letters instead of ballooned modifiers (audit S2).
function spread(chars,leftW,flexFloor,flexCap){
  var n=chars.length, raw=15-leftW-n;
  var flexW=Math.min(flexCap,Math.max(flexFloor,raw));
  var cw=Math.round((15-leftW-flexW)/n*1000)/1000;
  for(var i=0;i<n;i++)chars[i].w=cw;
  return Math.round(flexW*100)/100;
}

// ---- shared bottom row (centered space, symmetric flanks) -------------------
// Equalize the two flanks (the lighter one stretches to match), then give the
// space bar everything that is left: big, and centered by construction.
// `bw` on a key is its relative weight within its flank (default 1).
function balancedBottom(left,right,base){
  function wsum(a){var t=0;for(var i=0;i<a.length;i++)t+=(a[i].bw||1);return t;}
  var lw=wsum(left),rw=wsum(right);
  var T=base*Math.max(lw,rw);
  function apply(a,ws){var f=T/ws;for(var i=0;i<a.length;i++)a[i].w=Math.round((a[i].bw||1)*f*100)/100;}
  apply(left,lw);apply(right,rw);
  var space=special("space","","space",65,Math.round((15-2*T)*100)/100);
  return left.concat([space]).concat(right);
}
function bottomRow(layerLabel,langLabel,mic,rtl){
  // Globe sits just left of the space bar on every layout, so it never moves;
  // the mic takes the bottom-right corner (iOS-style) when voxtype is around.
  var left=[modifier("ctrl","ctrl"),modifier("alt","alt"),action("layer",layerLabel),action("globe",langLabel)];
  var right=rtl
    ? [ch("،","؛",59),ch(".",">",60),ch("/","؟",61)]
    : [ch(",","<",59),ch(".",">",60),ch("/","?",61)];
  if(mic)right.push(action("dictate",GLYPH.mic));
  return balancedBottom(left,right,1.15);
}

// ---- shared symbols/?123 layer ---------------------------------------------
function fkeys(){var o=[special("esc","esc","Escape",9,1)];
  var n=["F1","F2","F3","F4","F5","F6","F7","F8","F9","F10","F11","F12"];
  var c=[67,68,69,70,71,72,73,74,75,76,95,96];
  for(var i=0;i<12;i++)o.push(special("f"+(i+1),n[i],n[i],c[i]));
  o.push(special("backspace","⌫","BackSpace",22,2,{repeat:true}));return o;}
function symbolsRows(langLabel,mic,rtl){
  // Full punctuation coverage lives here: layouts that drop ; ' " from their
  // base rows (Deutsch, Русский, עברית, every import) rely on this plane.
  var r1=[special("tab","⇥","Tab",23,1.5),sym("`",49),sym("~",49),sym("[",34),sym("]",35),
    sym("{",34),sym("}",35),sym("<",59),sym(">",60),sym("|",51),
    sym("7",16),sym("8",17),sym("9",18),
    special("delete","⌦","Delete",119,1.5,{repeat:true})];
  var r2=[special("home","home","Home",110,1.5),sym(";",47),sym(":",47),sym("'",48),sym("\"",48),
    sym("_",20),sym("-",20),sym("+",21),sym("=",21),sym("%",14),
    sym("4",13),sym("5",14),sym("6",15),
    special("enter","⏎","Return",36,1.5)];
  var r3=[modifier("shift","⇧",2.5),special("end","end","End",115,1),modifier("super","super",1),
    sym("?",61),sym("!",10),
    special("left","←","Left",113,1,{repeat:true}),special("up","↑","Up",111,1,{repeat:true}),
    special("down","↓","Down",116,1,{repeat:true}),special("right","→","Right",114,1,{repeat:true}),
    sym("1",10),sym("2",11),sym("3",12),sym("0",19,1.5)];
  return [fkeys(),r1,r2,r3,bottomRow("abc",langLabel,mic,rtl)];
}

// ---- FULL layout builder (ANSI stagger + flex right key) --------------------
function numberRow(){
  return [special("esc","esc","Escape",9,1),
    ch("1","!",10),ch("2","@",11),ch("3","#",12),ch("4","$",13),ch("5","%",14),
    ch("6","^",15),ch("7","&",16),ch("8","*",17),ch("9","(",18),ch("0",")",19),
    ch("-","_",20),ch("=","+",21),special("backspace","⌫","BackSpace",22,2,{repeat:true})];
}
function fullBaseWide(lay,mic){
  // Rows carry their own punctuation; flanking modifiers are clamped and the
  // letters take the slack, so sparse rows widen letters, not modifiers.
  var r=lay.ltrs, langLabel=GLYPH.globe+" "+lay.label, sm=lay.shiftMap;
  var c1=letters(r[0],null,R1,sm);
  var w1=Math.round((15-1.5)/c1.length*1000)/1000;
  for(var i=0;i<c1.length;i++)c1[i].w=w1;
  var row1=[special("tab","⇥","Tab",23,1.5)].concat(c1);
  var c2=letters(r[1],null,R2,sm);
  var enterW=spread(c2,1.75,1.75,2.25);
  var row2=[modifier("caps","⇪",1.75)].concat(c2,[special("enter","⏎","Return",36,enterW)]);
  var c3=letters(r[2],null,R3,sm);
  var shiftW=spread(c3,2.25,1.75,2.75);
  var row3=[modifier("shift","⇧",2.25)].concat(c3,[modifier("shift","⇧",shiftW)]);
  return [numberRow(),row1,row2,row3,bottomRow("?123",langLabel,mic,lay.rtlPunct)];
}
function fullBase(lay,mic){
  if(lay.noPunct) return fullBaseWide(lay,mic);
  var r=lay.ltrs, langLabel=GLYPH.globe+" "+lay.label;
  // top row: letters + [ ] when they fit at 1u; \ takes a clamped share
  var c1=letters(r[0],null,R1);
  if(15-1.5-c1.length-2>=1) c1=c1.concat([ch("[","{",34),ch("]","}",35)]);
  var bsW=spread(c1,1.5,1,1.5);
  var row1=[special("tab","⇥","Tab",23,1.5)].concat(c1,[ch("\\","|",51,bsW)]);
  // home row: ; ' only when letters stay 1u with a decent enter (symbols has them)
  var c2=letters(r[1],null,R2);
  if(15-1.75-c2.length-2>=1.75) c2=c2.concat([ch(";",":",47),ch("'","\"",48)]);
  var enterW=spread(c2,1.75,1.75,2.25);
  var row2=[modifier("caps","⇪",1.75)].concat(c2,[special("enter","⏎","Return",36,enterW)]);
  // shift row
  var midR3=lay.deExtra?[ch("ß","?",20),ch(",","<",59),ch(".",">",60)]:[ch(",","<",59),ch(".",">",60),ch("/","?",61)];
  var c3=letters(r[2],null,R3).concat(midR3);
  var shiftW=spread(c3,2.25,1.75,2.75);
  var row3=[modifier("shift","⇧",2.25)].concat(c3,[modifier("shift","⇧",shiftW)]);
  return [numberRow(),row1,row2,row3,bottomRow("?123",langLabel,mic,lay.rtlPunct)];
}

function simpleBottom(layerLabel,langLabel,mic,rtl){
  var lk=action("layer",layerLabel); lk.bw=1.2;
  var left=[lk];
  if(mic)left.push(action("dictate",GLYPH.mic));
  left.push(action("globe",langLabel));
  var c=rtl?ch("\u060C","\u061B",59):ch(",",";",59); c.bw=0.8;
  var d=ch(".",":",60); d.bw=0.8;
  var e=special("enter","\u23CE","Return",36); e.bw=1.4;
  return balancedBottom(left,[c,d,e],1.5);
}

// ---- SIMPLIFIED layout (phone, big keys; rows auto-center) ------------------
function simpleBase(lay,mic){
  // Phone-style board for ANY layout: per-row letter width caps at 1.5u and
  // shrinks for dense scripts so every row fits the 15u board.
  var r=lay.ltrs, langLabel=GLYPH.globe+" "+lay.label, sm=lay.shiftMap;
  function fit(row,avail){var w=Math.min(1.5,Math.round(avail/row.length*1000)/1000);
    for(var i=0;i<row.length;i++)row[i].w=w;return row;}
  var row1=fit(letters(r[0],null,R1,sm),15);
  var row2=fit(letters(r[1],null,R2,sm),15);
  var row3=[modifier("shift","\u21E7",2)].concat(fit(letters(r[2],null,R3,sm),11));
  row3.push(special("backspace","\u232B","BackSpace",22,2,{repeat:true}));
  return [row1,row2,row3,simpleBottom("?123",langLabel,mic,lay.rtlPunct)];
}
function simpleSymbols(lay,mic){
  var bl=1.5, langLabel=GLYPH.globe+" "+lay.label;
  var row1=[special("esc","esc","Escape",9,1.5)]
    .concat(["1","2","3","4","5","6","7","8","9","0"].map(function(d,i){return sym(d,[10,11,12,13,14,15,16,17,18,19][i],1.2);}))
    .concat([special("tab","⇥","Tab",23,1.5)]);
  var row2=[sym("@",11,bl),sym("#",12,bl),sym("$",13,bl),sym("_",20,bl),sym("&",16,bl),sym("-",20,bl),sym("+",21,bl),sym("(",18,bl),sym(")",19,bl),sym("=",21,bl)];
  var s3=1.3;
  var row3=[sym("'",48,s3),sym("\"",48,s3),sym(";",47,s3),sym(":",47,s3),sym("!",10,s3),sym("?",61,s3),
    sym("/",61,s3),sym("\\",51,s3),sym("<",59,s3),sym(">",60,s3)];
  row3.push(special("backspace","⌫","BackSpace",22,2,{repeat:true}));
  return [row1,row2,row3,simpleBottom("abc",langLabel,mic,lay.rtlPunct)];
}

// ---- catalog (bundled locally for now; a GitHub catalog comes later) --------
var CATALOG=[
  {id:"en-qwerty", language:"English",  variant:"QWERTY",     label:"EN", v:"full",   ltrs:["qwertyuiop","asdfghjkl","zxcvbnm"]},
  {id:"es-qwerty", language:"Español",  variant:"QWERTY",     label:"ES", v:"full",   ltrs:["qwertyuiop","asdfghjklñ","zxcvbnm"]},
  {id:"de-qwertz", language:"Deutsch",  variant:"QWERTZ",     label:"DE", v:"full",   ltrs:["qwertzuiopü","asdfghjklöä","yxcvbnm"], deExtra:true},
  {id:"fr-azerty", language:"Français", variant:"AZERTY",     label:"FR", v:"full",   ltrs:["azertyuiop","qsdfghjklm","wxcvbn"]},
  {id:"en-dvorak", language:"English",  variant:"Dvorak",     label:"DV", v:"full", noPunct:true,
    ltrs:["',.pyfgcrl/=","aoeuidhtns-",";qjkxbmwvz"],
    shiftMap:{"'":"\"",",":"<",".":">","/":"?","=":"+","-":"_",";":":"}},
  {id:"he-standard",language:"עברית",   variant:"Standard",   label:"עב", v:"full", noPunct:true,
    ltrs:["/'קראטוןםפ][","שדגכעיחלךף,","זסבהנמצתץ."],
    shiftMap:{"/":"?","'":"\"",",":"<",".":">","[":"{","]":"}"}},
  {id:"ask-hebrew", language:"עברית", variant:"AnySoftKeyboard", label:"עב", v:"full", noPunct:true, ltrs:["ץקראטוןםפ", "שדגכעיחלךף", "זסבהנמצת"]},
  {id:"ask-arabic", rtlPunct:true, language:"العربية", variant:"AnySoftKeyboard", label:"ع", v:"full", noPunct:true, ltrs:["ضصقفغعهخحج", "شسيبلاتنمك", "ظطذدزروةث"]},
  {id:"ask-greek", language:"Ελληνικά", variant:"AnySoftKeyboard", label:"ΕΛ", v:"full", noPunct:true, ltrs:[";ςερτυθιοπ", "ασδφγηξκλ", "ζχψωβνμ"]},
  {id:"ask-russian2", language:"Русский", variant:"AnySoftKeyboard", label:"РУ", v:"full", noPunct:true, ltrs:["йцукенгшщзхъ", "фывапролджэ", "ячсмитьбюё"]},
  {id:"ask-persian", rtlPunct:true, language:"فارسی", variant:"AnySoftKeyboard", label:"ف", v:"full", noPunct:true, ltrs:["ضصثقفغعهخحج", "شسیبلاتنمکگ", "ظطژزرذدپوچ"]}
];

function buildLayout(lay,mic){
  var base = lay.v==='simple'? simpleBase(lay,mic) : fullBase(lay,mic);
  var symbols = lay.v==='simple'? simpleSymbols(lay,mic) : symbolsRows(GLYPH.globe+" "+lay.label,mic,lay.rtlPunct);
  var altMap = LAYALTS[lay.id] || LAYALTS[lay.id.replace(/-simple$/, "")];
  applyAlts(base, altMap);
  applyAlts(symbols, altMap);
  return { id:lay.id, language:lay.language, variant:lay.variant, label:lay.label, base:base, symbols:symbols };
}

// layouts() rebuilds with the current mic setting (widths depend on it).
// Every full layout gets an auto-generated phone-style sibling.
function expandCatalog(){
  var out=[];
  for(var i=0;i<CATALOG.length;i++){
    var e=CATALOG[i]; out.push(e);
    var c={}; for(var p in e) c[p]=e[p];
    c.id=e.id+"-simple"; c.v="simple"; c.variant=e.variant+" \u00b7 Simplified";
    out.push(c);
  }
  return out;
}
var EXPANDED=expandCatalog();
function make(mic){
  var m={}; for(var i=0;i<EXPANDED.length;i++) m[EXPANDED[i].id]=buildLayout(EXPANDED[i],mic); return m;
}
var catalogList = EXPANDED.map(function(c){return {id:c.id,language:c.language,variant:c.variant,label:c.label,v:c.v};});
var defaultLayout = "en-qwerty";
var defaultEnabled = ["en-qwerty","en-qwerty-simple","es-qwerty"];

// Long-press alternates: per-layout maps (AnySoftKeyboard popupCharacters +
// hand-authored accents); SHARED_ALTS applies on every layout.
var LAYALTS={"he-standard": {"ש": "₪"}, "ask-hebrew": {"ש": "₪"}, "ask-arabic": {"ض": "ًٌٍَُِ", "ف": "ڤ", "ج": "چ", "ي": "ىئ", "ب": "پ", "ا": "أإآء", "ك": "گ", "ظ": "؟،؛", "ز": "ژ", "و": "ؤ"}, "ask-greek": {"ε": "έ", "υ": "ύϋΰ", "ι": "ίϊΐ", "ο": "ό", "α": "ά", "η": "ή", "ω": "ώ"}, "ask-russian2": {"й": "їӣӥ", "ц": "ћџҵ", "у": "ўүұӯӱӳ", "к": "ќқҝҟҡӄ", "е": "ёјҽҿӗә", "н": "њңҥӈ", "г": "ґѓғҕ", "ш": "һѡѽѿ", "з": "ѕҙӟӡѯ", "х": "ҩҳ", "ъ": "ѣ҂҃҄҅҆", "ф": "ѱ", "ы": "іӹ", "в": "ѵѷ", "а": "ӑӓӕ", "п": "ҧ", "о": "ӧөӫѳѻ", "л": "љѧѩ", "ж": "ђҗӂӝѫѭ", "э": "є", "ч": "ҷҹӌӵ", "с": "ҫҁ", "т": "ҭ", "ю": "ѥ"}, "ask-persian": {"ج": "٫", "س": "٪", "ی": "ئيى", "ا": "آءأإٱ", "ت": "ة", "گ": "ك", "ر": "﷼", "و": "ؤ", "،": "؛,;َُِ", "‌": "'\"\\uce", "؟": "/@\\ubf", ".": "!:-\\u_"}, "es-qwerty": {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "úü"}, "fr-azerty": {"a": "àâ", "e": "éèêë", "i": "îï", "o": "ôœ", "u": "ùûü", "c": "ç", "y": "ÿ"}, "de-qwertz": {"e": "€", "s": "ß"}};
var SHARED_ALTS={"?":"¿","!":"¡","$":"€£¥","-":"–—","'":"‘’","\"":"“”«»","*":"†•","=":"≈≠"};
function applyAlts(rows,map){
  for(var i=0;i<rows.length;i++)for(var j=0;j<rows[i].length;j++){
    var k=rows[i][j];
    if(!k.label||k.special||k.mod||k.action)continue;
    var a=(map&&map[k.label])||SHARED_ALTS[k.label];
    if(a)k.alts=a;
  }
  return rows;
}

var MOD_CODES = { shift:50, ctrl:37, alt:64, super:133 };
