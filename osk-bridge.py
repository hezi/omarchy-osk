#!/usr/bin/env python3
"""Bridge between fcitx5's DBus virtual-keyboard backend and the Omarchy OSK plugin.

fcitx5 ships a "DBus Virtual Keyboard" UI addon: when some process owns the bus
name org.fcitx.Fcitx5.VirtualKeyboard, fcitx5 switches to on-screen-keyboard
mode and calls ShowVirtualKeyboard / HideVirtualKeyboard on that owner whenever
a text field gains or loses focus (Wayland text-input-v3 clients such as
Chromium and foot, plus GTK/Qt apps through their fcitx5 IM modules).

This process owns that name and speaks a tiny line protocol with the QML side:

  stdout (bridge -> QML), one JSON object per line:
    {"event": "ready"}
    {"event": "show"} / {"event": "hide"}
    {"event": "preedit", "text": "...", "caret": n}

  stdin (QML -> bridge), one JSON object per line:
    {"cmd": "type", "text": "Hi!"}                    type literal text
    {"cmd": "key",  "name": "Return", "mods": [...]}  press a named key (X keysym name)
    {"cmd": "chord", "code": 54, "mods": ["ctrl"]}   key (xkb keycode) with modifiers held
    {"cmd": "visible", "value": true}                 tell fcitx5 whether the panel is shown

Typing does NOT go through fcitx5: its virtual-keyboard forwarding drops the
modifier state and can't commit keysyms without a keycode (verified on
fcitx5 5.1.21), so Shift/Ctrl combos never arrive. Instead:
  * anything that exists on the US keyboard layout - letters, digits,
    punctuation (shifted or not), named keys, chords - goes through ydotool
    (kernel uinput, so every app and the compositor see an ordinary keyboard:
    Chromium types ",./<>?" correctly, Super+Enter hits Hyprland binds,
    Ctrl+C reaches the app). ydotoold runs as a user service; keyd is told to
    ignore its device.
  * characters outside that layout (é, emoji, ...) fall back to wtype, which
    builds a custom keymap on the fly. Terminals honour that keymap; Chromium
    only partly does, which is why wtype is not used for plain punctuation.
Calls are serialised on one worker thread so key order is kept.
"""
import ctypes
import json
import os
import queue
import re
import signal
import stat
import subprocess
import sys
import threading
import time

import gi
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from osk_engine import Engine
from osk_files import read_frames

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

VK_NAME = "org.fcitx.Fcitx5.VirtualKeyboard"
VK_PATH = "/org/fcitx/virtualkeyboard/impanel"
VK_IFACE = "org.fcitx.Fcitx5.VirtualKeyboard1"

BACKEND_NAME = "org.fcitx.Fcitx5"
BACKEND_PATH = "/virtualkeyboard"
BACKEND_IFACE = "org.fcitx.Fcitx5.VirtualKeyboardBackend1"
# fcitx5's own control surface for the on-screen keyboard (Show/Hide/Toggle).
SERVICE_IFACE = "org.fcitx.Fcitx.VirtualKeyboard1"

VK_XML = f"""
<node>
  <interface name="{VK_IFACE}">
    <method name="ShowVirtualKeyboard"/>
    <method name="HideVirtualKeyboard"/>
    <method name="UpdateCandidateArea">
      <arg type="a(ss)" name="candidates" direction="in"/>
      <arg type="b" name="hasPrev" direction="in"/>
      <arg type="b" name="hasNext" direction="in"/>
      <arg type="i" name="pageIndex" direction="in"/>
      <arg type="i" name="cursorIndex" direction="in"/>
    </method>
    <method name="UpdatePreeditArea"><arg type="s" name="text" direction="in"/></method>
    <method name="UpdatePreeditCaret"><arg type="i" name="pos" direction="in"/></method>
    <method name="NotifyIMActivated"><arg type="s" name="im" direction="in"/></method>
    <method name="NotifyIMDeactivated"><arg type="s" name="im" direction="in"/></method>
    <method name="NotifyIMListChanged"/>
  </interface>
</node>
"""


