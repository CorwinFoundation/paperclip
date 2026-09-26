#!/usr/bin/env python3
"""BEAAA-35987 - shape-log of writes to the shared Codex credential.

All 20 codex_local seats symlink codex-home/auth.json to
/home/beai-agent/.codex/auth.json. The 401 signature
    Incorrect API key provided: sk-svcac***  url: .../backend-api/codex/responses
means that file transiently carried an OPENAI_API_KEY while auth_mode was still
chatgpt. The next OAuth refresh rewrites it, so a later grep finds nothing.
This watcher records the SHAPE of every write as it happens.

It never records a credential. For a key it logs the type prefix
(everything up to the third '-'), the length, and 12 hex of sha256 - enough to
correlate two sightings, not enough to use.
"""
import ctypes, ctypes.util, datetime, errno, hashlib, json, os, select, struct, sys, time

TARGET_DIR = "/home/beai-agent/.codex"
TARGET_NAME = "auth.json"
TARGET = os.path.join(TARGET_DIR, TARGET_NAME)
LOG = os.path.join(TARGET_DIR, "auth-writewatch.log")
HEARTBEAT_SECONDS = 900
# Three heartbeats. Tolerates one missed beat plus scheduling slop.
STALE_AFTER_SECONDS = 3 * HEARTBEAT_SECONDS

IN_MODIFY, IN_CLOSE_WRITE = 0x00000002, 0x00000008
IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x00000080, 0x00000100, 0x00000200
IN_MOVE_SELF, IN_DELETE_SELF = 0x00000800, 0x00000400
DIR_MASK = IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE
FILE_MASK = IN_MODIFY | IN_CLOSE_WRITE | IN_MOVE_SELF | IN_DELETE_SELF
NAMES = [(IN_MODIFY, "MODIFY"), (IN_CLOSE_WRITE, "CLOSE_WRITE"),
         (IN_MOVED_TO, "MOVED_TO"), (IN_CREATE, "CREATE"),
         (IN_DELETE, "DELETE"), (IN_MOVE_SELF, "MOVE_SELF"),
         (IN_DELETE_SELF, "DELETE_SELF")]


