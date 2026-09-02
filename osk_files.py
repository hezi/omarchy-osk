"""Descriptor-relative, race-resistant file helpers shared by the OSK's Python
side (osk_engine.py, osk-store.py).

Every read or publish here goes through a directory *descriptor* that was
opened component by component, so a pathname cannot be swapped for a symlink,
FIFO or foreign file between "check" and "use":

  open_private_dir(path)      -> fd of a directory we own (created 0700 if
                                 missing); the trailing `private` components are
                                 opened O_NOFOLLOW and repaired to 0700
  read_regular(dfd, name, cap)-> bytes of a regular, owner-owned file no larger
                                 than `cap`, read from the opened fd (no path
                                 re-lookup); None if anything is off
  publish(dfd, name, data)    -> exclusive 0600 temp file, fsync, rename onto
                                 `name` inside the same directory fd, fsync dir
  read_frames(stream, ...)    -> newline framing that never buffers more than
                                 the line cap (oversized records are drained)

No dependencies beyond the standard library.
"""
import os, secrets, stat

_O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_O_DIR_NF = _O_DIR | os.O_NOFOLLOW
# O_NONBLOCK: opening a FIFO that has no writer would otherwise block forever.
_O_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOCTTY | os.O_CLOEXEC | os.O_NONBLOCK
_O_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _owned_dir(fd, mode_repair):
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        return False
    if mode_repair and st.st_mode & 0o077:
        os.fchmod(fd, 0o700)          # an older install may have left 0755
    return True


def open_private_dir(path, private=2):
    """Open `path` (absolute) as a directory fd, one component at a time.

    The last `private` components are created 0700 if missing, opened
    O_NOFOLLOW, must be owned by us and are repaired to 0700. Earlier
    components may be symlinks (dotfile managers do that) but must still be
    directories owned by us once we are inside $HOME. Returns an fd or None.
    """
    parts = [p for p in os.path.normpath(path).split(os.sep) if p]
    if not parts:
        return None
    home_parts = [p for p in os.path.normpath(os.path.expanduser("~")).split(os.sep) if p]
    fd = os.open(os.sep, _O_DIR)
    try:
        for i, comp in enumerate(parts):
            is_private = i >= len(parts) - private
            inside_home = i >= len(home_parts)
            flags = _O_DIR_NF if is_private else _O_DIR
            try:
                nfd = os.open(comp, flags, dir_fd=fd)
            except FileNotFoundError:
                if not is_private:
                    return None
                try:
                    os.mkdir(comp, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                nfd = os.open(comp, flags, dir_fd=fd)
            os.close(fd)
            fd = nfd
            if (is_private or inside_home) and not _owned_dir(fd, mode_repair=is_private):
                return None
        out, fd = fd, -1
        return out
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def read_regular(dfd, name, cap):
    """Return the bytes of `name` in directory `dfd`, or None.

    Refuses symlinks, non-regular files (FIFOs, devices), files not owned by
    us, and anything over `cap` bytes; the size check and the read use the same
    open descriptor, so the file cannot be swapped in between.
    """
    if os.sep in name or name in ("", ".", ".."):
        return None
    try:
        fd = os.open(name, _O_FILE, dir_fd=dfd)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_size > cap:
            return None
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o600)
        chunks, total = [], 0
        while True:
            b = os.read(fd, min(1 << 20, cap + 1 - total))
            if not b:
                break
            chunks.append(b)
            total += len(b)
            if total > cap:              # grew under us: refuse rather than truncate
                return None
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def publish(dfd, name, data):
    """Atomically replace `name` in directory `dfd` with `data` (bytes), 0600.

    Writes to an exclusive random temp name in the same directory, fsyncs the
    file, renames over the target descriptor-relatively, then fsyncs the
    directory so the replacement is durable. Raises OSError on failure; the
    previous file (if any) is left intact.
    """
    if os.sep in name or name in ("", ".", ".."):
        raise ValueError("bad name")
    tmp = ".%s.%s.tmp" % (name, secrets.token_hex(8))
    fd = os.open(tmp, _O_CREATE, 0o600, dir_fd=dfd)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
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


def read_frames(stream, on_line, on_overflow=lambda: None, max_line=64 * 1024):
    """Bounded newline framing over a binary stream.

    Reads at most `max_line + 1` bytes per record, so an unterminated or
    oversized producer line can never grow a Python string beyond the cap.
    Oversized records are drained in bounded chunks and dropped. Returns at EOF.
    """
    while True:
        line = stream.readline(max_line + 1)
        if not line:
            return
        if len(line) > max_line:
            if not line.endswith(b"\n"):
                while True:                     # drain the rest of the record
                    chunk = stream.readline(max_line)
                    if not chunk or chunk.endswith(b"\n"):
                        break
            on_overflow()
            continue
        on_line(line.decode("utf-8", "replace").strip())
