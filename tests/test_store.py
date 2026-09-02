"""osk-store.py end to end: closed settings schema, atomic 0600 saves, inotify
pickup of external edits and tablet-state changes, hostile swaps of the state
file (symlink / FIFO) answered with "unknown" rather than a hang, bounded IPC,
clean exit on stdin EOF."""
import json
import os
import queue
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest

from . import helpers

S = helpers.load("osk_store", "osk-store.py")


class Schema(unittest.TestCase):
    def test_closed_schema(self):
        raw = {"autoShow": "always", "swipe": "yes", "layout": "../evil", "split": True,
               "enabled": ["en-qwerty", "x/y", 5] + ["he-standard"] * 100, "bogus": 1}
        self.assertEqual(S.validate(raw), {"autoShow": "always", "split": True,
                                           "enabled": ["en-qwerty", "he-standard"]})

    def test_non_dict_and_bad_values(self):
        self.assertEqual(S.validate([1, 2]), {})
        self.assertEqual(S.validate({"autoShow": "sometimes", "layout": "A", "enabled": "en"}), {})

    def test_layout_cap(self):
        ids = ["l%d" % i for i in range(200)]
        self.assertEqual(len(S.validate({"enabled": ids})["enabled"]), S._MAX_LAYOUTS)


class StoreProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.run = os.path.join(self.tmp, "run")
        os.makedirs(os.path.join(self.home, ".config", "omarchy"))
        os.makedirs(self.run, mode=0o700)
        self.settings = os.path.join(self.home, ".config", "omarchy", "osk.json")
        self.state_dir = os.path.join(self.run, "omarchy")
        self.state = os.path.join(self.state_dir, "tablet-mode.state")
        self.p = None

    def tearDown(self):
        if self.p:
            if self.p.poll() is None:
                self.p.kill()
                self.p.wait()
            self.p.stdin.close()
            self.p.stdout.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self):
        env = dict(os.environ, HOME=self.home, XDG_RUNTIME_DIR=self.run)
        self.p = subprocess.Popen(["/usr/bin/python3", os.path.join(helpers.ROOT, "osk-store.py")], env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        # readline() buffers past what select() reports, so pump lines on a thread
        self.q = queue.Queue()

        def pump():
            for line in self.p.stdout:
                self.q.put(json.loads(line))
        threading.Thread(target=pump, daemon=True).start()
        return self.events(until=("ready",))

    def events(self, until=("ready", "saved", "error"), timeout=2.0, n=8):
        """Collect events until one of `until` arrives (or, with until=None,
        until the stream has been quiet for `timeout`)."""
        out, end = [], time.time() + timeout
        while len(out) < n:
            try:
                out.append(self.q.get(timeout=max(0.0, end - time.time()) if until else timeout))
            except queue.Empty:
                break
            if until and out[-1].get("event") in until:
                break
        return out

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def write_state(self, mode):
        tmp = os.path.join(self.state_dir, ".t")
        with open(tmp, "w") as f:
            f.write(mode + "\n")
        os.rename(tmp, self.state)

    def test_startup_validates_and_reports(self):
        with open(self.settings, "w") as f:
            json.dump({"autoShow": "always", "swipe": "yes", "bogus": 1}, f)
        ev = self.start()
        kinds = [e["event"] for e in ev]
        self.assertEqual(kinds, ["settings", "tablet", "ready"])
        self.assertEqual(ev[0]["settings"], {"autoShow": "always"})
        self.assertEqual(ev[1]["mode"], "unknown")
        self.assertEqual(stat.S_IMODE(os.stat(self.state_dir).st_mode), 0o700)   # created for the producer

    def test_tablet_state_changes_and_hostile_swaps(self):
        self.start()
        self.write_state("tablet")
        self.assertEqual(self.events(until=("tablet",)), [{"event": "tablet", "mode": "tablet"}])
        os.remove(self.state)
        os.symlink("/etc/passwd", self.state)
        self.assertEqual(self.events(until=("tablet",)), [{"event": "tablet", "mode": "unknown"}])
        os.remove(self.state)
        os.mkfifo(self.state)
        with open(self.state + ".x", "w") as f:       # unrelated file: no event
            f.write("laptop\n")
        self.assertEqual(self.events(until=None, timeout=0.6), [])
        os.remove(self.state)
        self.write_state("laptop")
        self.assertEqual(self.events(until=("tablet",)), [{"event": "tablet", "mode": "laptop"}])
        self.write_state("evil")
        self.assertEqual(self.events(until=("tablet",)), [{"event": "tablet", "mode": "unknown"}])
        self.assertIsNone(self.p.poll())

    def test_save_is_validated_atomic_0600(self):
        self.start()
        self.send({"cmd": "save", "settings": {"autoShow": "tablet", "glide": False, "layout": "de-qwertz",
                                               "enabled": ["de-qwertz", "bad id"], "nope": True}})
        ev = self.events()
        self.assertEqual(ev[0], {"event": "saved"})
        with open(self.settings) as f:
            saved = json.load(f)
        self.assertEqual(saved, {"autoShow": "tablet", "glide": False, "layout": "de-qwertz", "enabled": ["de-qwertz"]})
        self.assertEqual(stat.S_IMODE(os.stat(self.settings).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.settings)) if ".tmp" in n], [])
        # the store re-reads its own save and reports it once
        ev2 = self.events(until=("settings",))
        self.assertEqual(ev2[-1]["event"], "settings")

    def test_external_edit_is_picked_up(self):
        self.start()
        with open(self.settings, "w") as f:
            json.dump({"autoShow": "never"}, f)
        ev = self.events(until=("settings",))
        self.assertEqual(ev[-1], {"event": "settings", "settings": {"autoShow": "never"}})

    def test_bad_and_oversized_ipc_are_survived(self):
        self.start()
        self.p.stdin.write("not json\n")
        self.p.stdin.write("z" * (S._MAX_LINE + 5000) + "\n")
        self.p.stdin.flush()
        ev = self.events(until=None, timeout=1.0)
        msgs = sorted(e["message"] for e in ev if e["event"] == "error")
        self.assertEqual(msgs, ["bad json", "dropped oversized IPC line"])
        self.send({"cmd": "save", "settings": {"swipe": True}})
        self.assertEqual(self.events()[0], {"event": "saved"})

    def test_exits_on_stdin_eof(self):
        self.start()
        self.p.stdin.close()
        self.assertEqual(self.p.wait(timeout=3), 0)

    def test_symlinked_settings_file_is_not_followed(self):
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, self.settings)
        ev = self.start()
        self.assertEqual(ev[0]["settings"], {})            # symlink is not read
        self.send({"cmd": "save", "settings": {"swipe": True}})
        self.assertEqual(self.events()[0], {"event": "saved"})
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")     # ...nor written through
        self.assertFalse(os.path.islink(self.settings))


if __name__ == "__main__":
    unittest.main()