LOG_PATH = os.path.expanduser("~/.local/state/omarchy/osk-bridge.log")
# The debug mirror can contain typed/preedit content, so it is OFF by default
# and only written when OSK_BRIDGE_LOG=1 is set in the environment.
_LOG_ENABLED = os.environ.get("OSK_BRIDGE_LOG") == "1"
_LOG_MAX = 1024 * 1024

# Bounds on everything that crosses the IPC boundary or is surfaced to the UI.
_MAX_LINE = 64 * 1024          # one IPC line from stdin
_MAX_TEXT = 1024               # injected text / picked word
_MAX_KEYMAP = 256              # keymap entries
_MAX_SWIPE_POINTS = 512        # points in one swipe path
_MAX_SUGGEST_N = 8             # suggestions surfaced to the UI
_MAX_SUGGEST_LEN = 64          # per-suggestion string length
_QUEUE_MAX = 256               # pending injections
_MAX_PENDING = 256             # stdin lines parsed but not yet handled on the main loop
_INJECT_TIMEOUT = 2.0          # seconds one injector may run before its whole group is killed

# Injectors are packaged executables; bind their paths instead of trusting PATH.
YDOTOOL = "/usr/bin/ydotool"
WTYPE = "/usr/bin/wtype"


def emit(**payload):
    line = json.dumps(payload)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    if _LOG_ENABLED:
        _debug_log(time.strftime("%H:%M:%S ") + line + "\n")


def _debug_log(text):
    # Opt-in only. Private (0600), never follows a symlink, refuses a file that
    # is not a regular file we own, and self-rotates so it cannot grow unbounded.
    try:
        os.makedirs(os.path.dirname(LOG_PATH), mode=0o700, exist_ok=True)
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    except OSError:
        return
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            return
        if st.st_size > _LOG_MAX:
            os.close(fd)
            try:
                os.replace(LOG_PATH, LOG_PATH + ".1")
            except OSError:
                pass
            fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.write(fd, text.encode("utf-8", "replace"))
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


