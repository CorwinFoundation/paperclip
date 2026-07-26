"""Freeze and validate the BEAAA-18004 Wave 3 first-100 expansion.

This module is zero-call safe.  Selection uses only inventory metadata and the
explicit gap-family allowlist established by the Wave 0 reconciliation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "artifacts/beaaa-15688/wave0-case-inventory.jsonl"
SELECTION = ROOT / "artifacts/beaaa-18004/wave3-first100.jsonl"
MANIFEST = ROOT / "artifacts/beaaa-18004/wave3-execution-config.json"

# These families were inventory gaps, not members or aliases of the 180
# reconciled executable behaviors. Stable ordering then defines "next".
GAP_FAMILIES = frozenset(
    {
        "p2_1_demographic_parity_gap",
        "p2_2_equal_opportunity_gap",
        "p2_3_counterfactual_stability",
        "p2_4_eval_representation",
        "p2_5_sentiment_skew_by_attribute",
        "p2_6_mitigation_evidence",
        "peril_data_containment",
        "peril_recipient_integrity",
        "peril_tool_grounded_factuality",
        "stop_instruction_v1",
        "tool_misuse_param_tamper",
        "tool_misuse_scope_escape",
    }
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def select(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row["family_id"] in GAP_FAMILIES]
    return sorted(candidates, key=lambda row: row["stable_case_id"])[:100]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    expected = select(load_jsonl(INVENTORY))
    ids = [row["stable_case_id"] for row in rows]
    hashes = [row["dedupe_sha256"] for row in rows]
    assert rows == expected
    assert len(rows) == len(set(ids)) == len(set(hashes)) == 100
    assert all(row["family_id"] in GAP_FAMILIES for row in rows)
    assert manifest["selection"]["count"] == 100
    assert manifest["selection"]["selection_sha256"] == sha256(SELECTION)
    assert manifest["target"] == {"adapter": "syllable:apichat@authenticated-v1", "mock": False}
    assert manifest["judge"] == {
        "model": "anthropic:claude-haiku-4-5-20251001",
        "temperature": 0,
        "seed": 42,
        "rubric_version": "v1.3.0",
        "fallback_only": True,
    }
    assert manifest["budget"] == {
        "max_target_calls": 136,
        "max_judge_calls": 100,
        "max_estimated_judge_cost_usd": 100,
        "max_wall_clock_seconds": 28800,
    }
    retention = manifest["evidence_retention"]
    assert retention["mode"] == "append_only"
    assert retention["retention_years"] == 7
    assert retention["pii_scrub_before_write"] is True
    assert set(retention["excluded_fields"]) == {"proprietary_prompt", "customer_weights"}
    return {"count": 100, "unique_stable_ids": 100, "duplicate_hashes": 0, "aliases": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("materialize", "verify"))
    args = parser.parse_args()
    if args.command == "materialize":
        rows = select(load_jsonl(INVENTORY))
        SELECTION.parent.mkdir(parents=True, exist_ok=True)
        SELECTION.write_text(
            "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps({"count": len(rows), "selection_sha256": sha256(SELECTION)}, sort_keys=True))
        return
    print(json.dumps(validate(load_jsonl(SELECTION), json.loads(MANIFEST.read_text(encoding="utf-8"))), sort_keys=True))


if __name__ == "__main__":
    main()
