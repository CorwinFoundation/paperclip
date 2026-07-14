#!/usr/bin/env python3
"""Verify BEAAA-14142 contract-anchor reissue manifests.

This verifier is provenance-only. It emits no BackBond Score and changes no
scoring, rubric, judge, API-shape, corpus-semantic, or runtime behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO / "artifacts" / "beaaa-14142"
REISSUED_DIR = ARTIFACT_DIR / "reissued"

CONTRACT_ANCHOR = "are-scanner-api-v1@v1.0"
TARGET_IDENTITY = "retell:agent_1e7015ae5f87ea825a3d1fac11"
TARGET_AGENT_ID = "agent_1e7015ae5f87ea825a3d1fac11"
EXPECTED_SOURCE_ARCHIVE_SHA = "c8ac1fb7190d12ab8f72326c0caf0efeafa73abac73a8f0cae110bf874c44ce1"
EXPECTED_SOURCE_MANIFEST_SHA = "7a57dc5eeecf34cda605d74b1dd566ac0cfa995a864c991213b46596dd88fb3d"
EXPECTED_COUNTS = {
    "accepted": 270,
    "attempted": 270,
    "evidenced": 270,
    "excluded": 0,
    "terminal": 270,
}
FIXTURE_MARKERS = (
    "localhost",
    "127.0.0.1",
    "mock",
    "echo",
    "fixture",
    "replay",
    "local_target_response",
    "prewritten",
    "metadata-only",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _has_fixture_marker(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key, "")).lower()
        for key in (
            "case_id",
            "evidence_ref",
            "target_identity",
            "target_service_identifier",
            "source_archive",
            "disposition",
        )
    )
    return any(marker in haystack for marker in FIXTURE_MARKERS)


def _eligible(row: dict[str, Any]) -> tuple[bool, str]:
    required_true = (
        "expected",
        "applicable",
        "attempted",
        "terminal",
        "evidenced",
        "prompt_or_action_present",
        "raw_trace_present",
        "deterministic_or_pinned_judge",
        "target_agent_verified",
    )
    missing = [field for field in required_true if row.get(field) is not True]
    if missing:
        return False, "missing_required_fields:" + ",".join(missing)
    if row.get("contract_version_recorded") != CONTRACT_ANCHOR:
        return False, "recorded_contract_mismatch"
    if row.get("parent_contract_anchor") != CONTRACT_ANCHOR:
        return False, "parent_contract_anchor_mismatch"
    if row.get("target_identity") != TARGET_IDENTITY:
        return False, "target_identity_mismatch"
    if row.get("target_agent_id") != TARGET_AGENT_ID:
        return False, "target_agent_id_mismatch"
    if row.get("source_ticket") != "BEAAA-14096":
        return False, "source_ticket_mismatch"
    if row.get("source_archive_sha256") != EXPECTED_SOURCE_ARCHIVE_SHA:
        return False, "source_archive_sha_mismatch"
    if not row.get("evidence_ref_sha256"):
        return False, "missing_evidence_ref_sha256"
    if not row.get("row_sha256"):
        return False, "missing_row_sha256"
    if _has_fixture_marker(row):
        return False, "fixture_or_mock_marker_present"
    return True, "accepted_after_canonical_pr_ci_merge_gate"


def verify(_: argparse.Namespace) -> None:
    source_archives = _read_json(REISSUED_DIR / "source_archive_manifest.json")
    source = source_archives.get("BEAAA-14142")
    if not source:
        raise AssertionError("missing BEAAA-14142 source archive manifest entry")
    archive_path = REPO / source["file"]
    if not archive_path.exists():
        raise AssertionError(f"missing source archive {archive_path}")
    if _sha256_file(archive_path) != EXPECTED_SOURCE_ARCHIVE_SHA:
        raise AssertionError(f"source archive SHA mismatch for {archive_path}")
    if source.get("sha256") != EXPECTED_SOURCE_ARCHIVE_SHA:
        raise AssertionError("source_archive_manifest sha256 mismatch")
    if source.get("source_manifest_sha256") != EXPECTED_SOURCE_MANIFEST_SHA:
        raise AssertionError("source manifest SHA mismatch")

    rows = _read_jsonl(REISSUED_DIR / "row_manifest.jsonl")
    cases = _read_jsonl(REISSUED_DIR / "case_manifest.jsonl")
    if len(rows) != 270:
        raise AssertionError(f"expected 270 row manifest entries, got {len(rows)}")
    if len(cases) != 270:
        raise AssertionError(f"expected 270 case manifest entries, got {len(cases)}")
    if len({row["case_id"] for row in rows}) != 270:
        raise AssertionError("duplicate case_id in row manifest")
    if {row["case_id"] for row in rows} != {row["case_id"] for row in cases}:
        raise AssertionError("case_manifest and row_manifest case IDs differ")

    counts = Counter()
    for row in rows:
        eligible, disposition = _eligible(row)
        if row.get("accepted") != eligible:
            raise AssertionError(f"{row['case_id']} accepted={row.get('accepted')} but verifier computed {eligible}")
        if row.get("disposition") != disposition:
            raise AssertionError(f"{row['case_id']} disposition mismatch")
        counts["attempted"] += int(row["attempted"])
        counts["terminal"] += int(row["terminal"])
        counts["evidenced"] += int(row["evidenced"])
        counts["accepted"] += int(row["accepted"])
        counts["excluded"] += int(not row["accepted"])

    if dict(counts) != EXPECTED_COUNTS:
        raise AssertionError(f"expected {EXPECTED_COUNTS}, got {dict(counts)}")

    report = _read_json(REISSUED_DIR / "count_report.json")
    if report.get("counts") != EXPECTED_COUNTS:
        raise AssertionError(f"count_report mismatch: {report.get('counts')} != {EXPECTED_COUNTS}")
    if report.get("remaining_after_acceptance") != 0:
        raise AssertionError("remaining_after_acceptance must be 0 for BEAAA-14142")

    metadata = _read_json(REISSUED_DIR / "run_metadata.json")
    if metadata.get("contract_version") != CONTRACT_ANCHOR:
        raise AssertionError("run_metadata contract_version mismatch")
    if metadata.get("target_identity") != TARGET_IDENTITY:
        raise AssertionError("run_metadata target_identity mismatch")
    if metadata.get("n5_calibration_delta", {}).get("max_total_delta") != 0:
        raise AssertionError("N=5 delta must be zero/not-applicable for provenance-only reissue")

    print(json.dumps({"status": "pass", "counts": dict(counts)}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.set_defaults(func=verify)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
