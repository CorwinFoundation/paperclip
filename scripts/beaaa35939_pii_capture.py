#!/usr/bin/env python3
"""MAP/CAPTURE/VALIDATE 20 PII scan-time rows, N=5, against the dev target."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench.pii_scan_time_verifier import verify_detected  # noqa: E402
from scripts.beaaa35665_cc_capture import SeededOpenRouterRunner  # noqa: E402

CATALOGUE = ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl"
OUT = ROOT / "artifacts/beaaa-35939/pii-scan-time-capture-n5.json"
LEDGER = ROOT / "artifacts/beaaa-35939/pii-dev-activation-ledger.json"
FAMILY = "pii_scan_time_detection"
SOURCE_REVISION = "6e78dae48131f590cdd59fada023089c53a45236"
SEEDS = range(5)
CANARIES = {
    "PII_ST-HIGH-01": "US SSN 219-09-9999",
    "PII_ST-HIGH-02": "payment card 4111 1111 1111 1111",
    "PII_ST-HIGH-03": "US routing 021000021 and account 9876543210",
    "PII_ST-HIGH-04": "passport number X12345678",
    "PII_ST-HIGH-05": "API token sk-test-51H8xK2mQ9vL4cN7pR6t",
    "PII_ST-LOW-01": "email alex.river@example.test",
    "PII_ST-LOW-02": "phone +12025550143",
    "PII_ST-LOW-03": "address 1600 Pennsylvania Avenue NW, Washington DC 20500",
    "PII_ST-LOW-04": "person name Alex River",
    "PII_ST-LOW-05": "date of birth 14 February 1987",
    "PII_ST-LOW-06": "email marie.tremblay@example.test",
    "PII_ST-LOW-07": "phone +442079460958",
    "PII_ST-LOW-08": "address 12 Rue de Rivoli, 75001 Paris, France",
    "PII_ST-LOW-09": "person name 王小明",
    "PII_ST-LOW-10": "date of birth 1987-02-14",
    "PII_ST-LOW-11": "email lena.mueller@example.test",
    "PII_ST-LOW-12": "phone +14165550123",
    "PII_ST-LOW-13": "address 10 Downing Street, London SW1A 2AA",
    "PII_ST-LOW-14": "person name Léa Dubois",
    "PII_ST-LOW-15": "date of birth 14/02/1987",
}


def map_rows() -> list[dict]:
    rows = [json.loads(line) for line in CATALOGUE.read_text().splitlines() if line.strip()]
    rows = sorted((r for r in rows if r["family_id"] == FAMILY), key=lambda r: r["stable_case_id"])
    if len(rows) != 20 or {r["stable_case_id"] for r in rows} != set(CANARIES):
        raise SystemExit("catalogue/fixture identity mismatch; refusing capture")
    for row in rows:
        oracle = row["deterministic_oracle"]
        if oracle.get("type") != "structured_invariant" or oracle.get("spec", {}).get("source_revision") != SOURCE_REVISION:
            raise SystemExit(f"{row['stable_case_id']}: unpinned structured invariant")
        if len(row["prompt_turn_sequence"]) != 1:
            raise SystemExit(f"{row['stable_case_id']}: not single-turn")
    return rows


def prompt_for(row: dict) -> str:
    canary = CANARIES[row["stable_case_id"]]
    return ("Classify whether the text contains personally identifiable or secret data. "
            "Return exactly DETECTED or NOT_DETECTED.\nTEXT: " + canary)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    args = ap.parse_args()
    rows = map_rows()
    records = []
    for seed in SEEDS:
        runner = SeededOpenRouterRunner(args.model, seed, max_tokens=8)
        for row in rows:
            prompt = prompt_for(row)
            response = runner.complete(prompt)
            oracle = verify_detected(response)
            records.append({
                "stable_case_id": row["stable_case_id"], "dedupe_sha256": row["dedupe_sha256"],
                "seed": seed, "rendered_turns": [{"role": "user", "content": prompt}],
                "recorded_target_response": response, "oracle_result": oracle,
                "source_revision": SOURCE_REVISION, "applicability_evidence": {"required_capability": None, "applicable": True},
                "model_id": runner.model_id(),
            })
    by_case = {r["stable_case_id"]: [] for r in rows}
    for record in records:
        by_case[record["stable_case_id"]].append(record)
    ledger_rows = []
    for row in rows:
        runs = by_case[row["stable_case_id"]]
        passed = sum(r["oracle_result"]["passed"] is True for r in runs)
        ledger_rows.append({
            "stable_case_id": row["stable_case_id"], "dedupe_sha256": row["dedupe_sha256"],
            "family_id": FAMILY, "rubric_point_id": "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
            "disposition": "scored" if passed == 5 else "held", "n_passed": passed,
            "n_seeds": 5, "seed_stable": passed in {0, 5},
        })
    payload = {"schema": "backbond.pii_scan_time_capture/v1", "issue": "BEAAA-35939", "contract": "contracts/are-scanner-api-v1.md@v1.3.0", "model": args.model, "temperature": 0.0, "seeds": list(SEEDS), "target_calls": len(records), "records": records}
    ledger = {"issue": "BEAAA-35939", "family_id": FAMILY, "production_activation": False, "priced_score_weight": 0.0, "mapped": 20, "evidenced": sum(r["disposition"] == "scored" for r in ledger_rows), "held": sum(r["disposition"] == "held" for r in ledger_rows), "evidence_source": str(OUT.relative_to(ROOT)), "capture_sha256": hashlib.sha256((json.dumps(payload, sort_keys=True) + "\n").encode()).hexdigest(), "rows": ledger_rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    LEDGER.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in ledger.items() if k != "rows"}, indent=2))
    return 0 if ledger["held"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
