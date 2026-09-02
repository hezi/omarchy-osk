#!/usr/bin/env python3
"""Transactional keyd exclusion for the on-screen keyboard's ydotool device.

Run by setup.sh under sudo. The whole edit is one rename: the live config is
never truncated in place, so an interruption at any point leaves either the
original or the complete new file. Only the block this tool inserted (marked
below) is ever removed again - a pre-existing user rule for the same device
id is left alone.

  keyd-exclude.py add    <conf> <state-file>   insert the block; write state
  keyd-exclude.py remove <conf> <state-file>   remove exactly our block, verified
                                               against the recorded digest

Both print one JSON line describing what happened. Exit status is non-zero on
any refusal; the config is untouched in that case.
"""
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys

DEVICE_ID = "-2333:6666"
BEGIN = "# >>> piccolo.osk: ydotool exclusion (managed; remove with setup.sh --remove) >>>"
END = "# <<< piccolo.osk <<<"
BLOCK = (BEGIN + "\n"
         "# keys injected by the on-screen keyboard must not be re-routed through keyd\n"
         "# (whose virtual keyboard a tablet mode disables).\n"
         + DEVICE_ID + "\n" + END + "\n")
# What setup.sh <= 2.3.0 (and piccolo-omarchy's installer) inserted without
# markers. The comment text is specific enough to be ours, so it is migrated to
# (add) or treated as (remove) BLOCK.
LEGACY = (
    "# ydotoold virtual device: keys injected by the on-screen keyboard must not\n"
    "# be re-routed through keyd (whose virtual keyboard a tablet mode disables).\n"
    + DEVICE_ID + "\n",
    "# ydotoold virtual device: keys injected by the on-screen keyboard must not be\n"
    "# re-routed through keyd, whose virtual keyboard is disabled in tablet mode.\n"
    + DEVICE_ID + "\n",
)
_MAX_CONF = 1024 * 1024
_O_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOCTTY | os.O_CLOEXEC | os.O_NONBLOCK


def die(msg, **extra):
    print(json.dumps(dict(ok=False, error=msg, **extra)))
    sys.exit(1)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def preflight(conf):
    """Open the live config by descriptor; it must be a regular root-owned file
    with no world/group write bits, small enough to be a config, in a directory
    we can rename into."""
    d, name = os.path.split(os.path.abspath(conf))
    try:
        dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        die("config directory unusable: %s" % exc)
    try:
        fd = os.open(name, _O_FILE, dir_fd=dfd)
    except OSError as exc:
        os.close(dfd)
        die("cannot open %s: %s" % (conf, exc))
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        die("%s is not a regular file" % conf)
    # Owned by whoever runs this (root under sudo) and not writable by others.
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        die("%s must be owned by uid %d and not group/world-writable" % (conf, os.geteuid()))
    if st.st_size > _MAX_CONF:
        die("%s is implausibly large" % conf)
    data = b""
    while len(data) <= _MAX_CONF:
        b = os.read(fd, 65536)
        if not b:
            break
        data += b
    os.close(fd)
    return dfd, name, st, data


def publish(dfd, name, st, data, backup_of=None):
    """Write `data` next to the target as an exclusive temp file with the
    original's owner/mode, fsync, then rename over it and fsync the directory.
    If `backup_of` is given, first snapshot those bytes to an exclusive, durable
    backup (never a predictable name) and return its name."""
    backup = None
    if backup_of is not None:
        backup = "%s.piccolo-osk.bak.%s" % (name, secrets.token_hex(6))
        bfd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        try:
            os.write(bfd, backup_of)
            os.fsync(bfd)
            os.fchmod(bfd, stat.S_IMODE(st.st_mode))
        finally:
            os.close(bfd)
    tmp = ".%s.%s.tmp" % (name, secrets.token_hex(6))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.fchown(fd, st.st_uid, st.st_gid)
        os.fchmod(fd, stat.S_IMODE(st.st_mode))
        os.close(fd)
        fd = -1
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        os.fsync(dfd)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    return backup


