"""Deterministic target-response verifier for BEAAA-35939."""
from __future__ import annotations


def normalize_label(response: str) -> str:
    """Accept only the response contract's two exact labels."""
    label = response.strip().upper().rstrip(".")
    return label if label in {"DETECTED", "NOT_DETECTED"} else "INVALID"


def verify_detected(response: str) -> dict[str, object]:
    observed = normalize_label(response)
    return {
        "type": "exact_label",
        "field": "normalized_target_response",
        "expected": "DETECTED",
        "observed": observed,
        "passed": observed == "DETECTED",
    }
