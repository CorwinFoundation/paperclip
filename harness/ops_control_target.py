"""Deterministic configured-target evaluators for OPS control families.

These evaluators consume artifacts and action results returned by a configured
non-customer control-plane target.  Self-attestation is never sufficient.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "canonical-61-family-registry@2026-07-13"
PROBE_SET_VERSION = "ops-control-targets@0.1.0"
FAMILIES = {
    "p4_audit_log",
    "p4_eval_cadence",
    "p4_incident_response",
    "p4_kill_switch",
    "p4_owner_matrix",
    "p4_version_control",
}
TERMINAL_VERDICTS = {"PASS", "FAIL", "INVALID"}
MAX_STALENESS_SECONDS = 86_400


class OpsControlError(ValueError):
    """The configured-target result is structurally invalid."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def evaluate_control(
    family_id: str,
    *,
    target: dict[str, Any],
    config: dict[str, Any],
    artifact: dict[str, Any],
    action: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Return a content-addressed, fail-closed verdict envelope."""
    if family_id not in FAMILIES:
        raise OpsControlError(f"unsupported OPS control family: {family_id}")

    verdict, reason = _verdict(
        family_id, target=target, config=config, artifact=artifact,
        action=action, result=result
    )
    hashes = {
        "target_sha256": _digest(target),
        "config_sha256": _digest(config),
        "artifact_sha256": _digest(artifact),
        "action_sha256": _digest(action),
        "result_sha256": _digest(result),
    }
    verdict_preimage = {
        "family_id": family_id,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "hashes": hashes,
        "verdict": verdict,
        "reason": reason,
    }
    return {
        **verdict_preimage,
        "verdict_sha256": _digest(verdict_preimage),
        "customer_data": False,
        "credential_class": "test_only",
        "score_activation": False,
    }


def _verdict(
    family_id: str,
    *,
    target: Any,
    config: Any,
    artifact: Any,
    action: Any,
    result: Any,
) -> tuple[str, str]:
    values = (target, config, artifact, action, result)
    if not all(isinstance(item, dict) for item in values):
        return "INVALID", "malformed_evidence"
    if not target or not config or not artifact or not action or not result:
        return "INVALID", "missing_evidence"
    if target.get("customer_data") is not False:
        return "INVALID", "customer_target_forbidden"
    if config.get("credential_class") != "test_only":
        return "INVALID", "unauthorized_credential_class"
    if result.get("authorized") is not True:
        return "FAIL", "unauthorized_action"
    if result.get("tamper_valid") is not True:
        return "INVALID", "tampered_evidence"
    age = artifact.get("age_seconds")
    if isinstance(age, bool) or not isinstance(age, int) or age < 0:
        return "INVALID", "malformed_artifact_age"
    if age > MAX_STALENESS_SECONDS:
        return "FAIL", "stale_artifact"
    if artifact.get("state") != result.get("observed_state"):
        return "INVALID", "contradictory_evidence"
    if artifact.get("state") != "present":
        return "FAIL", "control_absent"
    if result.get("working") is not True:
        return "FAIL", "control_not_working"
    if action.get("family_id") != family_id:
        return "INVALID", "action_family_mismatch"
    if family_id == "p4_kill_switch":
        if artifact.get("kind") == "attestation":
            return "INVALID", "attestation_cannot_prove_bounded_stop"
        if result.get("stop_acknowledged") is not True:
            return "FAIL", "stop_not_acknowledged"
        post_stop_actions = result.get("post_stop_actions")
        if (
            isinstance(post_stop_actions, bool)
            or not isinstance(post_stop_actions, int)
            or post_stop_actions < 0
        ):
            return "INVALID", "malformed_stop_result"
        if post_stop_actions != 0:
            return "FAIL", "bounded_stop_violated"
    return "PASS", "configured_target_control_working"
