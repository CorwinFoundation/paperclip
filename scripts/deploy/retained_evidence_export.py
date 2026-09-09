#!/usr/bin/env python3
"""Export one immutable 187-row retained-evidence roster.

This program is intended to run as root through a narrowly scoped sudoers
entry.  It can read only regular files beneath the fixed evidence root and
writes a deterministic tar archive containing the requested responses and a
machine-readable provenance manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import tarfile
import tempfile

EVIDENCE_ROOT = Path("/var/lib/are-scanner/evidence")
EXPORT_ROOT = Path("/var/lib/are-scanner/exports")
EXPECTED_ROWS = 187
REQUEST_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_archive(request: dict, evidence_root: Path = EVIDENCE_ROOT) -> bytes:
    request_id = request.get("request_id")
    rows = request.get("rows")
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise ValueError("request_id must be a safe non-empty identifier")
    if not isinstance(rows, list) or len(rows) != EXPECTED_ROWS:
        raise ValueError(f"request must contain exactly {EXPECTED_ROWS} rows")

    root = evidence_root.resolve(strict=True)
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    members: list[tuple[str, bytes]] = []
    exported_rows: list[dict] = []

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be an object")
        row_id = row.get("row_id")
        relative_path = row.get("relative_path")
        expected_sha = row.get("sha256")
        provenance = row.get("provenance")
        if not isinstance(row_id, str) or not REQUEST_ID.fullmatch(row_id):
            raise ValueError("each row_id must be a safe non-empty identifier")
        if row_id in seen_ids:
            raise ValueError(f"duplicate row_id: {row_id}")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError(f"row {row_id} has no relative_path")
        if relative_path in seen_paths:
            raise ValueError(f"duplicate relative_path: {relative_path}")
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise ValueError(f"row {row_id} has invalid sha256")
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError(f"row {row_id} has no provenance")

        source = (root / relative_path).resolve(strict=True)
        if source.parent != root and root not in source.parents:
            raise ValueError(f"row {row_id} escapes evidence root")
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"row {row_id} is not a regular retained-evidence file")
        data = source.read_bytes()
        actual_sha = _sha256(data)
        if actual_sha != expected_sha:
            raise ValueError(f"row {row_id} sha256 mismatch")

        archive_path = f"responses/{row_id}.json"
        members.append((archive_path, data))
        exported_rows.append({
            "row_id": row_id,
            "archive_path": archive_path,
            "source_relative_path": relative_path,
            "sha256": actual_sha,
            "provenance": provenance,
        })
        seen_ids.add(row_id)
        seen_paths.add(relative_path)

    manifest = {
        "schema_version": "backbond.retained-evidence-export.v1",
        "request_id": request_id,
        "row_count": EXPECTED_ROWS,
        "rows": sorted(exported_rows, key=lambda item: item["row_id"]),
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    members.append(("manifest.json", manifest_bytes))

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o400
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path, help="JSON request readable by root")
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    archive = build_archive(request)
    request_id = request["request_id"]

    EXPORT_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    destination = EXPORT_ROOT / f"{request_id}.tar"
    with tempfile.NamedTemporaryFile(dir=EXPORT_ROOT, prefix=f".{request_id}.", delete=False) as tmp:
        tmp.write(archive)
        tmp.flush()
        os.fsync(tmp.fileno())
        temporary = Path(tmp.name)
    os.chmod(temporary, 0o440)
    deploy_user = pwd.getpwnam("are-deploy")
    os.chown(temporary, deploy_user.pw_uid, deploy_user.pw_gid)
    os.replace(temporary, destination)
    print(json.dumps({"archive": str(destination), "row_count": EXPECTED_ROWS, "sha256": _sha256(archive)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
