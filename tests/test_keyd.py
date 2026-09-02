"""keyd-exclude.py: the privileged /etc/keyd edit is a single rename with an
exclusive backup, preserved owner/mode and rollback; --remove undoes only the
block this tool inserted and refuses anything it does not recognise.

Runs unprivileged: the config lives in a temp dir owned by the test user (the
tool checks "owned by the effective uid", which is root in production) and
`keyd reload` is stubbed."""
import hashlib
import json
import os
import shutil
import stat
import tempfile
import unittest

from . import helpers

K = helpers.load("keyd_exclude", "keyd-exclude.py")

BASE = "[ids]\n*\n\n[main]\ncapslock = overload(control, esc)\n"


class Keyd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conf = os.path.join(self.tmp, "default.conf")
        self.state = os.path.join(self.tmp, "state", "piccolo-osk-keyd.json")
        self.write_conf(BASE)
        self.reloads = []
        self.reload_ok = True
        K.keyd_reload = lambda: (self.reloads.append(1) or (self.reload_ok, "stub"))
        self.out = []
        K.print = lambda s: self.out.append(json.loads(s))   # module global shadows the builtin
        for v in ("SUDO_UID", "SUDO_GID"):
            os.environ.pop(v, None)

    def tearDown(self):
        del K.print
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_conf(self, text, mode=0o644):
        with open(self.conf, "w") as f:
            f.write(text)
        os.chmod(self.conf, mode)

    def read_conf(self):
        with open(self.conf) as f:
            return f.read()

    def backups(self):
        return [n for n in os.listdir(self.tmp) if ".piccolo-osk.bak." in n]

    def tmps(self):
        return [n for n in os.listdir(self.tmp) if n.endswith(".tmp")]

    def test_add_then_remove_round_trip(self):
        K.do_add(self.conf, self.state)
        self.assertEqual(self.out[-1]["ok"], True)
        self.assertTrue(self.out[-1]["changed"])
        text = self.read_conf()
        self.assertEqual(text, "[ids]\n*\n" + K.BLOCK + "\n[main]\ncapslock = overload(control, esc)\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.conf).st_mode), 0o644)   # mode preserved
        self.assertEqual(len(self.backups()), 1)
        with open(os.path.join(self.tmp, self.backups()[0])) as f:
            self.assertEqual(f.read(), BASE)
        self.assertEqual(self.tmps(), [])
        with open(self.state) as f:
            rec = json.load(f)
        self.assertEqual(rec["before_sha256"], hashlib.sha256(BASE.encode()).hexdigest())
        self.assertEqual(rec["after_sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.state)).st_mode), 0o700)

        K.do_remove(self.conf, self.state)
        self.assertTrue(self.out[-1]["changed"])
        self.assertEqual(self.read_conf(), BASE)
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(len(self.reloads), 2)

    def test_add_is_idempotent(self):
        K.do_add(self.conf, self.state)
        K.do_add(self.conf, self.state)
        self.assertEqual(self.out[-1], {"ok": True, "changed": False, "reason": "already managed"})
        self.assertEqual(len(self.backups()), 1)

    def test_preexisting_user_rule_left_alone(self):
        user = "[ids]\n*\n-2333:6666\n\n[main]\n"
        self.write_conf(user)
        K.do_add(self.conf, self.state)
        self.assertFalse(self.out[-1]["changed"])
        self.assertEqual(self.read_conf(), user)
        K.do_remove(self.conf, self.state)
        self.assertFalse(self.out[-1]["changed"])
        self.assertEqual(self.read_conf(), user)          # --remove never touches the user's line

    def test_remove_refuses_hand_edited_block(self):
        K.do_add(self.conf, self.state)
        self.write_conf(self.read_conf().replace(K.DEVICE_ID, K.DEVICE_ID + "\n-1234:5678"))
        before = self.read_conf()
        with self.assertRaises(SystemExit):
            K.do_remove(self.conf, self.state)
        self.assertFalse(self.out[-1]["ok"])
        self.assertEqual(self.read_conf(), before)

    def test_legacy_unmarked_blocks_are_migrated_and_removable(self):
        for legacy in K.LEGACY:
            self.write_conf("[ids]\n*\n" + legacy + "\n[main]\n")
            K.do_add(self.conf, self.state)
            self.assertTrue(self.out[-1]["changed"])
            self.assertEqual(self.read_conf(), "[ids]\n*\n" + K.BLOCK + "\n[main]\n")
            K.do_remove(self.conf, self.state)
            self.assertEqual(self.read_conf(), "[ids]\n*\n\n[main]\n")

    def test_keyd_rejection_rolls_back(self):
        self.reload_ok = False
        with self.assertRaises(SystemExit):
            K.do_add(self.conf, self.state)
        self.assertEqual(self.read_conf(), BASE)
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(len(self.backups()), 1)          # kept for inspection
        self.assertEqual(self.tmps(), [])
        self.assertEqual(self.out[-1]["ok"], False)

    def test_interrupted_publish_leaves_original(self):
        real = os.rename

        def boom(*a, **k):
            raise OSError("simulated crash mid-rename")
        os.rename = boom
        try:
            with self.assertRaises(OSError):
                K.do_add(self.conf, self.state)
        finally:
            os.rename = real
        self.assertEqual(self.read_conf(), BASE)
        self.assertEqual(self.tmps(), [])
        self.assertEqual(self.reloads, [])
        self.assertFalse(os.path.exists(self.state))

    def test_preflight_refusals(self):
        os.remove(self.conf)
        os.symlink("/etc/hostname", self.conf)
        with self.assertRaises(SystemExit):
            K.do_add(self.conf, self.state)
        os.remove(self.conf)
        os.mkfifo(self.conf)
        with self.assertRaises(SystemExit):
            K.do_add(self.conf, self.state)
        os.remove(self.conf)
        self.write_conf(BASE, mode=0o666)
        with self.assertRaises(SystemExit):
            K.do_add(self.conf, self.state)
        self.assertEqual(self.read_conf(), BASE)
        self.assertEqual(self.backups(), [])

    def test_no_ids_section_prepends_block(self):
        self.write_conf("[main]\n")
        K.do_add(self.conf, self.state)
        self.assertEqual(self.read_conf(), K.BLOCK + "\n[main]\n")
        K.do_remove(self.conf, self.state)
        self.assertEqual(self.read_conf(), "\n[main]\n")


if __name__ == "__main__":
    unittest.main()
