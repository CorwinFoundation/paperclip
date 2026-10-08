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
LEGACY_INSERT_ANCHOR = '''def _status(token: str) -> dict:
    value = store.status(token)
    if not value:
        raise HTTPException(404, "scan not found")
    return value


'''
FORWARDING_BLOCK = '''_PROGRESS_FIELDS = {
    "state", "phase", "stage_label", "processed_count", "accepted_pack_total",
    "usable_count", "unavailable_count", "timing", "heartbeat_at", "updated_at",
    "last_activity_at", "current_test_id", "current_family",
}


def _engine_progress(scan_id: str) -> dict:
    """Fetch the bounded owner-scoped engine projection, never its evidence rows."""
    row = store.scan(scan_id)
    if not row or not row["scanner_id"] or row["status"] not in {"running", "dispatch_unknown"}:
        return {}
    tenant_id = f"partner-{row['partner']}"
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(
                f"{SCANNER_URL}/scans/{row['scanner_id']}/progress",
                headers=_headers({"tenant_id": tenant_id}),
            )
        if response.status_code != 200:
            return {}
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in _PROGRESS_FIELDS if key in payload}


'''
LEGACY_STATUS = '''@app.post("/scan/v1/scan-status")
def scan_status(body: StatusRequest):
    # POST so the bearer-style token never lands in access-log URLs.
    value = _status(body.status_token)
    value.pop("scan_id")
    return value
'''
FORWARDING_STATUS = '''@app.post("/scan/v1/scan-status")
def scan_status(body: StatusRequest):
    # POST so the bearer-style token never lands in access-log URLs.
    value = _status(body.status_token)
    progress = _engine_progress(value["scan_id"])
    if progress:
        value.update(progress)
        value["status"] = progress.get("state", value["status"])
    value.pop("scan_id")
    return value
'''
GUARDS = (
    'def _tenant_tokens() -> dict:',
    'kaltura_genie_browser requires the approved public URL',
    'kaltura_genie_browser requires auth type none',
    '"dispatch_unknown"',
    'def _mail_configured() -> bool:',
)

def patch(target: Path, backup: Path) -> bool:
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or target.is_symlink():
        raise RuntimeError("target must be a regular non-symlink file")
    source = target.read_text()
    missing = [guard for guard in GUARDS if guard not in source]
    if missing:
        raise RuntimeError(f"live guard mismatch: {missing}")
    if NEW in source and LEGACY_STATUS not in source:
        return False
    if source.count(OLD) == 1:
        updated = source.replace(OLD, NEW)
    elif source.count(NEW) == 1 and source.count(LEGACY_STATUS) == 1:
        updated = source.replace(LEGACY_STATUS, FORWARDING_STATUS)
    elif source.count(LEGACY_INSERT_ANCHOR) == 1 and source.count(LEGACY_STATUS) == 1:
        updated = source.replace(LEGACY_INSERT_ANCHOR, LEGACY_INSERT_ANCHOR + FORWARDING_BLOCK)
        updated = updated.replace(LEGACY_STATUS, FORWARDING_STATUS)
    else:
        raise RuntimeError("progress forwarding context mismatch")
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