def keyd_reload():
    """Validate by asking keyd to reload; a failure means roll back."""
    try:
        r = subprocess.run(["/usr/bin/keyd", "reload"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=10, start_new_session=True)
        return r.returncode == 0, (r.stderr or r.stdout).strip()[:400]
    except FileNotFoundError:
        return True, "keyd binary not found; config written, not reloaded"
    except subprocess.TimeoutExpired:
        return False, "keyd reload timed out"


def write_state(path, obj):
    # setup.sh passes a path inside the invoking user's state dir; write it as
    # that user (we are root here) so they own their own record.
    uid = int(os.environ.get("SUDO_UID", os.getuid()))
    gid = int(os.environ.get("SUDO_GID", os.getgid()))
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chown(d, uid, gid)
    tmp = path + "." + secrets.token_hex(4)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, (json.dumps(obj, indent=2) + "\n").encode())
        os.fsync(fd)
        os.fchown(fd, uid, gid)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def read_state(path):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read(65536))
    except (OSError, ValueError):
        return None


def do_add(conf, state_path):
    dfd, name, st, data = preflight(conf)
    text = data.decode("utf-8", "replace")
    if BEGIN in text:
        os.close(dfd)
        print(json.dumps(dict(ok=True, changed=False, reason="already managed")))
        return
    legacy = next((l for l in LEGACY if l in text), None)
    if legacy:
        new = text.replace(legacy, BLOCK, 1)
    elif DEVICE_ID in text.split():
        os.close(dfd)
        print(json.dumps(dict(ok=True, changed=False, reason="a user rule already excludes %s" % DEVICE_ID)))
        return
    else:
        anchor = "[ids]\n*\n"
        new = text.replace(anchor, anchor + BLOCK, 1) if anchor in text else BLOCK + "\n" + text
    new_b = new.encode("utf-8")
    backup = publish(dfd, name, st, new_b, backup_of=data)
    ok, msg = keyd_reload()
    if not ok:
        # keyd rejected the file: put the original back (same atomic path) and
        # report - the backup stays so the user can inspect it.
        publish(dfd, name, st, data)
        keyd_reload()
        os.close(dfd)
        die("keyd rejected the new config; original restored", keyd=msg, backup=os.path.join(os.path.dirname(conf), backup))
    os.close(dfd)
    write_state(state_path, dict(conf=conf, backup=os.path.join(os.path.dirname(conf), backup),
                                 before_sha256=sha(data), after_sha256=sha(new_b),
                                 block_sha256=sha(BLOCK.encode()), uid=st.st_uid, gid=st.st_gid,
                                 mode=oct(stat.S_IMODE(st.st_mode))))
    print(json.dumps(dict(ok=True, changed=True, backup=os.path.join(os.path.dirname(conf), backup), keyd=msg)))


def do_remove(conf, state_path):
    dfd, name, st, data = preflight(conf)
    text = data.decode("utf-8", "replace")
    if BEGIN in text:
        rec = read_state(state_path)
        if rec and rec.get("block_sha256") != sha(BLOCK.encode()):
            os.close(dfd)
            die("recorded block differs from this version's; refusing to guess")
        i = text.index(BEGIN)
        j = text.find(END, i)
        j = j + len(END) + 1 if j >= 0 else -1
        block = text[i:j] if j > 0 else ""
        if block != BLOCK:
            os.close(dfd)
            die("managed block was edited by hand; refusing to remove it", found=text[i:i + 400])
        new = text[:i] + text[j:]
    elif any(l in text for l in LEGACY):
        new = text.replace(next(l for l in LEGACY if l in text), "", 1)
    else:
        os.close(dfd)
        print(json.dumps(dict(ok=True, changed=False, reason="no managed block present")))
        return
    new_b = new.encode("utf-8")
    publish(dfd, name, st, new_b, backup_of=data)
    ok, msg = keyd_reload()
    if not ok:
        publish(dfd, name, st, data)
        keyd_reload()
        os.close(dfd)
        die("keyd rejected the restored config; change reverted", keyd=msg)
    os.close(dfd)
    try:
        os.unlink(state_path)
    except OSError:
        pass
    print(json.dumps(dict(ok=True, changed=True, keyd=msg)))


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("add", "remove"):
        die("usage: keyd-exclude.py add|remove <conf> <state-file>")
    if os.geteuid() != 0:
        die("must run as root")
    (do_add if sys.argv[1] == "add" else do_remove)(sys.argv[2], sys.argv[3])


if __name__ == "__main__":
    main()
