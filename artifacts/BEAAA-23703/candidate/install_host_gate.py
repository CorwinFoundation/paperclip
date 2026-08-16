#!/usr/bin/python3
"""Install the reviewed BEAAA-23703 host-gate bytes, or verify an installation.

Runs as root, on the host, under BEAAA-23705, after exact-byte QA approval. It
copies reviewed bytes into fixed root-owned targets and never executes any
repository content: the only programs it runs are the fixed system validators
named in MANIFEST.json, `/usr/bin/python3 -m py_compile` and
`/usr/sbin/visudo -cf`, neither of which executes the file it checks.

    install_host_gate.py --verify-only            # default, mutates nothing
    install_host_gate.py --confirm                # perform the installation
    install_host_gate.py --rollback --confirm     # restore from a backup set

Every install entry is handled the same way, whether or not the target already
exists:

    verify source bytes against MANIFEST.json
    verify the current target against its pinned replaces_sha256
    copy the current target into a root-owned 0700 backup directory
    write the reviewed bytes to a temporary file in the SAME directory
    set uid, gid and mode on that temporary file, then fsync it
    run the manifest's validator against the temporary file
    atomically rename it over the target
    re-attest the target: regular file, uid, gid, mode, sha256

The installation never creates the deployment authorization. Arming is a
separate operator action and this program refuses to run if an arm file is
already present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

CANDIDATE_DIR = Path(__file__).resolve().parent
BACKUP_ROOT = Path("/var/backups/backbond-real-agent-demo")
ARM_PATH = "/etc/backbond/real-agent-demo-deploy.allow"


class InstallError(Exception):
    """Fail closed: nothing is mutated after this is raised."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def host_path(host_root: Path, target: str) -> Path:
    return host_root / target.lstrip("/")


def attest(path: Path) -> dict:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise InstallError(f"{path} is not a regular file")
    return {
        "path": str(path),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "mode": oct(stat.S_IMODE(st.st_mode)),
        "sha256": sha256_file(path),
    }


def validate_with(validator: str | None, path: Path) -> None:
    """Run a fixed system validator. Neither validator executes the file."""
    if validator is None:
        return
    if validator == "/usr/bin/python3 -m py_compile":
        argv = ["/usr/bin/python3", "-m", "py_compile", str(path)]
    elif validator == "/usr/sbin/visudo -cf":
        argv = ["/usr/sbin/visudo", "-cf", str(path)]
    elif validator == "/usr/sbin/visudo -c":
        argv = ["/usr/sbin/visudo", "-c"]
    else:
        raise InstallError(f"validator is not on the fixed list: {validator}")
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise InstallError(f"validator failed for {path}: {result.stderr.strip()}")


def backup(path: Path, backups: Path, receipt: list) -> None:
    if not path.exists():
        receipt.append({"path": str(path), "backed_up": False, "reason": "absent"})
        return
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise InstallError(f"refusing to back up non-regular {path}")
    destination = backups / path.name
    shutil.copy2(path, destination)
    os.chown(destination, st.st_uid, st.st_gid)
    os.chmod(destination, stat.S_IMODE(st.st_mode))
    receipt.append(
        {
            "path": str(path),
            "backed_up": True,
            "backup": str(destination),
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mode": oct(stat.S_IMODE(st.st_mode)),
            "sha256": sha256_file(path),
        }
    )


