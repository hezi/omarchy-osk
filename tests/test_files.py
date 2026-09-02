"""osk_files: descriptor-relative reads refuse symlinks, FIFOs, foreign owners
and oversized files; publish is atomic, exclusive and 0600; private
directories are created/repaired 0700."""
import os
import shutil
import stat
import tempfile
import unittest

from . import helpers  # noqa: F401  (sys.path)
import osk_files as F


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        os.makedirs(os.path.join(self.tmp, ".config", "omarchy"))   # shared parents must pre-exist
        self.d = os.path.join(self.tmp, ".config", "omarchy", "osk", "dict")

    def tearDown(self):
        os.environ["HOME"] = self.home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def open_dir(self):
        dfd = F.open_private_dir(self.d, private=2)
        self.assertIsNotNone(dfd)
        return dfd

    def test_private_dir_created_0700_and_repaired(self):
        dfd = self.open_dir()
        os.close(dfd)
        self.assertEqual(stat.S_IMODE(os.stat(self.d).st_mode), 0o700)
        os.chmod(self.d, 0o755)
        os.close(self.open_dir())
        self.assertEqual(stat.S_IMODE(os.stat(self.d).st_mode), 0o700)

    def test_private_component_symlink_refused(self):
        os.makedirs(os.path.dirname(self.d))
        os.symlink("/etc", self.d)
        self.assertIsNone(F.open_private_dir(self.d, private=2))

    def test_read_regular_symlink_refused(self):
        dfd = self.open_dir()
        os.symlink("/etc/passwd", os.path.join(self.d, "english.txt"))
        self.assertIsNone(F.read_regular(dfd, "english.txt", 1 << 20))
        os.close(dfd)

    def test_read_regular_fifo_refused_without_blocking(self):
        dfd = self.open_dir()
        os.mkfifo(os.path.join(self.d, "greek.txt"))
        self.assertIsNone(F.read_regular(dfd, "greek.txt", 1 << 20))   # would hang without O_NONBLOCK
        os.close(dfd)

    def test_read_regular_oversized_refused(self):
        dfd = self.open_dir()
        with open(os.path.join(self.d, "big.txt"), "wb") as f:
            f.truncate(101)
        self.assertIsNone(F.read_regular(dfd, "big.txt", 100))
        with open(os.path.join(self.d, "ok.txt"), "wb") as f:
            f.write(b"x" * 100)
        self.assertEqual(F.read_regular(dfd, "ok.txt", 100), b"x" * 100)
        os.close(dfd)

    def test_read_regular_rejects_path_components(self):
        dfd = self.open_dir()
        for bad in ("", ".", "..", "a/b", "../x"):
            self.assertIsNone(F.read_regular(dfd, bad, 10))
        os.close(dfd)

    def test_read_regular_repairs_mode(self):
        dfd = self.open_dir()
        p = os.path.join(self.d, "w.txt")
        with open(p, "wb") as f:
            f.write(b"hi")
        os.chmod(p, 0o644)
        self.assertEqual(F.read_regular(dfd, "w.txt", 10), b"hi")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        os.close(dfd)

    def test_publish_atomic_exclusive_0600(self):
        dfd = self.open_dir()
        F.publish(dfd, "french.txt", b"bonjour\t5\n")
        p = os.path.join(self.d, "french.txt")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"bonjour\t5\n")
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith(".tmp")], [])
        # replacing over a symlink target does not follow it
        os.remove(p)
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, p)
        F.publish(dfd, "french.txt", b"new")
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")
        self.assertFalse(os.path.islink(p))
        os.close(dfd)

    def test_publish_failure_leaves_original_and_no_tmp(self):
        dfd = self.open_dir()
        F.publish(dfd, "a.txt", b"old")
        real_rename = os.rename

        def boom(*a, **k):
            raise OSError("simulated ENOSPC")
        os.rename = boom
        try:
            with self.assertRaises(OSError):
                F.publish(dfd, "a.txt", b"new")
        finally:
            os.rename = real_rename
        with open(os.path.join(self.d, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"old")
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith(".tmp")], [])
        os.close(dfd)


if __name__ == "__main__":
    unittest.main()
