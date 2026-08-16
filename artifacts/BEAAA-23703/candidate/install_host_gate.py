#!/usr/bin/python3
"""Transition the passive-demo host gate from the a68e7e09 shape to exact d9.

NOT RUN BY BEAAA-23703. This file is candidate bytes only. It is the reviewable
encoding of the transition for BEAAA-23702 to attest and for a host owner to
run later, as root, with the candidate directory bind-mounted at /staging.

It replaces two files, installs one, and removes two. It does not touch the SSH
key material or its options, does not create a deployment authorization, does
not pull an image, and does not start or stop a container. Deployment stays
fail-closed afterwards until a host owner creates the one-shot authorization
under a separate explicit decision.
"""

import base64
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

HOST = Path(os.environ.get("HOST_ROOT", "/"))
STAGING = Path(os.environ.get("STAGING_ROOT", "/staging"))

DEPLOY_ACCOUNT = "passive-demo-deploy"
DEPLOY_UID = 1002
DEPLOY_GID = 1002
EXPECTED_KEY_FP = "SHA256:z0GzuYG3rnNtFjMKeOsG36kDWrsXeG0larXTHd9V3Dg"
GATE_PATH = "/usr/local/sbin/backbond-real-agent-demo-ssh-gate"
HELPER_PATH = "/usr/local/sbin/backbond-real-agent-demo-secret-preflight"
SUDOERS_PATH = "/etc/sudoers.d/backbond-real-agent-demo"
STALE_RUNNER = "/usr/local/sbin/backbond-real-agent-demo-deploy"
STALE_SEALED = "/opt/backbond-real-agent-demo/sealed"
BACKUP_DIR = "/var/backups/backbond-demo/BEAAA-23703"

REPLACE = [
    ("backbond-real-agent-demo-ssh-gate", GATE_PATH, 0o755),
    ("backbond-real-agent-demo-secret-preflight", HELPER_PATH, 0o755),
    ("sudoers-backbond-real-agent-demo", SUDOERS_PATH, 0o440),
]


def fail(message: str) -> "NoReturn":
    print(f"install: {message}", file=sys.stderr)
    raise SystemExit(1)


def under(target: str) -> Path:
    return HOST / target.lstrip("/")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(line: str) -> str:
    parts = line.split()
    index = next(i for i, value in enumerate(parts) if value.startswith(("ssh-", "ecdsa-", "sk-")))
    raw = base64.b64decode(parts[index + 1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def check_authorized_key() -> None:
    """The key and its options must already be correct, and must not change."""
    auth = under(f"/home/{DEPLOY_ACCOUNT}/.ssh/authorized_keys")
    lines = [line for line in auth.read_text().splitlines() if line.strip()]
    if len(lines) != 1:
        fail("the deployment key file does not hold exactly one key")
    if fingerprint(lines[0]) != EXPECTED_KEY_FP:
        fail("the deployment key fingerprint changed")
    parts = lines[0].split()
    index = next(i for i, value in enumerate(parts) if value.startswith(("ssh-", "ecdsa-", "sk-")))
    options = " ".join(parts[:index])
    if options != f'restrict,command="{GATE_PATH}"':
        fail(f"unexpected authorized_keys options: {options}")


def atomic_install(source: Path, target: Path, mode: int) -> None:
    """Same-directory temp plus rename, so the path is never partially written."""
    temporary = target.with_name(target.name + ".beaaa-23703.tmp")
    data = source.read_bytes()
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, data)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != hashlib.sha256(data).hexdigest():
        os.unlink(temporary)
        fail(f"staged bytes for {target} did not survive the write")
    os.replace(temporary, target)


def verify_staging() -> None:
    """Refuse to install anything whose bytes drifted from the attested manifest."""
    manifest = STAGING / "SHA256SUMS"
    if not manifest.is_file():
        fail("candidate SHA256SUMS is missing from staging")
    recorded = {}
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        expected, name = line.split(maxsplit=1)
        recorded[name.strip().lstrip("*")] = expected
    for name, _, _ in REPLACE:
        candidate = STAGING / name
        if not candidate.is_file():
            fail(f"candidate file is missing from staging: {name}")
        if name not in recorded:
            fail(f"candidate file is absent from SHA256SUMS: {name}")
        actual = digest(candidate)
        if actual != recorded[name]:
            fail(f"candidate {name} sha256 {actual} does not match the attested manifest")
        print(f"verified {name} sha256={actual}")


def main() -> None:
    if os.geteuid() != 0:
        fail("must run as root")
    verify_staging()

    check_authorized_key()

    sudoers_candidate = STAGING / "sudoers-backbond-real-agent-demo"
    check = subprocess.run(
        ["/usr/sbin/visudo", "-c", "-f", str(sudoers_candidate)], capture_output=True, text=True
    )
    if check.returncode != 0:
        fail(f"candidate sudoers failed validation: {check.stderr.strip()}")

    backups = under(BACKUP_DIR)
    if backups.exists():
        fail(f"{BACKUP_DIR} already exists; a previous transition was recorded")
    backups.mkdir(mode=0o700, parents=True)
    os.chown(backups, 0, 0)
    for target in (GATE_PATH, SUDOERS_PATH, STALE_RUNNER):
        existing = under(target)
        if existing.exists():
            saved = backups / (Path(target).name + ".before")
            shutil.copy2(existing, saved)
            os.chown(saved, 0, 0)
            os.chmod(saved, 0o600)
            print(f"backup {target} sha256={digest(saved)}")

    if under(HELPER_PATH).exists():
        fail(f"{HELPER_PATH} already exists; refusing to replace the privileged helper")

    for name, target, mode in REPLACE:
        atomic_install(STAGING / name, under(target), mode)
        installed = under(target)
        st = installed.lstat()
        if st.st_uid != 0 or st.st_gid != 0 or stat.S_IMODE(st.st_mode) != mode:
            fail(f"{target} did not land with root ownership and mode {mode:04o}")
        print(f"install {target} mode={mode:04o} sha256={digest(installed)}")

    runner = under(STALE_RUNNER)
    if runner.exists():
        os.unlink(runner)
        print(f"remove {STALE_RUNNER}")
    sealed = under(STALE_SEALED)
    if sealed.is_dir():
        if any(sealed.iterdir()):
            fail(f"{STALE_SEALED} is not empty; a host owner must review its contents")
        os.rmdir(sealed)
        print(f"remove {STALE_SEALED}")

    check_authorized_key()
    print("install=PASS armed=false deployed=false key_fingerprint=" + EXPECTED_KEY_FP)


if __name__ == "__main__":
    main()
