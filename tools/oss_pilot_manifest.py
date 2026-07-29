"""Validate disabled, immutable OSS pilot manifests.

The manifests are planning/provenance inputs only.  This module is deliberately
not imported by the active-test registry or any scoring path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = REPO_ROOT / "config" / "oss-pilots"
IMMUTABLE_COMMIT = re.compile(r"^[0-9a-f]{40}$")
INTEGRITY = re.compile(r"^(sha256:[0-9a-f]{64}|git-tree-sha1:[0-9a-f]{40}|pinned-source-tree:[0-9a-f]{40})$")
KNOWN_CASE_CLASSES = {"agent_behavior", "customer_control", "scanner_substrate"}
REQUIRED = {
    "source_project",
    "source_commit",
    "source_path",
    "source_integrity",
    "license",
    "local_case_id",
    "capability_family",
    "case_class",
    "applicability",
    "oracle_version",
}


class ManifestValidationError(ValueError):
    """A pilot manifest is unsafe or incomplete."""


def validate_manifests(paths: list[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    local_ids: set[str] = set()
    for path in paths:
        try:
            document = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestValidationError(f"{path}: unreadable JSON: {exc}") from exc
        if document.get("schema_version") != 1:
            raise ManifestValidationError(f"{path}: schema_version must be 1")
        if document.get("enabled") is not False:
            raise ManifestValidationError(f"{path}: pilot must default disabled")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ManifestValidationError(f"{path}: entries must be a non-empty list")
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                raise ManifestValidationError(f"{path}: entry {index} must be an object")
            missing = sorted(REQUIRED - entry.keys())
            if missing:
                raise ManifestValidationError(f"{path}: entry {index} missing provenance: {missing}")
            if not IMMUTABLE_COMMIT.fullmatch(str(entry["source_commit"])):
                raise ManifestValidationError(f"{path}: entry {index} source_commit is mutable")
            if not INTEGRITY.fullmatch(str(entry["source_integrity"])):
                raise ManifestValidationError(f"{path}: entry {index} source_integrity is invalid")
            has_id = "source_case_id" in entry
            has_index = "source_case_index" in entry
            if has_id == has_index:
                raise ManifestValidationError(
                    f"{path}: entry {index} requires exactly one source_case_id or source_case_index"
                )
            local_id = entry["local_case_id"]
            if not isinstance(local_id, str) or not local_id:
                raise ManifestValidationError(f"{path}: entry {index} local_case_id is invalid")
            if local_id in local_ids:
                raise ManifestValidationError(f"{path}: duplicate local_case_id: {local_id}")
            local_ids.add(local_id)
            if entry["case_class"] not in KNOWN_CASE_CLASSES:
                raise ManifestValidationError(f"{path}: entry {index} unknown case_class")
            for field in REQUIRED - {"case_class"}:
                if not isinstance(entry[field], str) or not entry[field]:
                    raise ManifestValidationError(f"{path}: entry {index} {field} must be non-empty")
            entries.append(entry)
    return entries


def manifest_set_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    paths = sorted(MANIFEST_DIR.glob("*.json"))
    entries = validate_manifests(paths)
    print(json.dumps({"manifests": len(paths), "entries": len(entries), "sha256": manifest_set_digest(paths)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
