"""Shared plumbing for the offline test suite.

The Python helpers (`osk-bridge.py`, `osk-store.py`, `keyd-exclude.py`) have
dashes in their names and one of them needs `gi` at import time, so they are
loaded from their paths with a stub `gi` when the real one is absent or when a
test must not touch the session bus.
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def stub_gi():
    """Install a minimal `gi` so osk-bridge.py imports without D-Bus. idle_add
    runs the callback synchronously, which is what the framing tests want."""
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a: None
    rep = types.ModuleType("gi.repository")

    class GLib:
        PRIORITY_HIGH = 0

        @staticmethod
        def idle_add(fn, *a):
            fn(*a)
            return 1

        @staticmethod
        def timeout_add_seconds(*a):
            return 1

        @staticmethod
        def unix_signal_add(*a):
            return 1

        class MainLoop:
            pass

    rep.GLib = GLib
    rep.Gio = types.SimpleNamespace()
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = rep


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
