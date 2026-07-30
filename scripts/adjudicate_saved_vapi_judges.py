#!/usr/bin/env python3
"""Build or adjudicate the frozen BEAAA-19390 saved-response cohort.

This tool never calls Vapi or a model. It hashes the saved response evidence,
emits the review packet, and only produces adjudicated results when two
independent reviews (plus an adjudicator for disagreements) are complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

LABELS = {"pass", "partial", "fail", "abstain"}
SCORES = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
PROTOCOL = "backbond-saved-llm-judge-adjudication@2.0.0"


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def saved_rows(evidence: dict[str, Any], scoreability: dict[str, Any]) -> list[dict[str, Any]]:
    blocked = {
        row["ordinal"]: row
        for row in scoreability["rows"]
        if "judge_seed_unenforceable:anthropic_messages_api_has_no_seed_parameter"
        in row.get("blockers", [])
    }
    evidence_by_ordinal = {row["ordinal"]: row for row in evidence["records"]}
    if len(blocked) != 295:
        raise ValueError(f"expected 295 legacy judge rows, found {len(blocked)}")

    output = []
    for ordinal in sorted(blocked):
        row = evidence_by_ordinal[ordinal]
        responses = [item for item in row.get("responses", []) if item.get("response_text")]
        if not responses:
            output.append(
                {
                    "ordinal": ordinal,
                    "stable_case_id": row["stable_case_id"],
                    "family_id": row["family_id"],
                    "supportable": False,
                    "uncovered_reason": row.get("exclusion_reason", "saved_response_unavailable"),
                    "canonical_deterministic_result": None,
                    "canonical_score_input_eligible": False,
                }
            )
            continue
        response_hashes = []
        for response in responses:
            text_sha = hashlib.sha256(response["response_text"].encode()).hexdigest()
            if text_sha != response["response_sha256"]:
                raise ValueError(f"ordinal {ordinal}: saved response hash mismatch")
            response_hashes.append(response["response_sha256"])
        output.append(
            {
                "ordinal": ordinal,
                "stable_case_id": row["stable_case_id"],
                "family_id": row["family_id"],
                "source_row_sha256": row["source_row_sha256"],
                "supportable": True,
                "response_sha256": canonical_sha(response_hashes),
                "response_turns": [
                    {
                        "request_index": response.get("request_index"),
                        "turn": response.get("turn"),
                        "response_sha256": response["response_sha256"],
                        "response_text": response["response_text"],
                    }
                    for response in responses
                ],
                "rubric": row["judge_spec"],
                "prompt_action_reference": row["prompt_action_reference"],
                "canonical_deterministic_result": None,
                "canonical_score_input_eligible": False,
            }
        )
    return output


def load_reviews(path: Path | None, role: str) -> tuple[str, dict[int, dict[str, Any]]]:
    if path is None:
        return "", {}
    doc = load_json(path)
    identity = doc.get("reviewer_identity", "").strip()
    if not identity:
        raise ValueError(f"{role}: reviewer_identity is required")
    decisions: dict[int, dict[str, Any]] = {}
    for item in doc.get("decisions", []):
        label = item.get("label")
        if label not in LABELS:
            raise ValueError(f"{role} ordinal {item.get('ordinal')}: invalid label {label!r}")
        if not item.get("rationale", "").strip():
            raise ValueError(f"{role} ordinal {item.get('ordinal')}: rationale is required")
        decisions[item["ordinal"]] = item
    return identity, decisions


def kappa(left: list[str], right: list[str]) -> float:
    if not left:
        return 0.0
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    lc, rc = Counter(left), Counter(right)
    expected = sum((lc[x] / len(left)) * (rc[x] / len(right)) for x in LABELS)
    return 1.0 if expected == 1.0 else (observed - expected) / (1.0 - expected)


def adjudicate(
    rows: list[dict[str, Any]],
    reviewer_a: tuple[str, dict[int, dict[str, Any]]],
    reviewer_b: tuple[str, dict[int, dict[str, Any]]],
    adjudicator: tuple[str, dict[int, dict[str, Any]]],
) -> dict[str, Any]:
    aid, a, bid, b, cid, c = reviewer_a[0], reviewer_a[1], reviewer_b[0], reviewer_b[1], adjudicator[0], adjudicator[1]
    if aid and aid == bid:
        raise ValueError("reviewer identities must be independent")
    results, labels_a, labels_b = [], [], []
    for row in rows:
        if not row.get("supportable"):
            continue
        ordinal = row["ordinal"]
        if ordinal not in a or ordinal not in b:
            continue
        la, lb = a[ordinal]["label"], b[ordinal]["label"]
        labels_a.append(la)
        labels_b.append(lb)
        if la == lb and la != "abstain":
            final, method, confidence = la, "reviewer_consensus", 1.0
        else:
            if ordinal not in c or c[ordinal]["label"] == "abstain":
                continue
            final, method, confidence = c[ordinal]["label"], "adjudicator", 2 / 3
        results.append(
            {
                "ordinal": ordinal,
                "stable_case_id": row["stable_case_id"],
                "family_id": row["family_id"],
                "source_response_sha256": row["response_sha256"],
                "reviewer_a": {"identity": aid, **a[ordinal]},
                "reviewer_b": {"identity": bid, **b[ordinal]},
                "adjudicator": ({"identity": cid, **c[ordinal]} if method == "adjudicator" else None),
                "consensus_label": final,
                "consensus_score": SCORES[final],
                "consensus_confidence": confidence,
                "consensus_method": method,
                "canonical_deterministic_result_unchanged": True,
                "adjudicated_score_input_eligible": True,
            }
        )
    agreement = sum(a == b for a, b in zip(labels_a, labels_b)) / len(labels_a) if labels_a else 0.0
    return {
        "schema": "backbond.saved_judge_adjudication_results.v2",
        "protocol": PROTOCOL,
        "results": results,
        "statistics": {
            "legacy_rows": len(rows),
            "double_reviewed_rows": len(labels_a),
            "adjudicated_rows": len(results),
            "supportable_rows": sum(bool(row.get("supportable")) for row in rows),
            "uncovered_rows": sum(not row.get("supportable") for row in rows),
            "unresolved_rows": sum(bool(row.get("supportable")) for row in rows) - len(results),
            "raw_reviewer_agreement": agreement,
            "cohen_kappa": kappa(labels_a, labels_b),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--scoreability", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path)
    parser.add_argument("--reviewer-b", type=Path)
    parser.add_argument("--adjudicator", type=Path)
    args = parser.parse_args()

    evidence, scoreability = load_json(args.evidence), load_json(args.scoreability)
    rows = saved_rows(evidence, scoreability)
    result = adjudicate(
        rows,
        load_reviews(args.reviewer_a, "reviewer_a"),
        load_reviews(args.reviewer_b, "reviewer_b"),
        load_reviews(args.adjudicator, "adjudicator"),
    )
    result["metadata"] = {
        "issue": "BEAAA-19390",
        "classification": "rule-change",
        "contract": "contracts/are-scanner-api-v1.md@v1.3.0",
        "scanner_version": "0.5.0",
        "corpus_version": "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988",
        "probe_set_version": "1.4",
        "rubric_point_id": sorted({row["family_id"] for row in rows}),
        "temperature": 0,
        "seed": None,
        "seed_note": "No seed is claimed; adjudication is human-review protocol v2.",
        "source_evidence_sha256": canonical_sha(evidence),
        "source_scoreability_sha256": canonical_sha(scoreability),
        "input_manifest_sha256": canonical_sha(rows),
        "canonical_deterministic_results_unchanged": True,
        "publication_namespace": "adjudicated_saved_llm_judge_v2",
        "cto_cosign_ref": None,
        "underwriter_approval_ref": None,
    }
    result["review_packet"] = rows
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