def emit(record):
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + \
        ".%03dZ" % (int(time.time() * 1000) % 1000)
    with open(LOG, "a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def fingerprint(value):
    """Shape of a secret. Never the secret."""
    if value is None:
        return None
    if not isinstance(value, str):
        return {"type": type(value).__name__}
    prefix = "-".join(value.split("-")[:3])[:24] if value.startswith("sk-") else value[:4]
    return {"prefix": prefix, "len": len(value),
            "sha256_12": hashlib.sha256(value.encode()).hexdigest()[:12]}


def shape():
    """Read the credential and describe it without copying it."""
    try:
        st = os.stat(TARGET)
    except OSError as exc:
        return {"present": False, "stat_errno": errno.errorcode.get(exc.errno, exc.errno)}
    out = {"present": True, "size": st.st_size, "mtime": round(st.st_mtime, 3),
           "inode": st.st_ino, "mode": oct(st.st_mode & 0o7777)}
    try:
        with open(TARGET) as fh:
            raw = fh.read()
    except OSError as exc:
        out["read_errno"] = errno.errorcode.get(exc.errno, exc.errno)
        return out
    out["bytes_sha256_12"] = hashlib.sha256(raw.encode()).hexdigest()[:12]
    try:
        doc = json.loads(raw)
    except ValueError:
        # A torn write is itself a finding: the CLI may read this state.
        out["parse"] = "invalid_json"
        return out
    if not isinstance(doc, dict):
        out["parse"] = "not_an_object"
        return out
    out["keys"] = sorted(doc.keys())
    out["auth_mode"] = doc.get("auth_mode")
    out["last_refresh"] = doc.get("last_refresh")
    out["api_key"] = fingerprint(doc.get("OPENAI_API_KEY"))
    tok = doc.get("tokens")
    if isinstance(tok, dict):
        out["tokens"] = {k: bool(tok.get(k)) for k in sorted(tok.keys())}
    else:
        out["tokens"] = None
    # The condition that produces the 401: a platform key present while the
    # provider is still in ChatGPT mode.
    out["poisoned"] = bool(out["api_key"]) and out["auth_mode"] == "chatgpt"
    return out


def writers():
    """Which processes hold this inode open, by /proc scan. Best effort."""
    try:
        target = os.stat(TARGET)
    except OSError:
        return []
    hits = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = "/proc/%s/fd" % pid
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                st = os.stat(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if st.st_ino == target.st_ino and st.st_dev == target.st_dev:
                try:
                    with open("/proc/%s/cmdline" % pid, "rb") as fh:
                        cmd = fh.read().replace(b"\0", b" ").decode(errors="replace")
                except OSError:
                    cmd = "?"
                hits.append({"pid": int(pid), "cmd": cmd.strip()[:160]})
                break
    return hits


def flags(mask):
    return [name for bit, name in NAMES if mask & bit] or [hex(mask)]


def assert_fresh(max_age_seconds, log_path=None):
    """Read-only liveness check over the newest log line. Returns an exit code.

    BEAAA-36045. ``Restart=always`` on the systemd --user unit restarts a process
    that *exits*; it cannot see a process that is alive but no longer receiving
    events -- a dropped inotify watch, an inode swapped out from under the watch,
    a wedged loop. In that state ``systemctl --user is-active`` still says
    ``active`` and the log simply goes quiet, which is indistinguishable from
    "nothing was written to the credential". The periodic ``alive`` line exists
    precisely to separate those two, and until now nothing read it.

    Any event counts as proof of life, not just ``alive``: a burst of ``write``
    records means the loop is very much running.
    """
    path = log_path or LOG
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        sys.stderr.write("STALE: no watcher log at %s — the watcher has never run\n" % path)
        return 5
    newest = None
    for line in reversed(lines):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            newest = json.loads(line)
        except ValueError:
            continue
        break
    if not newest or not newest.get("ts"):
        sys.stderr.write("STALE: %s has no parseable timestamped event\n" % path)
        return 5
    raw = str(newest["ts"])
    try:
        stamped = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        sys.stderr.write("STALE: newest event has an unreadable ts: %r\n" % raw)
        return 5
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - stamped).total_seconds()
    if age > max_age_seconds:
        sys.stderr.write(
            "STALE: newest watcher event is %ds old (max %ds), event=%s at %s. "
            "The process may be alive but no longer watching — check "
            "`systemctl --user status codex-auth-writewatch.service` and restart it.\n"
            % (age, max_age_seconds, newest.get("event"), raw))
        return 5
    sys.stdout.write("OK: newest watcher event %ds ago (max %ds) — event=%s at %s\n"
                     % (age, max_age_seconds, newest.get("event"), raw))
    return 0


def main():
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
    fd = libc.inotify_init1(0x80000)  # IN_CLOEXEC == O_CLOEXEC
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1 errno=%d" % ctypes.get_errno())
    dir_wd = libc.inotify_add_watch(fd, TARGET_DIR.encode(), DIR_MASK)
    if dir_wd < 0:
        raise OSError(ctypes.get_errno(), "inotify_add_watch %s" % TARGET_DIR)
    # Watch the inode too: a write through any seat's symlink lands here, and a
    # rename-in-place swaps the inode out from under the directory entry.
    file_wd = libc.inotify_add_watch(fd, TARGET.encode(), FILE_MASK)
    emit({"event": "watcher_start", "pid": os.getpid(),
          "dir_wd": dir_wd, "file_wd": file_wd, "baseline": shape()})
    while True:
        # select, not a bare blocking read: without a timeout the periodic
        # alive line never fires and a silent log is ambiguous.
        try:
            ready, _, _ = select.select([fd], [], [], HEARTBEAT_SECONDS)
        except InterruptedError:
            continue
        if not ready:
            emit({"event": "alive", "shape": shape()})
            continue
        try:
            buf = os.read(fd, 8192)
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        offset, seen = 0, []
        while offset + 16 <= len(buf):
            wd, mask, _cookie, length = struct.unpack_from("iIII", buf, offset)
            name = buf[offset + 16:offset + 16 + length].split(b"\0", 1)[0].decode(errors="replace")
            offset += 16 + length
            if wd == dir_wd and name != TARGET_NAME:
                continue
            seen.append({"wd": wd, "name": name or TARGET_NAME, "flags": flags(mask)})
            if mask & (IN_DELETE_SELF | IN_MOVE_SELF):
                if file_wd >= 0:
                    libc.inotify_rm_watch(fd, file_wd)
                file_wd = libc.inotify_add_watch(fd, TARGET.encode(), FILE_MASK)
        if not seen:
            continue
        # Read the shape immediately - a poisoned window can be brief.
        record = {"event": "write", "inotify": seen, "shape": shape()}
        if record["shape"].get("poisoned") or record["shape"].get("api_key"):
            record["open_by"] = writers()
        emit(record)


def _parse_args(argv):
    """Returns (mode, max_age) or raises SystemExit(64) on a bad argument."""
    if not argv:
        return "watch", None
    if len(argv) == 1:
        arg = argv[0]
        if arg == "--assert-fresh":
            return "assert-fresh", STALE_AFTER_SECONDS
        if arg.startswith("--assert-fresh="):
            raw = arg.split("=", 1)[1]
            if not raw.isdigit():
                sys.stderr.write("--assert-fresh: SECONDS must be a non-negative integer, got: %r\n" % raw)
                raise SystemExit(64)
            return "assert-fresh", int(raw)
    sys.stderr.write("usage: %s [--assert-fresh[=SECONDS]]\n" % os.path.basename(sys.argv[0] or "codex-auth-writewatch.py"))
    raise SystemExit(64)


if __name__ == "__main__":
    mode, max_age = _parse_args(sys.argv[1:])
    if mode == "assert-fresh":
        raise SystemExit(assert_fresh(max_age))
    try:
        main()
    except Exception as exc:  # a dead watcher must say so in its own log
        emit({"event": "watcher_error", "error": "%s: %s" % (type(exc).__name__, exc)})
        raise
