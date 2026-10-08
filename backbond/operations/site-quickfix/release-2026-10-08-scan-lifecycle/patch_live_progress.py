#!/usr/bin/env python3
import argparse
import os
import stat
from pathlib import Path

OLD = '''_PROGRESS_FIELDS = {
    "state", "phase", "stage_label", "processed_count", "accepted_pack_total",
    "usable_count", "unavailable_count", "timing", "heartbeat_at", "updated_at",
}
'''
NEW = '''_PROGRESS_FIELDS = {
    "state", "phase", "stage_label", "processed_count", "accepted_pack_total",
    "usable_count", "unavailable_count", "timing", "heartbeat_at", "updated_at",
    "last_activity_at", "current_test_id", "current_family",
}
'''
GUARDS = (
    'tenant_id = f"partner-{row[\'partner\']}"',
    'kaltura_genie_browser requires the approved public URL',
    'kaltura_genie_browser requires auth type none',
    'if value["status"] == "dispatch_unknown":',
    'if value["status"] == "finished" and value.get("report_emailed"):',
)

def patch(target: Path, backup: Path) -> bool:
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or target.is_symlink():
        raise RuntimeError("target must be a regular non-symlink file")
    source = target.read_text()
    missing = [guard for guard in GUARDS if guard not in source]
    if missing:
        raise RuntimeError(f"live guard mismatch: {missing}")
    if NEW in source and OLD not in source:
        return False
    if source.count(OLD) != 1:
        raise RuntimeError("progress field context mismatch")
    updated = source.replace(OLD, NEW)
    compile(updated, str(target), "exec")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(backup, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source.encode())
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    temporary = target.with_name(f".{target.name}.beaaa-39323-{os.getpid()}")
    temporary.write_text(updated)
    temporary.chmod(stat.S_IMODE(info.st_mode))
    os.replace(temporary, target)
    return True

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    print(f"changed={str(patch(args.target, args.backup)).lower()} backup={args.backup}")

if __name__ == "__main__":
    main()
