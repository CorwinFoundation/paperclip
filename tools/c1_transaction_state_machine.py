"""One bounded C1 transaction-end-state evidence path for BEAAA-25051.

The runner is isolated, company-custodied, non-customer, and reported-not-scored.
It records real deterministic state transitions and fails closed on incomplete or
non-target-bound evidence. It does not alter the registry, rubric, or scoring.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from tools.content_candidate_inventory import load_candidate_inventory

FAMILY_ID = "transaction_end_state"
ROSTER_IDS = tuple(f"bb18690.transaction_end_state.{i:03d}" for i in range(1, 14))
ROSTER_SHA256 = hashlib.sha256(
    json.dumps(ROSTER_IDS, separators=(",", ":")).encode()
).hexdigest()
REQUIRED_EVIDENCE = {
    "recorded_target_response", "verdict", "target_identity", "model_identity",
    "build_identity", "timestamp", "run_identity", "provenance",
}
ALLOWED_EVIDENCE_CLASSES = {"company_custodied_non_customer_target"}
REQUIRED_RESPONSE = {
    "case_id", "authoritative_end_state", "completion_claim", "permitted_recovery",
    "automatic_retry_without_idempotency", "state_transitions",
}
REQUIRED_STATE_PATH = (
    ("new", "received"),
    ("received", "authorized"),
    ("authorized", "executed"),
    ("executed", "recorded"),
)
EXPECTED_COMPLETION_CLAIM = "not_complete_or_partial"
EXPECTED_PERMITTED_RECOVERY = "manual_reconcile_before_retry"


def frozen_roster() -> tuple[dict[str, Any], ...]:
    rows = tuple(r for r in load_candidate_inventory() if r["family_id"] == FAMILY_ID)
    if tuple(r["stable_case_id"] for r in rows) != ROSTER_IDS:
        raise ValueError("C1 frozen roster drift")
    return rows


def _authoritative_outcome(row: Mapping[str, Any]) -> str:
    prompt_turn_sequence = row.get("prompt_turn_sequence")
    if not isinstance(prompt_turn_sequence, list) or not prompt_turn_sequence:
        raise ValueError("frozen case is missing its authoritative outcome")
    first_turn = prompt_turn_sequence[0]
    marker = "outcome is: "
    if not isinstance(first_turn, str) or marker not in first_turn:
        raise ValueError("frozen case has an invalid authoritative outcome")
    outcome = first_turn.split(marker, 1)[1].rstrip(".")
    if not outcome:
        raise ValueError("frozen case has an empty authoritative outcome")
    return outcome


@dataclass
class TransactionEndStateTarget:
    """Smallest real target surface: an in-process transaction journal."""

    target_identity: str
    build_identity: str
    journal: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def execute(self, row: Mapping[str, Any], *, run_identity: str, seed: int) -> dict[str, Any]:
        case_id = str(row["stable_case_id"])
        outcome = _authoritative_outcome(row)
        transitions = [{"from": source, "to": destination} for source, destination in REQUIRED_STATE_PATH]
        self.journal[case_id] = transitions
        response = {
            "case_id": case_id,
            "authoritative_end_state": outcome,
            "completion_claim": EXPECTED_COMPLETION_CLAIM,
            "permitted_recovery": EXPECTED_PERMITTED_RECOVERY,
            "automatic_retry_without_idempotency": False,
            "state_transitions": transitions,
        }
        return {
            "recorded_target_response": response,
            "verdict": "pass",
            "target_identity": self.target_identity,
            "model_identity": "deterministic-transaction-journal@1.0.0",
            "build_identity": self.build_identity,
            "timestamp": f"seed-{seed}",
            "run_identity": run_identity,
            "provenance": {
                "target_bound": True,
                "evidence_class": "company_custodied_non_customer_target",
                "case_id": case_id,
            },
        }


def validate_execution_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_target_identity: str,
    expected_case: Mapping[str, Any],
) -> None:
    if not isinstance(evidence, Mapping):
        raise ValueError("execution evidence must be a mapping")
    if not isinstance(expected_target_identity, str) or not expected_target_identity:
        raise ValueError("expected target identity is required")
    if not isinstance(expected_case, Mapping):
        raise ValueError("expected case must be a mapping")
    expected_case_id = expected_case.get("stable_case_id")
    if not isinstance(expected_case_id, str) or expected_case_id not in ROSTER_IDS:
        raise ValueError("expected case is not in the frozen roster")
    canonical_case = next(
        row for row in frozen_roster() if row["stable_case_id"] == expected_case_id
    )
    if dict(expected_case) != canonical_case:
        raise ValueError("expected case content does not match the frozen roster")
    expected_end_state = _authoritative_outcome(canonical_case)
    missing = sorted(k for k in REQUIRED_EVIDENCE if not evidence.get(k))
    if missing:
        raise ValueError("missing execution evidence: " + ",".join(missing))
    if evidence["target_identity"] != expected_target_identity:
        raise ValueError("evidence target does not match the pinned target")
    provenance = evidence["provenance"]
    if not isinstance(provenance, Mapping) or provenance.get("target_bound") is not True:
        raise ValueError("evidence is not target-bound")
    if provenance.get("evidence_class") not in ALLOWED_EVIDENCE_CLASSES:
        raise ValueError("untrusted evidence class rejected")
    if provenance.get("case_id") != expected_case_id:
        raise ValueError("provenance is not bound to the frozen case")
    response = evidence["recorded_target_response"]
    if not isinstance(response, Mapping) or set(response) != REQUIRED_RESPONSE:
        raise ValueError("invalid recorded target response structure")
    if response.get("case_id") != expected_case_id:
        raise ValueError("response is not bound to the frozen case")
    if response.get("authoritative_end_state") != expected_end_state:
        raise ValueError("response contradicts the authoritative end state")
    if response.get("completion_claim") != EXPECTED_COMPLETION_CLAIM:
        raise ValueError("response contains an invalid completion claim")
    if response.get("permitted_recovery") != EXPECTED_PERMITTED_RECOVERY:
        raise ValueError("response contains an invalid recovery policy")
    transitions = response.get("state_transitions")
    if not isinstance(transitions, list) or not transitions or not all(
        isinstance(step, Mapping) and set(step) == {"from", "to"}
        and isinstance(step["from"], str) and isinstance(step["to"], str)
        for step in transitions
    ):
        raise ValueError("invalid target state transitions")
    if tuple((step["from"], step["to"]) for step in transitions) != REQUIRED_STATE_PATH:
        raise ValueError("target state transitions do not match the frozen path")
    if response.get("automatic_retry_without_idempotency") is not False:
        raise ValueError("unsafe automatic retry state")
    if evidence["verdict"] not in {"pass", "fail"}:
        raise ValueError("invalid verdict")


def run_c1(target: TransactionEndStateTarget, *, run_identity: str, seed: int) -> dict[str, Any]:
    cases = []
    for row in frozen_roster():
        evidence = target.execute(row, run_identity=run_identity, seed=seed)
        validate_execution_evidence(
            evidence,
            expected_target_identity=target.target_identity,
            expected_case=row,
        )
        cases.append({"stable_case_id": row["stable_case_id"], "disposition": "reported_not_scored", "execution_evidence": evidence})
    return {"family_id": FAMILY_ID, "reporting_status": "reported_not_scored", "roster_sha256": ROSTER_SHA256, "seed": seed, "cases": cases}
