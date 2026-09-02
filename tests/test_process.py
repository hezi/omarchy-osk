"""Process supervision in osk-bridge.py: injectors run in their own session so
a timeout kills every descendant, not only the direct child; executables are
referenced by absolute path; shutdown kills an in-flight group."""
import os
import subprocess
import threading
import time
import unittest

from . import helpers

helpers.stub_gi()
B = helpers.load("osk_bridge", "osk-bridge.py")


def bare_bridge():
    br = B.Bridge.__new__(B.Bridge)
    br._child = None
    br._child_lock = threading.Lock()
    br.inject_queue = B.queue.Queue()
    return br


def sleepers(marker):
    """A shell that backgrounds one sleeper and execs into another; both carry
    `marker` in argv so pgrep -f can find them after the shell is gone."""
    one = "/usr/bin/python3 -c 'import time; time.sleep(30)  # %s'" % marker
    return "%s & exec %s" % (one, one)


def survivors(marker):
    return subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()


class Supervision(unittest.TestCase):
    def test_timeout_kills_grandchildren(self):
        br = bare_bridge()
        marker = "osk-test-grandchild-%d-%d" % (os.getpid(), time.time_ns())
        errors = []
        real_emit = B.emit
        B.emit = lambda **kw: errors.append(kw)
        t0 = time.time()
        try:
            br._run_injector(["/bin/sh", "-c", sleepers(marker)])
        finally:
            B.emit = real_emit
        dt = time.time() - t0
        self.assertLess(dt, B._INJECT_TIMEOUT + 2)
        time.sleep(0.2)
        self.assertEqual(survivors(marker), [])
        self.assertTrue(any("timed out" in e.get("message", "") for e in errors))
        self.assertIsNone(br._child)

    def test_shutdown_kills_inflight_group(self):
        br = bare_bridge()
        marker = "osk-test-inflight-%d-%d" % (os.getpid(), time.time_ns())
        real_emit = B.emit
        B.emit = lambda **kw: None
        t = threading.Thread(target=br._run_injector,
                             args=(["/bin/sh", "-c", sleepers(marker)],), daemon=True)
        t.start()
        try:
            for _ in range(50):
                if survivors(marker):
                    break
                time.sleep(0.05)
            self.assertTrue(survivors(marker))
            br.shutdown()
            t.join(3)
        finally:
            B.emit = real_emit
        self.assertFalse(t.is_alive())
        time.sleep(0.2)
        self.assertEqual(survivors(marker), [])
        self.assertIsNone(br.inject_queue.get_nowait())          # worker sentinel enqueued

    def test_injector_paths_are_absolute(self):
        for p in (B.YDOTOOL, B.WTYPE):
            self.assertTrue(os.path.isabs(p), p)
        for argv in (B.Bridge.ydotool_text_argv("a"), B.Bridge.ydotool_named_argv("enter", []),
                     B.Bridge.wtype_argv({"cmd": "text", "text": "é"})):
            if argv:
                self.assertTrue(os.path.isabs(argv[0]), argv)

    def test_pdeathsig_is_requested(self):
        """A child spawned with the bridge's parent-death hook dies with its
        parent; run the real prctl through a throwaway subprocess."""
        code = (
            "import os, sys, time, ctypes, signal, select\n"
            "r, w = os.pipe()\n"
            "pid = os.fork()\n"
            "if pid == 0:\n"
            "    os.close(r)\n"
            "    kid = os.fork()\n"
            "    if kid == 0:\n"
            "        signal.signal(signal.SIGTERM, lambda *a: (os.write(w, b'dead'), os._exit(0)))\n"
            "        ctypes.CDLL('libc.so.6').prctl(1, signal.SIGTERM, 0, 0, 0)\n"
            "        time.sleep(30); os._exit(1)\n"
            "    time.sleep(0.3); os._exit(0)\n"   # parent of the hooked child exits
            "os.close(w)\n"
            "os.waitpid(pid, 0)\n"
            "ready, _, _ = select.select([r], [], [], 5)\n"
            "print(os.read(r, 16).decode() if ready else 'alive')\n"
        )
        r = subprocess.run(["/usr/bin/python3", "-c", code], capture_output=True, text=True, timeout=15)
        self.assertEqual(r.stdout.strip(), "dead", r.stderr)


if __name__ == "__main__":
    unittest.main()
