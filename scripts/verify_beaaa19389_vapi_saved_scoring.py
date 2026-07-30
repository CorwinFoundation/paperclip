#!/usr/bin/env python3
"""Build the BEAAA-19389 offline saved-response scoring verification receipt."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "artifacts/beaaa-19218/vapi-2000-probe-authorization-freeze-v1.4.jsonl"
LEDGER = ROOT / "artifacts/beaaa-19322/vapi-2000-scoreability-ledger-v1.1.json"
VERDICTS = ROOT / "artifacts/beaaa-19322/vapi-897-deterministic-verdicts-v1.1.json"
OUTPUT = ROOT / "artifacts/beaaa-19389/vapi-saved-response-scoring-verification-v1.0.json"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _reason(record: dict[str, Any]) -> str:
    if record["verdict"] == "INVALID":
        return "unsupported_family:" + ",".join(record["uncovered_dimensions"])
    fields = ",".join(f"{key}={json.dumps(value, sort_keys=True)}" for key, value in sorted(record["derived_fields"].items()))
    return f"{record['oracle']}:{fields}"


def build_receipt() -> dict[str, Any]:
    registry_rows = [json.loads(line) for line in REGISTRY.read_text().splitlines() if line]
    ledger = json.loads(LEDGER.read_text())
    verdict_artifact = json.loads(VERDICTS.read_text())

    assert len(registry_rows) == len(ledger["rows"]) == 2000
    assert sha256_file(REGISTRY) == ledger["source_freeze"]["sha256"]
    eligible_ids = {
        row["stable_case_id"]
        for row in ledger["rows"]
        if row["execution_status"] == "executed" and row["judge_mode"] == "deterministic"
    }
    verdicts = verdict_artifact["records"]
    assert len(eligible_ids) == 897
    assert len(verdicts) == len({row["stable_case_id"] for row in verdicts})
    assert {row["stable_case_id"] for row in verdicts} == eligible_ids

    rows = []
    for record in verdicts:
        sealed = dict(record)
        result_sha256 = sealed.pop("result_sha256")
        assert sha256_bytes(canonical_bytes(sealed)) == result_sha256
        response_hashes = record["source_response_sha256"]
        assert response_hashes and all(len(value) == 64 for value in response_hashes)
        rows.append({
            "row_id": record["stable_case_id"],
            "response_sha256": response_hashes,
            "verifier_family": record["oracle"],
            "verifier_version": record["verifier_version"],
            "verdict": record["verdict"],
            "reason": _reason(record),
            "evidence_pointer": f"{VERDICTS.relative_to(ROOT)}#records/{record['stable_case_id']}",
            "immutable_verdict_sha256": result_sha256,
        })

    return {
        "artifact_type": "vapi_saved_response_scoring_verification",
        "version": "1.0.0",
        "ticket_refs": ["BEAAA-19389", "BEAAA-19322"],
        "contracts_referenced": [
            "are-scanner-api-v1.md@v1.3.0",
            "scanner-implementation-supervision-v1.md@v1.1",
        ],
        "classification": "rule-execution",
        "scanner_version": "0.5.0",
        "corpus_version": "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988",
        "probe_set_version": "1.4",
        "rubric_point_id": "multi-family/mapped-per-record",
        "source_registry_sha256": sha256_file(REGISTRY),
        "source_ledger_sha256": sha256_file(LEDGER),
        "source_verdicts_sha256": sha256_file(VERDICTS),
        "vapi_requests": 0,
        "counts": {
            "frozen_registry_rows": len(registry_rows),
            "eligible_saved_deterministic_rows": len(rows),
            "immutable_verdicts": len(rows),
            "verdicts": dict(sorted(Counter(row["verdict"] for row in rows).items())),
        },
        "rows": rows,
    }


def main() -> None:
    receipt = build_receipt()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(canonical_bytes(receipt))
    print(json.dumps({"output": str(OUTPUT), "sha256": sha256_file(OUTPUT), "counts": receipt["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
