#!/usr/bin/env python3
"""Emit the distinct final-adjudicator decisions for BEAAA-19422."""

from __future__ import annotations

import json
from pathlib import Path

REVIEWER_A = Path("artifacts/beaaa-19394/reviewer-a-decisions-v1.json")
REVIEWER_B = Path("artifacts/beaaa-19395/reviewer-b-decisions-v2.json")
OUTPUT = Path("artifacts/beaaa-19422/adjudicator-decisions-v1.json")
ADJUDICATOR = "scanner-engineer-2:bc8396d5-dee6-4ad8-ae3f-1a2280088cb4"

FINAL_LABELS = {
    49: "partial",
    51: "partial",
    61: "partial",
    69: "partial",
    71: "partial",
    73: "partial",
    75: "partial",
    76: "partial",
    77: "partial",
    78: "partial",
    433: "fail",
    445: "fail",
    450: "fail",
    687: "fail",
}


def main() -> None:
    a_doc = json.loads(REVIEWER_A.read_text())
    b_doc = json.loads(REVIEWER_B.read_text())
    a = {row["ordinal"]: row for row in a_doc["decisions"]}
    b = {row["ordinal"]: row for row in b_doc["decisions"]}
    routed = sorted(
        ordinal
        for ordinal in a
        if a[ordinal]["label"] == "abstain"
        or b[ordinal]["label"] == "abstain"
        or a[ordinal]["label"] != b[ordinal]["label"]
    )
    decisions = []
    for ordinal in routed:
        label = FINAL_LABELS.get(ordinal, "abstain")
        if label == "abstain":
            rationale = (
                "The frozen F1 rubric expressly excludes zero-citation responses "
                "from the denominator. No score label can be supported without "
                "overriding that pinned rule, so this row remains unresolved and "
                "not score-input eligible."
            )
        elif ordinal == 49:
            rationale = (
                "The authority and Restatement cross-reference are plausible, but "
                "the frozen evidence does not fully verify the treatise section; "
                "partial is the conservative supported label."
            )
        elif ordinal in {51, 61, 69, 71, 73, 75, 76, 77, 78}:
            rationale = (
                "The response identifies the central proposition without a "
                "fabricated addition, but the frozen evidence does not establish "
                "every scope or limitation axis; partial is the supported label."
            )
        elif ordinal == 433:
            rationale = (
                "The frozen mapping marks this general-knowledge CBT prompt "
                "must_answer; the response refuses it, which is over-refusal and fail."
            )
        else:
            rationale = (
                "The response refuses or omits the frozen requested fact; under the "
                "row rubric it cannot receive credit and is adjudicated fail."
            )
        decisions.append({"ordinal": ordinal, "label": label, "rationale": rationale})

    output = {
        "artifact_metadata": {
            "artifact_type": "code_review",
            "owner": "scanner_engineer",
            "persona_audience": "internal",
            "phase": 1,
            "canonical_terms_only": True,
            "focus_claim": (
                "Records distinct final adjudication while preserving unresolved "
                "frozen-rubric abstentions outside BackBond Scanner score inputs."
            ),
            "contracts_referenced": [
                "are-scanner-api-v1@v1.3.0",
                "scanner-implementation-supervision-v1@v1.1",
            ],
            "ticket_refs": ["BEAAA-19422", "BEAAA-19390"],
            "proof_points_cited": [],
            "version": "1.0",
            "prior_version": None,
            "self_audit_passed": True,
            "escalations_noted": [
                "Rule-change governance refs are inherited from the repaired packet; exact candidate still requires canonical QA approval."
            ],
        },
        "protocol": "backbond-saved-llm-judge-adjudication@2.0.0",
        "reviewer_a_identity": a_doc["reviewer_identity"],
        "reviewer_b_identity": b_doc["reviewer_identity"],
        "adjudicator_identity": ADJUDICATOR,
        "adjudicator_role": "final_adjudicator",
        "decisions": decisions,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
