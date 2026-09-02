#!/usr/bin/env python3
"""Settings + tablet-state store for the Omarchy OSK plugin.

QML's FileView reads whole files by pathname, which is neither bounded nor
symlink-safe. This tiny stdlib-only helper owns the plugin's two files instead
and talks to Osk.qml over a line protocol:

  stdout (store -> QML), one JSON object per line:
    {"event": "settings", "settings": {...}}   validated, schema-closed settings
    {"event": "tablet", "mode": "tablet"|"laptop"|"unknown"}
    {"event": "saved"} / {"event": "error", "message": "..."}

  stdin (QML -> store), one JSON object per line (bounded framing):
    {"cmd": "save", "settings": {...}}        validate + atomic private replace

Files:
  ~/.config/omarchy/osk.json                    settings (0600, atomic rename)
  $XDG_RUNTIME_DIR/omarchy/tablet-mode.state    "tablet" | "laptop", written by a
                                                tablet-mode service (piccolo-omarchy)

Both are read descriptor-relatively (osk_files): regular file, owned by us,
no symlink following, hard size caps. Changes are picked up with inotify - no
polling - and re-emitted. One process, so writes are serialized by construction.
"""
import ctypes
import json
import os
import re
import select
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from osk_files import open_private_dir, read_regular, publish, read_frames  # noqa: E402

HOME = os.path.expanduser("~")
SETTINGS_DIR = os.path.join(HOME, ".config", "omarchy")
SETTINGS_NAME = "osk.json"
RUNTIME_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or ("/run/user/%d" % os.getuid()), "omarchy")
TABLET_NAME = "tablet-mode.state"

_MAX_SETTINGS = 64 * 1024      # osk.json on disk
_MAX_STATE = 64                # tablet-mode.state
_MAX_LINE = 64 * 1024          # one IPC line
_MAX_LAYOUTS = 64              # entries in `enabled`
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_BOOLS = ("swipe", "keyPreview", "split", "suggest", "autocorrect", "glide")


def emit(**payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


# ---- schema ------------------------------------------------------------------
def validate(raw):
    """Closed schema: only known keys, checked types, bounded cardinality."""
    out = {}
    if not isinstance(raw, dict):
        return out
    if raw.get("autoShow") in ("tablet", "always", "never"):
        out["autoShow"] = raw["autoShow"]
    for k in _BOOLS:
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    lay = raw.get("layout")
    if isinstance(lay, str) and _ID.fullmatch(lay):
        out["layout"] = lay
    en = raw.get("enabled")
    if isinstance(en, list):
        ids = list(dict.fromkeys(x for x in en[:_MAX_LAYOUTS] if isinstance(x, str) and _ID.fullmatch(x)))
        if ids:
            out["enabled"] = ids
    return out


# ---- files -------------------------------------------------------------------
def read_settings():
    dfd = open_private_dir(SETTINGS_DIR, private=0)   # shared omarchy dir: owner-checked, not chmod'ed
    if dfd is None:
        return {}
    try:
        data = read_regular(dfd, SETTINGS_NAME, _MAX_SETTINGS)
    finally:
        os.close(dfd)
    if data is None:
        return {}
    try:
        return validate(json.loads(data.decode("utf-8", "replace")))
    except ValueError:
        return {}


def write_settings(settings):
    dfd = open_private_dir(SETTINGS_DIR, private=0)
    if dfd is None:
        raise OSError("settings directory unusable")
    try:
        body = json.dumps(validate(settings), indent=2, sort_keys=True) + "\n"
        publish(dfd, SETTINGS_NAME, body.encode("utf-8"))
    finally:
        os.close(dfd)


def read_tablet():
    dfd = open_private_dir(RUNTIME_DIR, private=1)
    if dfd is None:
        return "unknown"
    try:
        data = read_regular(dfd, TABLET_NAME, _MAX_STATE)
    finally:
        os.close(dfd)
    if data is None:
        return "unknown"
    mode = data.decode("ascii", "replace").strip()
    return mode if mode in ("tablet", "laptop") else "unknown"


# ---- inotify (ctypes, no dependency) ----------------------------------------------
IN_CLOSE_WRITE, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x8, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF, IN_ATTRIB = 0x400, 0x800, 0x4
_MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF | IN_ATTRIB
_EVENT = struct.Struct("iIII")


class Watcher:
    def __init__(self):
        self.libc = ctypes.CDLL("libc.so.6", use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.wds = {}

    def watch(self, path, tag):
        wd = self.libc.inotify_add_watch(self.fd, path.encode(), _MASK)
        if wd >= 0:
            self.wds[wd] = tag
        return wd >= 0

    def drain(self):
        """Return the set of (tag, name) touched since the last call."""
        touched = set()
        try:
            buf = os.read(self.fd, 64 * 1024)
        except BlockingIOError:
            return touched
        off = 0
        while off + _EVENT.size <= len(buf):
            wd, mask, cookie, ln = _EVENT.unpack_from(buf, off)
            name = buf[off + _EVENT.size: off + _EVENT.size + ln].split(b"\0", 1)[0].decode("utf-8", "replace")
            off += _EVENT.size + ln
            touched.add((self.wds.get(wd), name))
        return touched


# ---- main loop --------------------------------------------------------------------
def main():
    state = {"settings": None, "tablet": None}

    def refresh(which):
        if which == "settings":
            s = read_settings()
            if s != state["settings"]:
                state["settings"] = s
                emit(event="settings", settings=s)
        else:
            m = read_tablet()
            if m != state["tablet"]:
                state["tablet"] = m
                emit(event="tablet", mode=m)

    # Make sure the runtime dir exists (0700) before watching it.
    d = open_private_dir(RUNTIME_DIR, private=1)
    if d is not None:
        os.close(d)
    w = Watcher()
    w.watch(SETTINGS_DIR, "settings")
    w.watch(RUNTIME_DIR, "tablet")
    refresh("settings")
    refresh("tablet")
    emit(event="ready")

    def on_line(line):
        try:
            cmd = json.loads(line)
        except ValueError:
            emit(event="error", message="bad json")
            return
        if isinstance(cmd, dict) and cmd.get("cmd") == "save":
            try:
                write_settings(cmd.get("settings"))
                emit(event="saved")
            except OSError as exc:
                emit(event="error", message="save failed: %s" % exc)
            refresh("settings")

    # stdin is read with bounded framing on a thread (readline blocks per line);
    # each parsed line is queued and a byte on a self-pipe wakes select(), which
    # otherwise sleeps with no timeout - the process is fully idle between events.
    import threading, queue
    lines = queue.Queue(maxsize=64)
    wake_r, wake_w = os.pipe()

    def reader():
        def put(ln):
            lines.put(ln)
            os.write(wake_w, b"x")
        read_frames(sys.stdin.buffer, put,
                    lambda: emit(event="error", message="dropped oversized IPC line"), _MAX_LINE)
        put(None)
    threading.Thread(target=reader, daemon=True).start()

    while True:
        r, _, _ = select.select([w.fd, wake_r], [], [])
        if w.fd in r:
            for tag, name in w.drain():
                if tag == "settings" and (name == SETTINGS_NAME or name == ""):
                    refresh("settings")
                elif tag == "tablet" and (name == TABLET_NAME or name == ""):
                    refresh("tablet")
        if wake_r in r:
            os.read(wake_r, 4096)
            try:
                while True:
                    ln = lines.get_nowait()
                    if ln is None:
                        return              # stdin EOF: the shell went away
                    on_line(ln)
            except queue.Empty:
                pass


if __name__ == "__main__":
    main()
