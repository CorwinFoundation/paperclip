"""Root-owned launcher for one disposable, disk-bounded rootless Podman pod.

Only the broker imports this module. Never install it in a writable job volume.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess

STATE = Path("/var/lib/backbond-build-pool")
MIN_FREE_DISK_BYTES = 19 * 1024 ** 3
IMAGE_ARCHIVE = Path("/opt/backbond-build-pool/images.tar")
RUNNER_IMAGE = "localhost/backbond-build-runner:2.337.0"
DIND_IMAGE = "docker.io/library/docker@sha256:9e79b879b177fbd72c34376a3dd3d91747da5db49e1f560f76aea59561471977"


def command(*args, **kwargs):
    kwargs.setdefault("check", True)
    return subprocess.run(list(args), **kwargs)


class Worker:
    def __init__(self, identity: str, role: str):
        if not re.fullmatch(r"[0-9a-f]{32}", identity) or role not in {"build", "publish"}:
            raise ValueError("invalid worker identity")
        self.identity, self.role = identity, role
        self.account = pwd.getpwnam(f"backbond-{role}")
        self.root = STATE / identity
        self.data = self.root / "data"
        # Podman 4.9 rejects runroots longer than 50 characters, and its Unix
        # sockets need further space. The root-owned parent prevents precreation.
        self.runtime = Path("/run/bb") / identity[:16]
        self.pod = f"bb-{identity}"

    def podman(self, *args, **kwargs):
        return command("runuser", "-u", self.account.pw_name, "--", "env",
                       f"HOME={self.data / 'host-home'}",
                       f"XDG_CONFIG_HOME={self.data / 'host-home' / '.config'}",
                       f"XDG_CACHE_HOME={self.data / 'host-home' / '.cache'}",
                       f"TMPDIR={self.data / 'host-home' / 'tmp'}",
                       f"XDG_RUNTIME_DIR={self.runtime}",
                       f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{self.account.pw_uid}/bus",
                       "podman", "--root", str(self.data / "store"),
                       "--runroot", str(self.runtime / "e"),
                       "--tmpdir", str(self.runtime / "t"), *args,
                       cwd="/", **kwargs)

    def prepare(self, expected: dict, jit: str):
        # No user-controlled path components and no reusable job state.
        if shutil.disk_usage(STATE).free < MIN_FREE_DISK_BYTES:
            raise RuntimeError("build disk reservation would leave less than 5 GiB free")
        command("systemctl", "start", f"user@{self.account.pw_uid}.service")
        self.root.mkdir(mode=0o711)
        os.chown(self.root, 0, self.account.pw_gid)
        # crun's mapped root must traverse this root-owned ancestor too. The
        # role-owned data directory and group-only admission remain private.
        self.root.chmod(0o711)
        self.data.mkdir()
        disk = self.root / "job.ext4"
        command("fallocate", "-l", "14G", str(disk))
        command("mkfs.ext4", "-q", "-m", "0", "-E", "nodiscard", str(disk), stdout=subprocess.DEVNULL)
        command("mount", "-o", "loop,nodev,nosuid", str(disk), str(self.data))
        os.chown(self.data, self.account.pw_uid, self.account.pw_gid)
        for name in ("home", "docker", "socket", "host-home", "host-home/tmp"):
            target = self.data / name
            target.mkdir(mode=0o700)
            os.chown(target, self.account.pw_uid, self.account.pw_gid)
        self.runtime.parent.mkdir(mode=0o711, exist_ok=True)
        if self.runtime.parent.stat().st_uid != 0:
            raise RuntimeError("untrusted runtime parent")
        self.runtime.parent.chmod(0o711)
        self.runtime.mkdir(mode=0o700)
        os.chown(self.runtime, self.account.pw_uid, self.account.pw_gid)
        # The rootless pause helper uses this fixed path even when --tmpdir
        # overrides Podman's engine temp directory.
        for name in ("libpod", "libpod/tmp", "e", "t"):
            nested = self.runtime / name
            nested.mkdir(mode=0o700)
            os.chown(nested, self.account.pw_uid, self.account.pw_gid)
        admission = self.root / "admission"
        admission.mkdir(mode=0o750)
        os.chown(admission, 0, self.account.pw_gid)
        admission.chmod(0o750)
        (admission / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
        (admission / "jit").write_text(jit, encoding="utf-8")
        # JIT config is a one-job identity. Administration credentials never enter here.
        for file in admission.iterdir():
            os.chown(file, 0, self.account.pw_gid)
            file.chmod(0o440)
        with IMAGE_ARCHIVE.open("rb") as archive:
            self.podman("load", stdin=archive, stdout=subprocess.DEVNULL)
        self.podman("pod", "create", "--name", self.pod, "--network",
                    "slirp4netns:allow_host_loopback=false", "--cpus", "1.5",
                    "--userns", "keep-id:uid=1001,gid=1001",
                    "--memory", "5g", stdout=subprocess.DEVNULL)
        home = f"{self.data / 'home'}:/home/backbond-ci:rw"
        socket = f"{self.data / 'socket'}:/docker-socket:rw"
        # Privilege is limited to a fresh, otherwise empty host UID/user namespace.
        # Never substitute a host or another runner's Docker socket here.
        self.podman("run", "--detach", "--name", f"{self.pod}-docker", "--pod", self.pod,
                    "--privileged", "--security-opt", "no-new-privileges",
                    "--pids-limit", "1024",
                    "--oom-score-adj", "1000",
                    "--user", "0:0",
                    "--volume", home, "--volume", socket,
                    "--volume", f"{self.data / 'docker'}:/var/lib/docker:rw",
                    DIND_IMAGE, "dockerd", "--host=unix:///docker-socket/docker.sock",
                    "--group=1001", "--storage-driver=overlay2", stdout=subprocess.DEVNULL)
        self.podman("exec", f"{self.pod}-docker", "sh", "-c",
                    "for n in $(seq 1 60); do docker -H unix:///docker-socket/docker.sock info >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1",
                    timeout=75)
        self.podman("run", "--detach", "--name", f"{self.pod}-runner", "--pod", self.pod,
                    "--user", "1001:1001",
                    "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", "1024",
                    "--oom-score-adj", "1000",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m", "--tmpfs", "/run:rw,nosuid,nodev,size=32m",
                    "--volume", home, "--volume", socket,
                    "--volume", f"{admission}:/run/backbond:ro", RUNNER_IMAGE,
                    stdout=subprocess.DEVNULL)

    def wait(self, minutes: int):
        # JIT runner exits after its single job. An absent/failed Docker daemon
        # fails the job; it must never trigger hosted replacement execution.
        self.podman("wait", f"{self.pod}-runner", timeout=minutes * 60,
                    stdout=subprocess.DEVNULL)

    def cleanup(self):
        # Do not free admission to other workers if pod removal or unmount fails.
        if not self.root.exists():
            return
        if os.path.ismount(self.data):
            # Podman rm --ignore is idempotent and touches only this exact pod.
            if self.runtime.exists():
                self.podman("pod", "rm", "--force", "--ignore", self.pod,
                            stdout=subprocess.DEVNULL)
                self.podman("unmount", "--all", stdout=subprocess.DEVNULL)
                # A private XDG_RUNTIME_DIR gives each job its own rootless
                # pause/mount namespace. End it before detaching the loop FS.
                self.podman("system", "migrate", stdout=subprocess.DEVNULL)
            command("loginctl", "terminate-user", str(self.account.pw_uid),
                    check=False, stderr=subprocess.DEVNULL)
            command("systemctl", "stop", f"user@{self.account.pw_uid}.service",
                    f"user-runtime-dir@{self.account.pw_uid}.service")
            command("umount", str(self.data))
        # root is root-owned; rmtree does not follow job-created symlinks.
        shutil.rmtree(self.root)
        if self.runtime.exists():
            shutil.rmtree(self.runtime)