class Bridge:
    def __init__(self):
        self.conn = None
        self.preedit = ""
        self.arming = False
        self.arm_sync = True
        self.visible = False          # is the QML keyboard on screen
        self.auto_allowed = False     # may fcitx5 focus events show it right now
        self.suppress_until = 0.0     # ignore fcitx5 Show/Hide until this monotonic time
        self.last_inject = 0.0
        self.inject_queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._child = None                 # in-flight injector (Popen), for cleanup
        self._child_lock = threading.Lock()
        threading.Thread(target=self.inject_worker, daemon=True).start()
        # ---- suggestions / autocorrect / glide typing -----------------------
        self.engine = Engine(log=lambda *a: emit(event="engine", msg=" ".join(str(x) for x in a)))
        self.cur_word = ""
        self.last_commit = None        # word we placed (swipe/autocorrect/pick), for replacement
        self.feat = {"suggest": False, "autocorrect": False, "glide": False}
        self.app_ok = True             # autocorrect disabled for terminals
        self.TERMINALS = {"foot", "footclient", "kitty", "alacritty", "org.wezfurlong.wezterm",
                          "com.mitchellh.ghostty", "ghostty", "xterm", "st"}
        # fcitx5 leaves on-screen-keyboard mode as soon as it sees a key from a
        # real keyboard (see after_inject); while the keyboard is allowed to
        # auto-show, re-arm now and then so the next focus change reaches us.
        GLib.timeout_add_seconds(5, self.periodic_arm)

    # --- arming ---------------------------------------------------------------
    # Owning the bus name only makes fcitx5 *notice* us. It switches into
    # on-screen-keyboard mode - exporting VirtualKeyboardBackend1 and sending
    # focus-driven Show/Hide - the first time its own ShowVirtualKeyboard
    # service method is called. So call Show and then Hide once, and swallow
    # the pair of callbacks that produces.
    def arm(self, sync=True):
        if self.conn is None:
            return False
        self.arming = True
        self.arm_sync = sync
        for method in ("ShowVirtualKeyboard", "HideVirtualKeyboard"):
            self.conn.call(
                BACKEND_NAME, BACKEND_PATH, SERVICE_IFACE, method, None,
                None, Gio.DBusCallFlags.NONE, 1000, None, None,
            )
        GLib.timeout_add(600, self._armed)
        return False

    def _armed(self):
        self.arming = False
        if self.arm_sync:
            emit(event="armed")
            self.sync_focus()
        return False

    def periodic_arm(self):
        # Only while hidden: while our keyboard is up, after_inject keeps the
        # mode alive, and arming would needlessly poke fcitx5 on every tick.
        if self.auto_allowed and not self.visible and not self.arming \
                and time.monotonic() - self.last_inject > 2:
            self.arm(sync=False)
        return True

    # Every key we inject looks like a physical keyboard to fcitx5, which then
    # hides its virtual keyboard (a HideVirtualKeyboard call to us) and drops
    # back to physical-keyboard mode, after which focus changes no longer
    # produce Show calls. So: ignore its Show/Hide for a moment after each
    # injection, and if our keyboard is on screen tell fcitx5 it is shown
    # again, which also restores on-screen-keyboard mode.
    # ---- typing intelligence -------------------------------------------------
    WORD_CHARS = "'-"

    def inject_text_internal(self, text):
        self.suppress_until = time.monotonic() + 0.9
        argv = self.ydotool_text_argv(text) or self.wtype_argv({"cmd": "type", "text": text})
        if argv:
            self._enqueue(argv)

    def inject_backspaces(self, n):
        if n <= 0:
            return
        self.suppress_until = time.monotonic() + 0.9
        self._enqueue([YDOTOOL, "key", "--key-delay", "4"] + ["14:1", "14:0"] * n)

    def _enqueue(self, argv):
        try:
            self.inject_queue.put_nowait(argv)
        except queue.Full:
            pass  # drop under a flood rather than grow without bound

    def _cap_cands(self, cands):
        return [str(c)[:_MAX_SUGGEST_LEN] for c in list(cands)[:_MAX_SUGGEST_N]]

    def _cap_args(self, args):
        return [str(a)[:_MAX_SUGGEST_LEN] for a in list(args)[:_MAX_SUGGEST_N]]

    def emit_suggest(self):
        if not self.feat["suggest"]:
            return
        cands = self.engine.suggest(self.cur_word) if self.cur_word else []
        emit(event="suggest", word=self.cur_word[:_MAX_TEXT], cands=self._cap_cands(cands))

    def track_char(self, ch):
        """Follow what the user types; on a space, maybe autocorrect the word.
        Returns True if the injection was replaced (caller must not inject)."""
        if not (self.feat["suggest"] or self.feat["autocorrect"]):
            return False
        if ch.isalpha() or (ch in self.WORD_CHARS and self.cur_word):
            self.cur_word += ch
            self.last_commit = None
            self.emit_suggest()
            return False
        if ch == " ":
            w, self.cur_word = self.cur_word, ""
            handled = False
            if (w and len(w) >= 2 and self.feat["autocorrect"] and self.app_ok
                    and not w.isupper()):
                fix = self.engine.correct(w)
                if fix:
                    self.inject_backspaces(len(w))
                    self.inject_text_internal(fix + " ")
                    self.last_commit = (fix, w)
                    emit(event="autocorrected", frm=w[:_MAX_TEXT], to=fix[:_MAX_TEXT])
                    handled = True
            if not handled and w:
                self.last_commit = None
            self.emit_suggest()
            return handled
        # punctuation, digits, anything else: word boundary, no correction
        self.cur_word = ""
        self.emit_suggest()
        return False

    def track_key(self, name):
        if not (self.feat["suggest"] or self.feat["autocorrect"]):
            return
        if name == "BackSpace":
            if self.cur_word:
                self.cur_word = self.cur_word[:-1]
            else:
                self.last_commit = None
        else:
            self.cur_word = ""
            self.last_commit = None
        self.emit_suggest()

    def do_pick(self, word):
        if self.cur_word:
            self.inject_backspaces(len(self.cur_word))
            self.inject_text_internal(word + " ")
            self.cur_word = ""
        elif self.last_commit:
            self.inject_backspaces(len(self.last_commit[0]) + 1)
            self.inject_text_internal(word + " ")
        else:
            self.inject_text_internal(word + " ")
        self.last_commit = (word, None)
        self.emit_suggest()

    def do_swipe(self, path):
        if not self.feat["glide"]:
            return
        pts = []
        for q in path[:_MAX_SWIPE_POINTS]:
            if isinstance(q, (list, tuple)) and len(q) >= 2:
                try:
                    pts.append((float(q[0]), float(q[1])))
                except (TypeError, ValueError):
                    pass
        cands = self.engine.decode_swipe(pts)
        if not cands:
            return
        lead = "" if self.cur_word == "" else " "
        self.cur_word = ""
        self.inject_text_internal(lead + cands[0] + " ")
        self.last_commit = (cands[0], None)
        emit(event="swiped", word=str(cands[0])[:_MAX_SUGGEST_LEN], cands=self._cap_cands(cands[1:]))

    def after_inject(self):
        self.last_inject = time.monotonic()
        self.suppress_until = self.last_inject + 0.6
        if self.visible and self.conn is not None:
            self.conn.call(
                BACKEND_NAME, BACKEND_PATH, SERVICE_IFACE, "ShowVirtualKeyboard", None,
                None, Gio.DBusCallFlags.NONE, 1000, None, None,
            )
        return False

    # fcitx5 fires a Show whenever a text-capable client takes focus - including
    # the shell's OWN popups (a bar popup that grabs keyboard focus for its key
    # navigation, like the tablet panel, focuses `program:quickshell`). That is
    # never something to type into with this keyboard, so ignore a Show whose
    # focused input context belongs to the shell.
    SHELL_PROGRAMS = ("quickshell",)

    def shell_has_focus(self):
        if self.conn is None:
            return False
        try:
            reply = self.conn.call_sync(
                BACKEND_NAME, "/controller", "org.fcitx.Fcitx.Controller1", "DebugInfo",
                None, None, Gio.DBusCallFlags.NONE, 500, None,
            )
            info = reply.unpack()[0]
        except Exception:
            return False
        # Find the line of the focused IC and read its program.
        for line in info.splitlines():
            if "focus:1" not in line:
                continue
            m = re.search(r"program:(\S+)", line)
            return bool(m and m.group(1) in self.SHELL_PROGRAMS)
        return False

    # fcitx5 only reports focus *transitions*. A field that was already focused
    # when this bridge started (the terminal you were typing in, say) would
    # never be reported until focus left and came back - so after arming, ask
    # fcitx5 which input contexts exist and whether one has focus right now.
    def sync_focus(self):
        if self.conn is None:
            return
        try:
            reply = self.conn.call_sync(
                BACKEND_NAME, "/controller", "org.fcitx.Fcitx.Controller1", "DebugInfo",
                None, None, Gio.DBusCallFlags.NONE, 1000, None,
            )
            info = reply.unpack()[0]
            focused = "focus:1" in info and not self.shell_has_focus()
            emit(event="show" if focused else "hide", source="sync")
        except Exception as exc:
            emit(event="error", message="sync_focus: %s" % exc)

    # --- fcitx5 -> us ---------------------------------------------------------
    def on_method_call(self, conn, sender, path, iface, method, params, invocation):
        args = params.unpack() if params is not None else ()
        swallow = self.arming or time.monotonic() < self.suppress_until
        if method == "ShowVirtualKeyboard":
            if not swallow and not self.shell_has_focus():
                emit(event="show")
        elif method == "HideVirtualKeyboard":
            if not swallow:
                emit(event="hide")
        elif method == "UpdatePreeditArea":
            # Session-bus callers are unauthenticated beyond "same user": cap.
            self.preedit = str(args[0])[:_MAX_TEXT]
            emit(event="preedit", text=self.preedit)
        elif method == "UpdatePreeditCaret":
            emit(event="preedit", text=self.preedit, caret=int(args[0]))
        elif method == "NotifyIMActivated":
            emit(event="notify", method=method, args=self._cap_args(args))
            if not swallow and not self.shell_has_focus():
                emit(event="show")
        elif method == "NotifyIMDeactivated":
            emit(event="notify", method=method, args=self._cap_args(args))
            if not swallow:
                emit(event="hide")
        elif method.startswith("Notify"):
            emit(event="notify", method=method, args=self._cap_args(args))
        # Candidate lists are irrelevant for keyboard-us.
        invocation.return_value(None)

    def on_bus_acquired(self, conn, name):
        self.conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(VK_XML)
        conn.register_object(VK_PATH, node.interfaces[0], self.on_method_call, None, None)

    def on_name_acquired(self, conn, name):
        emit(event="ready", name=name)
        # fcitx5 notices the new owner asynchronously (up to ~1s after a shell
        # restart) and only honours arming once its addon has resumed, so arm a
        # few times; the Show/Hide pair is idempotent and its callbacks are
        # swallowed while `arming` is set.
        for delay in (300, 2000, 6000):
            GLib.timeout_add(delay, self.arm)

    def on_fcitx_appeared(self, conn, name, owner):
        # fcitx5 (re)started: it sees our name at startup but still needs arming.
        GLib.timeout_add(1500, self.arm)

    def on_name_lost(self, conn, name):
        # A newer bridge instance (plugin reload) has replaced us: leave rather
        # than linger as a stale process fighting over the name.
        emit(event="name_lost", name=name)
        sys.exit(0)

    # --- us -> fcitx5 ---------------------------------------------------------
    def backend_call(self, method, params):
        if self.conn is None:
            return
        self.conn.call(
            BACKEND_NAME, BACKEND_PATH, BACKEND_IFACE, method, params,
            None, Gio.DBusCallFlags.NONE, 1000, None, None,
        )

    # --- key injection (wtype) --------------------------------------------------
    MODS = {"shift": "shift", "ctrl": "ctrl", "alt": "alt", "super": "logo"}

    @staticmethod
    def wtype_argv(cmd):
        mods = [Bridge.MODS[m] for m in cmd.get("mods", []) if m in Bridge.MODS]
        argv = [WTYPE]
        for m in mods:
            argv += ["-M", m]
        if cmd["cmd"] == "type":
            argv += ["--", cmd["text"]]
        elif cmd["cmd"] == "key":
            argv += ["-k", cmd["name"]]
        elif cmd["cmd"] == "char":
            argv += ["--", cmd["text"]]
        else:
            return None
        for m in reversed(mods):
            argv += ["-m", m]
        return argv

    # evdev codes (xkb keycode - 8) for ydotool.
    MOD_EVDEV = {"shift": 42, "ctrl": 29, "alt": 56, "super": 125}

    # US layout: character -> (evdev code, needs shift).
    US_KEYS = {}
    for _row, _base in (("`1234567890-=", 41), ("qwertyuiop[]", 16), ("asdfghjkl;'", 30), ("zxcvbnm,./", 44)):
        for _i, _c in enumerate(_row):
            US_KEYS[_c] = (_base + _i if _row[0] != "`" else (41 if _i == 0 else 1 + _i), False)
    US_KEYS["\\"] = (43, False)
    US_KEYS[" "] = (57, False)
    US_KEYS["\n"] = (28, False)
    US_KEYS["\t"] = (15, False)
    for _lower, _upper in zip("`1234567890-=[];',./\\", "~!@#$%^&*()_+{}:\"<>?|"):
        US_KEYS[_upper] = (US_KEYS[_lower][0], True)
    for _c in "abcdefghijklmnopqrstuvwxyz":
        US_KEYS[_c.upper()] = (US_KEYS[_c][0], True)

    NAMED_EVDEV = {
        "BackSpace": 14, "Tab": 15, "Return": 28, "Escape": 1, "space": 57, "Delete": 111,
        "Left": 105, "Right": 106, "Up": 103, "Down": 108, "Home": 102, "End": 107,
        "Page_Up": 104, "Page_Down": 109, "Insert": 110,
        "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66,
        "F9": 67, "F10": 68, "F11": 87, "F12": 88, "Print": 99, "Menu": 127,
    }

    @staticmethod
    def ydotool_text_argv(text):
        """A single ydotool invocation typing `text`, or None if a character is
        not on the US layout (the caller then falls back to wtype)."""
        seq = []
        for ch in text:
            if ch not in Bridge.US_KEYS:
                return None
            code, shift = Bridge.US_KEYS[ch]
            if shift:
                seq += ["42:1", "%d:1" % code, "%d:0" % code, "42:0"]
            else:
                seq += ["%d:1" % code, "%d:0" % code]
        return [YDOTOOL, "key", "--key-delay", "6"] + seq

    @staticmethod
    def ydotool_named_argv(name, mods):
        code = Bridge.NAMED_EVDEV.get(name)
        if code is None:
            return None
        m = [Bridge.MOD_EVDEV[x] for x in mods if x in Bridge.MOD_EVDEV]
        seq = ["%d:1" % x for x in m] + ["%d:1" % code, "%d:0" % code] + ["%d:0" % x for x in reversed(m)]
        return [YDOTOOL, "key", "--key-delay", "8"] + seq

    @staticmethod
    def ydotool_argv(cmd):
        mods = [Bridge.MOD_EVDEV[m] for m in cmd.get("mods", []) if m in Bridge.MOD_EVDEV]
        code = int(cmd["code"]) - 8
        if code <= 0:
            return None
        seq = ["%d:1" % m for m in mods] + ["%d:1" % code, "%d:0" % code] + ["%d:0" % m for m in reversed(mods)]
        return [YDOTOOL, "key", "--key-delay", "8"] + seq

    def inject_worker(self):
        while True:
            argv = self.inject_queue.get()
            if argv is None:
                return
            try:
                self._run_injector(argv)
            except Exception as exc:
                emit(event="error", message=str(exc))
            GLib.idle_add(self.after_inject)

    def _run_injector(self, argv):
        """Run one injector in its own session so a timeout kills every
        descendant (killpg), not only the direct child."""
        p = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._child_lock:
            self._child = p
        try:
            p.wait(timeout=_INJECT_TIMEOUT)
        except subprocess.TimeoutExpired:
            _killpg(p)
            emit(event="error", message="injector timed out: %s" % argv[0])
        finally:
            with self._child_lock:
                self._child = None

    def shutdown(self):
        """Stop the injector and kill any in-flight injector group."""
        with self._child_lock:
            p = self._child
        if p is not None:
            _killpg(p)
        try:
            self.inject_queue.put_nowait(None)
        except queue.Full:
            pass

    def handle_command(self, line):
        if not line:
            return False
        try:
            cmd = json.loads(line)
            kind = cmd.get("cmd")
            if kind in ("type", "key", "char", "chord"):
                self.suppress_until = time.monotonic() + 0.9
            if kind == "type":
                text = str(cmd.get("text", ""))[:_MAX_TEXT]
                if len(text) == 1 and self.track_char(text):
                    return False          # autocorrect swallowed the space and injected itself
                argv = self.ydotool_text_argv(text) or self.wtype_argv(cmd)
                if argv:
                    self._enqueue(argv)
            elif kind == "key":
                self.track_key(cmd["name"])
                argv = self.ydotool_named_argv(cmd["name"], cmd.get("mods", [])) or self.wtype_argv(cmd)
                if argv:
                    self._enqueue(argv)
            elif kind == "char":
                # A character with modifiers held: a chord on its US-layout key.
                key = self.US_KEYS.get(cmd["text"])
                if key:
                    mods = list(cmd.get("mods", [])) + (["shift"] if key[1] else [])
                    argv = self.ydotool_argv({"code": key[0] + 8, "mods": mods})
                else:
                    argv = self.wtype_argv(cmd)
                if argv:
                    self._enqueue(argv)
            elif kind == "chord":
                self.cur_word = ""
                self.last_commit = None
                argv = self.ydotool_argv(cmd)
                if argv:
                    self._enqueue(argv)
            elif kind == "visible":
                self.visible = bool(cmd.get("value"))
                self.backend_call("ProcessVisibilityEvent", GLib.Variant("(b)", (self.visible,)))
            elif kind == "lang":
                self.engine.set_lang(cmd.get("value", "english"),
                    on_ready=lambda l, n: emit(event="dict", lang=l, words=n))
            elif kind == "keymap":
                keys = cmd.get("keys", {})
                if isinstance(keys, dict) and len(keys) <= _MAX_KEYMAP:
                    self.engine.set_keymap(keys, cmd.get("unit", 60))
            elif kind == "features":
                for k in ("suggest", "autocorrect", "glide"):
                    if k in cmd:
                        self.feat[k] = bool(cmd[k])
                self.emit_suggest()
            elif kind == "appclass":
                self.app_ok = str(cmd.get("value", "")).lower() not in self.TERMINALS
            elif kind == "swipe":
                path = cmd.get("path", [])
                if isinstance(path, list):
                    self.do_swipe(path)
            elif kind == "pick":
                self.do_pick(str(cmd.get("word", ""))[:_MAX_TEXT])
            elif kind == "autoAllowed":
                self.auto_allowed = bool(cmd.get("value"))
                if self.auto_allowed:
                    self.arm(sync=False)
            elif kind == "arm":
                self.arm(sync=bool(cmd.get("sync", False)))
        except Exception as exc:  # keep the bridge alive on a bad line
            emit(event="error", message=str(exc), line=line)
        return False  # one-shot idle callback


