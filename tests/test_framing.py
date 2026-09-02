"""stdin framing: an unterminated or oversized producer line must never grow
memory past the cap, and the next valid record must still be handled."""
import io
import resource
import threading
import types
import unittest
import unittest.mock

from . import helpers

helpers.stub_gi()
B = helpers.load("osk_bridge", "osk-bridge.py")
from osk_files import read_frames  # noqa: E402


class Framing(unittest.TestCase):
    def test_unterminated_32mib_line_is_dropped_bounded(self):
        got, over = [], []
        big = b"x" * (32 * 1024 * 1024) + b"\n" + b'{"cmd":"noop"}\n'
        rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        read_frames(io.BytesIO(big), got.append, lambda: over.append(1), B._MAX_LINE)
        rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        self.assertEqual(got, ['{"cmd":"noop"}'])
        self.assertEqual(len(over), 1)
        self.assertLess(rss1 - rss0, 4096, "maxrss grew by %d KiB" % (rss1 - rss0))

    def test_final_line_without_newline_is_handled(self):
        got = []
        read_frames(io.BytesIO(b'{"cmd":"a"}'), got.append)
        self.assertEqual(got, ['{"cmd":"a"}'])

    def test_cap_plus_one_unterminated_then_eof_is_dropped(self):
        got, over = [], []
        read_frames(io.BytesIO(b"y" * (B._MAX_LINE + 1)), got.append, lambda: over.append(1), B._MAX_LINE)
        self.assertEqual(got, [])
        self.assertEqual(len(over), 1)

    def test_exactly_cap_is_accepted(self):
        got = []
        read_frames(io.BytesIO(b"z" * (B._MAX_LINE - 1) + b"\n"), got.append, max_line=B._MAX_LINE)
        self.assertEqual(len(got), 1)

    def test_many_oversized_records_then_valid(self):
        got, over = [], []
        data = (b"q" * (B._MAX_LINE * 3) + b"\n") * 5 + b"ok\n"
        read_frames(io.BytesIO(data), got.append, lambda: over.append(1), B._MAX_LINE)
        self.assertEqual(got, ["ok"])
        self.assertEqual(len(over), 5)

    def test_pending_cap_drops_when_loop_not_draining(self):
        """stdin_reader holds at most _MAX_PENDING undispatched commands; when
        the main loop never drains, further lines are dropped with an error
        instead of queuing without bound."""
        queued, errors = [], []
        n = B._MAX_PENDING + 3
        fake_stdin = types.SimpleNamespace(buffer=io.BytesIO(b'{"cmd":"noop"}\n' * n))
        real_idle, real_emit, real_stdin = B.GLib.idle_add, B.emit, B.sys.stdin
        B.GLib.idle_add = lambda fn, *a: queued.append((fn, a)) or 1     # never runs them
        B.emit = lambda **kw: errors.append(kw)
        B.sys.stdin = fake_stdin
        try:
            with unittest.mock.patch.object(B.threading.BoundedSemaphore, "acquire",
                                            lambda self, timeout=None: threading.Semaphore.acquire(self, blocking=False)):
                B.stdin_reader(bridge=None, loop=types.SimpleNamespace(quit=lambda: None))
        finally:
            B.GLib.idle_add, B.emit, B.sys.stdin = real_idle, real_emit, real_stdin
        handled = [q for q in queued if q[1]]           # (handle, (line,)) - not loop.quit
        self.assertEqual(len(handled), B._MAX_PENDING)
        dropped = [e for e in errors if "not draining" in e.get("message", "")]
        self.assertEqual(len(dropped), 3)


if __name__ == "__main__":
    unittest.main()
