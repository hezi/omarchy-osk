"""osk_engine: the dictionary cache is read descriptor-relatively (symlink /
FIFO / oversized swaps are refused, never followed), the network is never
touched when a cache exists, and concurrent set_lang calls always end with
the most recently requested language installed."""
import os
import shutil
import tempfile
import threading
import time
import unittest

from . import helpers  # noqa: F401
import osk_engine as E

CACHE = "the\t100\nthem\t50\nthen\t40\n"


class EngineCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        self.dict_dir = E.DICT_DIR
        E.DICT_DIR = os.path.join(self.tmp, ".config", "omarchy", "osk", "dict")
        os.makedirs(E.DICT_DIR, 0o700)
        self.dl = E._download_verified
        self.net_calls = []

        def no_net(*a):
            self.net_calls.append(a)
            raise RuntimeError("network disabled in tests")
        E._download_verified = no_net

    def tearDown(self):
        E.DICT_DIR = self.dict_dir
        E._download_verified = self.dl
        os.environ["HOME"] = self.home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, text):
        with open(os.path.join(E.DICT_DIR, name), "w") as f:
            f.write(text)

    def test_cache_hit_reads_text_no_network(self):
        self.put("english.txt", CACHE)
        self.assertEqual(E.fetch_dict("english"), CACHE)
        self.assertEqual(self.net_calls, [])

    def test_symlink_swap_refused_and_falls_to_network(self):
        os.symlink("/etc/passwd", os.path.join(E.DICT_DIR, "hebrew.txt"))
        self.assertIsNone(E.fetch_dict("hebrew"))
        self.assertEqual(len(self.net_calls), 1)        # tried a fresh download instead

    def test_fifo_swap_refused_without_hang(self):
        os.mkfifo(os.path.join(E.DICT_DIR, "greek.txt"))
        done = []
        t = threading.Thread(target=lambda: done.append(E.fetch_dict("greek")), daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "fetch_dict blocked on a FIFO")
        self.assertEqual(done, [None])

    def test_oversized_cache_refused(self):
        with open(os.path.join(E.DICT_DIR, "german.txt"), "wb") as f:
            f.truncate(E._MAX_CACHE_BYTES + 1)
        self.assertIsNone(E.fetch_dict("german"))

    def test_unknown_or_hostile_lang_refused(self):
        for lang in ("../../etc/passwd", "en/../x", "", "E" * 300):
            self.assertIsNone(E.fetch_dict(lang))
        self.assertEqual(self.net_calls, [])

    def test_engine_loads_cache_and_corrects(self):
        self.put("english.txt", CACHE)
        e = E.Engine()
        e.set_lang("english")
        e.loading.join(5)
        self.assertEqual(e.lang, "english")
        self.assertEqual(e.correct("teh"), "the")

    def test_concurrent_set_lang_latest_wins(self):
        self.put("english.txt", CACHE)
        self.put("french.txt", "bonjour\t5\n")
        orig = E.fetch_dict

        def slow(lang, log=None):
            if lang == "french":
                time.sleep(0.4)
            return orig(lang, log or (lambda *a: None))
        E.fetch_dict = slow
        try:
            e = E.Engine()
            e.set_lang("french")
            time.sleep(0.05)
            e.set_lang("english")
            time.sleep(1.2)
            self.assertEqual(e.lang, "english")
            self.assertIn("the", e.words)
            self.assertNotIn("bonjour", e.words)

            e2 = E.Engine()
            e2.set_lang("english")
            time.sleep(0.05)
            e2.set_lang("french")
            time.sleep(1.2)
            self.assertEqual(e2.lang, "french")
            self.assertEqual(list(e2.words), ["bonjour"])
        finally:
            E.fetch_dict = orig

    def test_line_cap_bounds_parse(self):
        self.put("english.txt", "".join("w%d\t1\n" % i for i in range(E._MAX_CACHE_LINES + 500)))
        e = E.Engine()
        e.set_lang("english")
        e.loading.join(10)
        self.assertLessEqual(len(e.words), E._MAX_CACHE_LINES)


if __name__ == "__main__":
    unittest.main()