def _killpg(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.wait(timeout=1)
    except Exception:
        pass


def _die_with_parent():
    """Ask the kernel to SIGTERM us if the shell that spawned us disappears,
    so an orphaned bridge (and its injector group) never lingers."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)      # PR_SET_PDEATHSIG
    except Exception:
        pass


def stdin_reader(bridge, loop):
    # Lines parsed here are handed to the GLib main loop one idle callback each;
    # the semaphore caps how many can be outstanding, so a producer faster than
    # the main loop stalls (bounded pipe) instead of growing an unbounded queue.
    pending = threading.BoundedSemaphore(_MAX_PENDING)

    def handle(line):
        try:
            bridge.handle_command(line)
        finally:
            pending.release()
        return False

    def on_line(line):
        if pending.acquire(timeout=5.0):
            GLib.idle_add(handle, line)
        else:
            emit(event="error", message="dropped command: main loop not draining")

    def on_overflow():
        emit(event="error", message="dropped oversized IPC line")

    read_frames(sys.stdin.buffer, on_line, on_overflow, _MAX_LINE)
    GLib.idle_add(loop.quit)


def main():
    _die_with_parent()
    bridge = Bridge()
    Gio.bus_own_name(
        Gio.BusType.SESSION, VK_NAME, Gio.BusNameOwnerFlags.REPLACE,
        bridge.on_bus_acquired, bridge.on_name_acquired, bridge.on_name_lost,
    )
    Gio.bus_watch_name(
        Gio.BusType.SESSION, BACKEND_NAME, Gio.BusNameWatcherFlags.NONE,
        bridge.on_fcitx_appeared, None,
    )
    loop = GLib.MainLoop()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, lambda *_: (loop.quit(), False)[1])
    threading.Thread(target=stdin_reader, args=(bridge, loop), daemon=True).start()
    try:
        loop.run()
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    main()
