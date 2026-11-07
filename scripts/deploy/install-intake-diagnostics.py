#!/usr/bin/python3
"""Reviewed installation artifact. Not authorization. Run with python3 -I.

Only install/rollback arguments. Source bytes must be staged root-owned under
/root/intake-diagnostic-reviewed. Refuse replacement of existing different files.
"""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

STAGE = Path('/root/intake-diagnostic-reviewed')
READER_SHA256 = 'd0e0dc4a17623acc3dfdeac7b0d0e712f46d3fec51057e3805f20f9a392b55d3'
READER = Path('/usr/local/libexec/backbond-intake-runtime-reader.py')
WRAPPER = Path('/usr/local/sbin/backbond-diagnose-intake-runtime')
GRANT = Path('/etc/sudoers.d/backbond-intake-runtime')
WRAPPER_BYTES = (b'#!/bin/sh\n[ "$#" -eq 0 ] || exit 2\n'
                 b'exec /usr/bin/python3 -I /usr/local/libexec/backbond-intake-runtime-reader.py\n')
GRANT_BYTES = b'beai-agent ALL=(root) NOPASSWD: /usr/local/sbin/backbond-diagnose-intake-runtime ""\n'


def trusted(path, *, directory=False):
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or (parent != path and not stat.S_ISDIR(info.st_mode))):
            raise RuntimeError('unsafe root-controlled path')
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('expected directory')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1048576:
        raise RuntimeError('unsafe regular file')


def main():
    if os.geteuid() != 0 or sys.argv[1:] not in [['install'], ['rollback']]:
        raise SystemExit('requires authorized root installer; install or rollback')
    trusted(STAGE, directory=True)
    source = STAGE / 'diagnose-intake-runtime.py'
    trusted(source)
    reader = source.read_bytes()
    if hashlib.sha256(reader).hexdigest() != READER_SHA256:
        raise RuntimeError('reviewed reader hash mismatch')
    targets = [(READER, reader, 0o644), (WRAPPER, WRAPPER_BYTES, 0o755),
               (GRANT, GRANT_BYTES, 0o440)]
    # Check every target before making any change. Existing different bytes or
    # modes are never overwritten, and symlinks (including dangling) are refused.
    for path, data, mode in targets:
        trusted(path.parent, directory=True)
        if os.path.lexists(path):
            trusted(path)
            if path.read_bytes() != data or stat.S_IMODE(path.stat().st_mode) != mode:
                raise RuntimeError('existing installation differs; explicit review needed')
    subprocess.run(['/usr/sbin/visudo', '-c'], check=True)
    staged_grant = STAGE / 'sudoers.candidate'
    if sys.argv[1] == 'rollback':
        # Only exact candidate bytes can be removed; no service/runtime changes.
        for path, _, _ in reversed(targets):
            if path.exists():
                path.unlink()
        subprocess.run(['/usr/sbin/visudo', '-c'], check=True)
        return
    if os.path.lexists(staged_grant):
        trusted(staged_grant)
        if staged_grant.read_bytes() != GRANT_BYTES:
            raise RuntimeError('staged sudoers differs')
    else:
        fd = os.open(staged_grant, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o440)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(GRANT_BYTES)
    subprocess.run(['/usr/sbin/visudo', '-cf', str(staged_grant)], check=True)
    created = []
    try:
        for path, data, mode in targets:
            if path.exists():
                continue
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            created.append(path)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                os.fchmod(handle.fileno(), mode)
                handle.flush()
                os.fsync(handle.fileno())
        subprocess.run(['/usr/sbin/visudo', '-c'], check=True)
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise


if __name__ == '__main__':
    main()
