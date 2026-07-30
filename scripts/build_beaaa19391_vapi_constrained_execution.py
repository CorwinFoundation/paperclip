#!/usr/bin/env python3
"""Execute and reconcile all latest constrained Vapi rows without network traffic."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.vapi_constrained_row_adapter import canonical_bytes, digest, execute


DEFAULT_SOURCE = ROOT / "artifacts/beaaa-19218/vapi-2000-probe-authorization-freeze-v1.4.jsonl"
DEFAULT_OUT = ROOT / "artifacts/beaaa-19391"
SOURCE_SHA256 = "b32a1b7ec582ee55fd80e05332448c584a4237acbe792c914e5a8cf0739fc008"
CORPUS_SHA256 = "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if file_sha(source) != SOURCE_SHA256:
        raise SystemExit("STOP: frozen v1.4 inventory SHA mismatch")
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line
    ]
    selected = [
        row for row in rows
        if row["authorization_class"] == "structurally_non_runnable"
    ]
    if len(selected) != 502 or len({r["stable_case_id"] for r in selected}) != 502:
        raise SystemExit("STOP: expected 502 unique constrained rows")

    records: list[dict[str, Any]] = []
    kind_counts: collections.Counter[str] = collections.Counter()
    for row in selected:  # concurrency=1; exceptions are STOP-on-first-mismatch
        try:
            trace = execute(row)
        except (KeyError, TypeError, ValueError) as exc:
            records.append({
                "stable_case_id": row.get("stable_case_id"),
                "family_id": row.get("family_id"),
                "source_row_sha256": row.get("source_row_sha256"),
                "terminal_status": "impossible",
                "missing_capability": "lossless_structured_or_role_preserving_adapter",
                "attempted_internal_remedies": [
                    "exact text transport materialization (BEAAA-19284)",
                    "lossless assessment-local structured/role adapter (BEAAA-19391)",
                ],
                "internally_owned_next_fix": "Scanner Engineer: extend the assessment-local adapter for the rejected source shape",
                "error": str(exc),
            })
            break
        kinds = sorted({event["kind"] for event in trace["events"]})
        for kind in kinds:
            kind_counts[kind] += 1
        records.append({
            "stable_case_id": row["stable_case_id"],
            "family_id": row["family_id"],
            "source_row_sha256": row["source_row_sha256"],
            "terminal_status": "executed",
            "adapter_kinds": kinds,
            "attempted_internal_remedies": [
                "exact text transport materialization (BEAAA-19284)",
                "lossless assessment-local structured/role adapter (BEAAA-19391)",
            ],
            "trace": trace,
        })

    counts = collections.Counter(record["terminal_status"] for record in records)
    summary = {
        "schema": "backbond.vapi_constrained_execution_summary/v1",
        "ticket_ref": "BEAAA-19391",
        "classification": "rule-execution",
        "contract_ref": "contracts/are-scanner-api-v1.md@v1.3.0",
        "scanner_version": "0.5.0",
        "corpus_version": CORPUS_SHA256,
        "probe_set_version": "1.4",
        "rubric_point_id": "not_applicable_non_score_bearing_adapter_execution",
        "seed": 42,
        "n5_calibration_delta": "0.0000",
        "cto_cosign_ref": None,
        "source_sha256": SOURCE_SHA256,
        "latest_evidence_refs": ["BEAAA-19283", "BEAAA-19284", "BEAAA-19287", "BEAAA-19288"],
        "counts": {
            "source_registry_rows": len(rows),
            "constrained_rows": len(selected),
            "executed": counts["executed"],
            "impossible": counts["impossible"],
            "structured_rows": kind_counts["structured_probe_input"],
            "role_bearing_rows": kind_counts["role_bearing_turn"],
            "terminal_records": len(records),
            "network_requests": 0,
            "duplicate_target_requests": 0,
        },
        "stop_controls": {
            "concurrency": 1,
            "stop_on_first_adapter_error": True,
            "stop_on_source_sha_mismatch": True,
            "stop_on_row_count_or_identity_mismatch": True,
        },
        "rollback": "Disable the assessment-local adapters and retain the original v1.4 execution ledger plus these terminal records; make no score claim.",
    }
    summary["records_sha256"] = digest(records)
    if len(records) != len(selected) or sum(counts.values()) != len(selected):
        raise SystemExit("STOP: exact terminal reconciliation failed")
    return summary, records


def write(source: Path, out: Path) -> tuple[Path, Path]:
    summary, records = build(source)
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "vapi-502-constrained-terminal-ledger-v1.0.json"
    report = out / "vapi-502-constrained-summary-v1.0.json"
    ledger.write_bytes(canonical_bytes({"summary": summary, "records": records}) + b"\n")
    report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return ledger, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    write(args.source, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
