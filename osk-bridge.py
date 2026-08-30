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
  * text and named keys are injected with wtype (Wayland virtual keyboard,
    handles Unicode and arbitrary keysyms);
  * chords go through ydotool (kernel uinput, so the compositor sees a real
    keyboard - Super+Enter and friends hit Hyprland binds, Ctrl+C reaches
    the app). ydotoold runs as a user service; keyd excludes its device.
Calls are serialised on one worker thread so key order is kept.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

import gi

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


def emit(**payload):
    line = json.dumps(payload)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    # Mirror to a small log so `tail -f ~/.local/state/omarchy/osk-bridge.log`
    # shows what fcitx5 is telling us; the QML side swallows stdout otherwise.
    try:
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + line + "\n")
    except OSError:
        pass


class Bridge:
    def __init__(self):
        self.conn = None
        self.preedit = ""
        self.arming = False
        self.inject_queue = queue.Queue()
        threading.Thread(target=self.inject_worker, daemon=True).start()

    # --- arming ---------------------------------------------------------------
    # Owning the bus name only makes fcitx5 *notice* us. It switches into
    # on-screen-keyboard mode - exporting VirtualKeyboardBackend1 and sending
    # focus-driven Show/Hide - the first time its own ShowVirtualKeyboard
    # service method is called. So call Show and then Hide once, and swallow
    # the pair of callbacks that produces.
    def arm(self):
        if self.conn is None:
            return False
        self.arming = True
        for method in ("ShowVirtualKeyboard", "HideVirtualKeyboard"):
            self.conn.call(
                BACKEND_NAME, BACKEND_PATH, SERVICE_IFACE, method, None,
                None, Gio.DBusCallFlags.NONE, 1000, None, None,
            )
        GLib.timeout_add(600, self._armed)
        return False

    def _armed(self):
        self.arming = False
        emit(event="armed")
        self.sync_focus()
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
            focused = "focus:1" in info
            emit(event="show" if focused else "hide", source="sync")
        except Exception as exc:
            emit(event="error", message="sync_focus: %s" % exc)

    # --- fcitx5 -> us ---------------------------------------------------------
    def on_method_call(self, conn, sender, path, iface, method, params, invocation):
        args = params.unpack() if params is not None else ()
        if method == "ShowVirtualKeyboard":
            if not self.arming:
                emit(event="show")
        elif method == "HideVirtualKeyboard":
            if not self.arming:
                emit(event="hide")
        elif method == "UpdatePreeditArea":
            self.preedit = args[0]
            emit(event="preedit", text=self.preedit)
        elif method == "UpdatePreeditCaret":
            emit(event="preedit", text=self.preedit, caret=args[0])
        elif method == "NotifyIMActivated":
            emit(event="notify", method=method, args=list(args))
            if not self.arming:
                emit(event="show")
        elif method == "NotifyIMDeactivated":
            emit(event="notify", method=method, args=list(args))
            if not self.arming:
                emit(event="hide")
        elif method.startswith("Notify"):
            emit(event="notify", method=method, args=list(args))
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
        argv = ["wtype"]
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

    @staticmethod
    def ydotool_argv(cmd):
        mods = [Bridge.MOD_EVDEV[m] for m in cmd.get("mods", []) if m in Bridge.MOD_EVDEV]
        code = int(cmd["code"]) - 8
        if code <= 0:
            return None
        seq = ["%d:1" % m for m in mods] + ["%d:1" % code, "%d:0" % code] + ["%d:0" % m for m in reversed(mods)]
        return ["ydotool", "key", "--key-delay", "8"] + seq

    def inject_worker(self):
        while True:
            argv = self.inject_queue.get()
            try:
                subprocess.run(argv, check=False, timeout=2,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                emit(event="error", message=str(exc))

    def handle_command(self, line):
        if not line:
            return False
        try:
            cmd = json.loads(line)
            kind = cmd.get("cmd")
            if kind in ("type", "key", "char"):
                argv = self.wtype_argv(cmd)
                if argv:
                    self.inject_queue.put(argv)
            elif kind == "chord":
                argv = self.ydotool_argv(cmd)
                if argv:
                    self.inject_queue.put(argv)
            elif kind == "visible":
                self.backend_call("ProcessVisibilityEvent", GLib.Variant("(b)", (bool(cmd.get("value")),)))
        except Exception as exc:  # keep the bridge alive on a bad line
            emit(event="error", message=str(exc), line=line)
        return False  # one-shot idle callback


def stdin_reader(bridge, loop):
    for line in sys.stdin:
        GLib.idle_add(bridge.handle_command, line.strip())
    GLib.idle_add(loop.quit)


def main():
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
    threading.Thread(target=stdin_reader, args=(bridge, loop), daemon=True).start()
    loop.run()


if __name__ == "__main__":
    main()