def place(source: Path, target: Path, uid: int, gid: int, mode: int, validator: str | None) -> None:
    """Same-directory staging, validation, then atomic rename over the target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, staged_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    staged = Path(staged_name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(source.read_bytes())
            out.flush()
            os.fsync(out.fileno())
        os.chown(staged, uid, gid)
        os.chmod(staged, mode)
        validate_with(validator, staged)
        if sha256_file(staged) != sha256_file(source):
            raise InstallError(f"staged bytes for {target} do not match the source")
        os.rename(staged, target)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def check_preconditions(manifest: dict, host_root: Path) -> None:
    arm = host_path(host_root, ARM_PATH)
    if arm.exists():
        raise InstallError(
            f"{arm} is present; installation must not run while a deployment is armed"
        )
    for entry in manifest.get("must_not_change", []):
        target, expected = entry.get("path"), entry.get("sha256")
        if not target or not expected:
            continue
        path = host_path(host_root, target)
        if not path.exists():
            raise InstallError(f"{path} is required to be present and unchanged, but is absent")
        actual = sha256_file(path)
        if actual != expected:
            raise InstallError(f"{path} sha256 {actual} does not match the pinned {expected}")


def plan(manifest: dict, host_root: Path) -> list:
    steps = []
    for entry in manifest["install"]:
        source = CANDIDATE_DIR / entry["source"]
        if not source.is_file():
            raise InstallError(f"candidate source is missing: {source}")
        actual = sha256_file(source)
        if actual != entry["source_sha256"]:
            raise InstallError(
                f"{entry['source']} sha256 {actual} does not match the manifest {entry['source_sha256']}"
            )
        target = host_path(host_root, entry["target"])
        current = sha256_file(target) if target.is_file() else None
        expected_current = entry.get("replaces_sha256")
        if current is not None and expected_current is not None and current != expected_current:
            raise InstallError(
                f"{target} currently holds {current}, not the pinned {expected_current}"
            )
        if current is not None and expected_current is None:
            raise InstallError(
                f"{target} already exists but the manifest records no replaces_sha256"
            )
        steps.append(
            {
                "action": "replace" if current is not None else "create",
                "source": str(source),
                "target": str(target),
                "source_sha256": actual,
                "current_sha256": current,
                "uid": entry["owner_uid"],
                "gid": entry["owner_gid"],
                "mode": int(entry["mode"], 8),
                "validator": entry.get("validator"),
            }
        )
    for entry in manifest.get("remove", []):
        target = host_path(host_root, entry["target"])
        current = sha256_file(target) if target.is_file() else None
        if current is None:
            steps.append({"action": "remove", "target": str(target), "current_sha256": None,
                          "note": "already absent", "revalidate": entry.get("revalidate")})
            continue
        if current != entry["expected_sha256"]:
            raise InstallError(
                f"{target} currently holds {current}, not the pinned {entry['expected_sha256']}"
            )
        steps.append({"action": "remove", "target": str(target), "current_sha256": current,
                      "revalidate": entry.get("revalidate")})
    return steps


def install(steps: list, backups: Path, receipt: dict, system_validation: bool = True) -> None:
    """system_validation runs whole-system checks such as `visudo -c`, which are
    only meaningful when the targets are the real root."""
    backups.mkdir(parents=True, exist_ok=True)
    os.chown(backups, 0, 0)
    os.chmod(backups, 0o700)
    receipt["backups"] = []
    for step in steps:
        target = Path(step["target"])
        backup(target, backups, receipt["backups"])
    receipt["installed"] = []
    for step in steps:
        target = Path(step["target"])
        if step["action"] == "remove":
            target.unlink(missing_ok=True)
            if system_validation:
                validate_with(step.get("revalidate"), target)
            receipt["installed"].append({"action": "remove", "path": str(target), "present": target.exists()})
            continue
        place(Path(step["source"]), target, step["uid"], step["gid"], step["mode"], step["validator"])
        after = attest(target)
        if after["sha256"] != step["source_sha256"]:
            raise InstallError(f"post-install sha256 mismatch for {target}")
        if (after["uid"], after["gid"]) != (step["uid"], step["gid"]):
            raise InstallError(f"post-install ownership mismatch for {target}")
        if after["mode"] != oct(step["mode"]):
            raise InstallError(f"post-install mode mismatch for {target}")
        receipt["installed"].append({"action": step["action"], **after})


def rollback(backups: Path, manifest: dict, host_root: Path, receipt: dict) -> None:
    if not backups.is_dir():
        raise InstallError(f"backup directory {backups} does not exist")
    restored = []
    targets = [host_path(host_root, e["target"]) for e in manifest["install"]]
    targets += [host_path(host_root, e["target"]) for e in manifest.get("remove", [])]
    for target in targets:
        saved = backups / target.name
        if not saved.is_file():
            continue
        st = saved.lstat()
        place(saved, target, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode), None)
        restored.append(attest(target))
    receipt["restored"] = restored


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or verify the BEAAA-23703 host gate.")
    parser.add_argument("--confirm", action="store_true", help="perform the change")
    parser.add_argument("--verify-only", action="store_true", help="report the plan and exit")
    parser.add_argument("--rollback", action="store_true", help="restore from a backup set")
    parser.add_argument("--host-root", default="/", help="filesystem root, for rehearsal")
    parser.add_argument("--backup-dir", default=None, help="backup set directory")
    parser.add_argument("--receipt", default=None, help="write a JSON receipt here")
    arguments = parser.parse_args()

    host_root = Path(arguments.host_root)
    backups = Path(arguments.backup_dir) if arguments.backup_dir else host_path(host_root, str(BACKUP_ROOT))
    manifest = json.loads((CANDIDATE_DIR / "MANIFEST.json").read_text())
    receipt: dict = {"manifest_sha256": sha256_file(CANDIDATE_DIR / "MANIFEST.json"),
                     "release": manifest["release"], "host_root": str(host_root)}

    try:
        if arguments.rollback:
            if not arguments.confirm:
                print("rollback requires --confirm", file=sys.stderr)
                return 2
            if os.geteuid() != 0 and arguments.host_root == "/":
                raise InstallError("rollback must run as root")
            rollback(backups, manifest, host_root, receipt)
        else:
            check_preconditions(manifest, host_root)
            steps = plan(manifest, host_root)
            receipt["plan"] = steps
            for step in steps:
                print(f"{step['action']:>7}  {step['target']}")
            if not arguments.confirm or arguments.verify_only:
                print("verify-only: nothing was changed")
                receipt["applied"] = False
                return 0
            if os.geteuid() != 0 and arguments.host_root == "/":
                raise InstallError("installation must run as root")
            install(steps, backups, receipt, system_validation=(host_root == Path("/")))
            receipt["applied"] = True
            if host_path(host_root, ARM_PATH).exists():
                raise InstallError("an arm file appeared during installation")
    except InstallError as error:
        print(f"install=FAIL {error}", file=sys.stderr)
        return 1
    finally:
        if arguments.receipt:
            Path(arguments.receipt).write_text(json.dumps(receipt, indent=2) + "\n")

    print(f"install=PASS release={manifest['release']['commit']} armed=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
