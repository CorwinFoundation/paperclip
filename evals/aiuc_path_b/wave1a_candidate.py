"""Wave 1A reported-not-scored candidate packet — BEAAA-16945.

This module packages exactly two production-connected scenario families:
`cross_session_data_leak` and `consequential_action_confirmation`. It does not
change score math, bands, pillar weights, or API fields. Missing authorization,
pins, signatures, immutable-evidence proof, capability declarations, or
complete execution fails closed to `not_underwriting_active`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from evals.aiuc_path_b import base
from evals.aiuc_path_b import consequential_action_confirmation as cac
from evals.aiuc_path_b import cross_session_data_leak as csdl

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_COMMIT = "5c084879167956be6e31e507b1b94509ab586596"
CONTRACT = "are-scanner-api-v1@v1.0"
PROMOTION_STATE = "reported_not_scored"
UNDERWRITING_STATE = "not_underwriting_active"
SCORING_ACTIVE_PROMOTION_STATE = "scoring_active"
UNDERWRITING_ACTIVE_STATE = "underwriting_active"
UNDERWRITING_COSIGN_REF = "BEAAA-16971"
WAVE1A_FAMILIES = (csdl.SPEC.name, cac.SPEC.name)
DEFAULT_MODEL_VERSIONS: tuple[str, ...] = ()
AUTHORIZATION_SIGNATURE_ALGORITHM = "ed25519-wave1a-authorization-v0.2"
AUTHORIZED_SIGNER_KEY_ID = "wave1a-production-authority-2026-07"
AUTHORIZED_SIGNER_PUBLIC_KEY_B64URL = "U89QKWg5b1goQOswjgKFpUpg4119BpZtLNKouItZXWA"
REFERENCE_AUTHORIZATION_SIGNATURE = (
    "ed25519:wave1a-production-authority-2026-07:"
    "prld--NVoNfE4pwC_uf6Fm3BCD3hIeBDmYXS4zFxLxHgt3Cea6QFiNKv5otc9ELex1FdTAYFb4VOFZ45pMQFDg"
)
REFERENCE_AUTHORIZATION_ISSUED_AT = "2026-07-23T00:00:00Z"
REFERENCE_AUTHORIZATION_EXPIRES_AT = "2026-12-31T23:59:59Z"
SCORING_ACTIVE_RUNTIME_CONTRACT = "scoring_active_underwriting_eligibility_zero_weight"
AUTHORIZATION_VERIFY_AT = datetime(2026, 7, 23, tzinfo=UTC)


@dataclass(frozen=True)
class TargetAuthorization:
    target_id: str
    authorization_ref: str
    authorization_signature: str
    authorization_issued_at: str
    authorization_expires_at: str
    tenant_boundary: str
    session_boundary: str
    declared_tool_inventory: tuple[str, ...]
    use_case_scope: str
    allowed_family_ids: tuple[str, ...]
    corpus_sha256: dict[str, str]
    config_sha256: dict[str, str]
    evidence_immutable: bool = True
    capability_conflicts: tuple[str, ...] = ()


class CandidateValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    import base64

    return base64.urlsafe_b64decode(value + padding)


def _parse_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("timestamp_must_be_utc_z")
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _signature_preimage(manifest: dict[str, Any]) -> dict[str, Any]:
    preimage = dict(manifest)
    preimage.pop("candidate_commit_sha", None)
    preimage.pop("signed_manifest", None)
    return preimage


def _authorization_signature_preimage(target: TargetAuthorization) -> dict[str, Any]:
    return {
        "algorithm": AUTHORIZATION_SIGNATURE_ALGORITHM,
        "signer_key_id": AUTHORIZED_SIGNER_KEY_ID,
        "target_id": target.target_id,
        "authorization_ref": target.authorization_ref,
        "authorization_issued_at": target.authorization_issued_at,
        "authorization_expires_at": target.authorization_expires_at,
        "tenant_boundary": target.tenant_boundary,
        "session_boundary": target.session_boundary,
        "declared_tool_inventory": list(target.declared_tool_inventory),
        "use_case_scope": target.use_case_scope,
        "allowed_family_ids": list(target.allowed_family_ids),
        "corpus_sha256": target.corpus_sha256,
        "config_sha256": target.config_sha256,
        "evidence_immutable": target.evidence_immutable,
    }


def verify_authorization_signature(target: TargetAuthorization) -> bool:
    parts = target.authorization_signature.split(":")
    if len(parts) != 3:
        raise CandidateValidationError("authorization_signature_invalid")
    algorithm, key_id, signature_b64url = parts
    if algorithm != "ed25519":
        raise CandidateValidationError("authorization_signature_algorithm_unsupported")
    if key_id != AUTHORIZED_SIGNER_KEY_ID:
        raise CandidateValidationError("authorization_signer_unknown")
    try:
        issued_at = _parse_timestamp(target.authorization_issued_at)
        expires_at = _parse_timestamp(target.authorization_expires_at)
    except ValueError as exc:
        raise CandidateValidationError("authorization_timestamp_invalid") from exc
    if issued_at > AUTHORIZATION_VERIFY_AT:
        raise CandidateValidationError("authorization_not_yet_valid")
    if expires_at <= AUTHORIZATION_VERIFY_AT:
        raise CandidateValidationError("authorization_signature_stale")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _b64url_decode(AUTHORIZED_SIGNER_PUBLIC_KEY_B64URL)
        )
        public_key.verify(
            _b64url_decode(signature_b64url),
            _canonical(_authorization_signature_preimage(target)).encode("utf-8"),
        )
    except (InvalidSignature, ValueError) as exc:
        raise CandidateValidationError("authorization_signature_invalid") from exc
    return True


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_wave1a_hashes() -> dict[str, dict[str, str]]:
    return {
        "corpus_sha256": {
            csdl.SPEC.name: _sha256_file(REPO_ROOT / "rubric/probe_mappings/cross_session_data_leak.yaml"),
            cac.SPEC.name: _sha256_file(
                REPO_ROOT / "rubric/probe_mappings/consequential_action_confirmation.yaml"
            ),
        },
        "config_sha256": {
            csdl.SPEC.name: _sha256_file(REPO_ROOT / "rubric/aiuc_path_b/family_registry.yaml"),
            cac.SPEC.name: _sha256_file(REPO_ROOT / "rubric/aiuc_path_b/family_registry.yaml"),
        },
    }


def reference_authorization() -> TargetAuthorization:
    pins = expected_wave1a_hashes()
    unsigned = TargetAuthorization(
        target_id="authorized-wave1a-reference-target",
        authorization_ref="BEAAA-16979#out-of-band-reference-target",
        authorization_signature=REFERENCE_AUTHORIZATION_SIGNATURE,
        authorization_issued_at=REFERENCE_AUTHORIZATION_ISSUED_AT,
        authorization_expires_at=REFERENCE_AUTHORIZATION_EXPIRES_AT,
        tenant_boundary="tenant_id and sibling_tenant_marker isolated per session",
        session_boundary="fresh session_id per case; no retained prior-session data may be disclosed",
        declared_tool_inventory=(
            "retrieve_conversation_history",
            "lookup_ticket",
            "search_kb",
            "payments.transfer_commit",
            "email.send",
        ),
        use_case_scope="bounded support, CRM, and workflow-agent scenario execution",
        allowed_family_ids=WAVE1A_FAMILIES,
        corpus_sha256=pins["corpus_sha256"],
        config_sha256=pins["config_sha256"],
    )
    return unsigned


def _fail_closed(code: str, target: TargetAuthorization | None) -> dict[str, Any]:
    return {
        "promotion_state": PROMOTION_STATE,
        "underwriting_state": UNDERWRITING_STATE,
        "underwriting_active_count": 0,
        "fail_closed": True,
        "failure_code": code,
        "target_id": getattr(target, "target_id", None),
    }


def _validate_authorization(target: TargetAuthorization) -> None:
    required = {
        "target_id": target.target_id,
        "authorization_ref": target.authorization_ref,
        "authorization_signature": target.authorization_signature,
        "authorization_issued_at": target.authorization_issued_at,
        "authorization_expires_at": target.authorization_expires_at,
        "tenant_boundary": target.tenant_boundary,
        "session_boundary": target.session_boundary,
        "use_case_scope": target.use_case_scope,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise CandidateValidationError("authorization_missing:" + ",".join(sorted(missing)))
    if not target.declared_tool_inventory:
        raise CandidateValidationError("declared_tool_inventory_missing")
    if set(target.allowed_family_ids) != set(WAVE1A_FAMILIES):
        raise CandidateValidationError("family_scope_conflict")
    if target.capability_conflicts:
        raise CandidateValidationError("capability_conflict:" + ",".join(target.capability_conflicts))
    if not target.evidence_immutable:
        raise CandidateValidationError("mutable_evidence")
    expected = expected_wave1a_hashes()
    if target.corpus_sha256 != expected["corpus_sha256"]:
        raise CandidateValidationError("corpus_hash_mismatch")
    if target.config_sha256 != expected["config_sha256"]:
        raise CandidateValidationError("config_hash_mismatch")
    verify_authorization_signature(target)


def _execution_trace(family: str, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    traces = []
    for row in evidence["case_results"]:
        traces.append(
            {
                "family": family,
                "case_id": row["case_id"],
                "seed": evidence["seed"],
                "transcript_sha256": hashlib.sha256(
                    _canonical(
                        {
                            "prompt": row["rendered_prompt"],
                            "decision": row["decision"],
                            "verdict": row["verdict"],
                        }
                    ).encode("utf-8")
                ).hexdigest(),
                "tool_call_trace": row["decision"],
                "verdict": row["verdict"],
            }
        )
    return traces


def _assert_complete_and_invariant(results: dict[str, list[dict[str, Any]]]) -> None:
    for family, runs in results.items():
        if len(runs) != base.REPLICATION_K:
            raise CandidateValidationError(f"incomplete_runs:{family}")
        expected_cases = {row["case_id"] for row in runs[0]["case_results"]}
        verdicts_by_case: dict[str, set[str]] = {case_id: set() for case_id in expected_cases}
        for run in runs:
            case_ids = {row["case_id"] for row in run["case_results"]}
            if case_ids != expected_cases:
                raise CandidateValidationError(f"incomplete_case_set:{family}")
            for row in run["case_results"]:
                verdicts_by_case[row["case_id"]].add(row["verdict"])
        unstable = sorted(case_id for case_id, verdicts in verdicts_by_case.items() if len(verdicts) != 1)
        if unstable:
            raise CandidateValidationError(f"terminal_verdict_drift:{family}:{','.join(unstable)}")


def build_candidate_packet(
    *,
    target: TargetAuthorization,
    commit_sha: str,
    model_versions: tuple[str, ...] = DEFAULT_MODEL_VERSIONS,
    force_incomplete_family: str | None = None,
) -> dict[str, Any]:
    try:
        _validate_authorization(target)
        results: dict[str, list[dict[str, Any]]] = {
            csdl.SPEC.name: base.replicate(csdl.SPEC, csdl.compliant_policy),
            cac.SPEC.name: base.replicate(cac.SPEC, cac.compliant_policy),
        }
        if force_incomplete_family:
            results[force_incomplete_family] = results[force_incomplete_family][:-1]
        _assert_complete_and_invariant(results)
    except CandidateValidationError as exc:
        return _fail_closed(exc.code, target)

    unique_counts = {family: runs[0]["case_count"] for family, runs in results.items()}
    execution_counts = {
        family: sum(len(run["case_results"]) for run in runs)
        for family, runs in results.items()
    }
    traces_by_family = {
        family: [trace for run in runs for trace in _execution_trace(family, run)]
        for family, runs in results.items()
    }
    family_artifacts = {
        family: {
            "evidence_sha256_by_seed": {str(run["seed"]): run["sha256"] for run in runs},
            "fired_probe_ledger": runs[0]["fired_probe_ledger"],
            "canary_records": runs[0]["canary_registration"],
            "transcript_trace_sha256": hashlib.sha256(
                _canonical(traces_by_family[family]).encode("utf-8")
            ).hexdigest(),
        }
        for family, runs in results.items()
    }
    unsigned_manifest: dict[str, Any] = {
        "ticket_ref": "BEAAA-16945",
        "baseline_commit_sha": BASELINE_COMMIT,
        "candidate_commit_sha": commit_sha,
        "contract": CONTRACT,
        "scanner_version": base.SCANNER_VERSION,
        "model_versions": list(model_versions),
        "promotion_state": PROMOTION_STATE,
        "underwriting_state": UNDERWRITING_STATE,
        "target_authorization": {
            "target_id": target.target_id,
            "authorization_ref": target.authorization_ref,
            "authorization_signature": target.authorization_signature,
            "authorization_issued_at": target.authorization_issued_at,
            "authorization_expires_at": target.authorization_expires_at,
            "tenant_boundary": target.tenant_boundary,
            "session_boundary": target.session_boundary,
            "declared_tool_inventory": list(target.declared_tool_inventory),
            "use_case_scope": target.use_case_scope,
        },
        "counts": {
            "unique_scenario_count": sum(unique_counts.values()),
            "unique_scenario_count_by_family": unique_counts,
            "execution_count": sum(execution_counts.values()),
            "execution_count_by_family": execution_counts,
            "underwriting_active_count": 0,
            "underwriting_active_count_by_family": {family: 0 for family in WAVE1A_FAMILIES},
        },
        "pins": expected_wave1a_hashes(),
        "seeds": [0, 1, 2, 3, 4],
        "families": family_artifacts,
        "reproducibility_summary": {
            "terminal_verdict_class_invariant": True,
            "golden_n5_delta": {"total": 0, "pillars": 0},
        },
        "fail_closed": False,
    }
    manifest_hash = hashlib.sha256(_canonical(_signature_preimage(unsigned_manifest)).encode("utf-8")).hexdigest()
    unsigned_manifest["signed_manifest"] = {
        "algorithm": "sha256-canonical-json-self-signature",
        "preimage_excludes": ["candidate_commit_sha", "signed_manifest"],
        "sha256": manifest_hash,
        "signature": f"sha256:{manifest_hash}",
    }
    return unsigned_manifest


def verify_signed_manifest(packet: dict[str, Any]) -> bool:
    signed_manifest = packet.get("signed_manifest")
    if not isinstance(signed_manifest, dict):
        return False
    if signed_manifest.get("algorithm") != "sha256-canonical-json-self-signature":
        return False
    expected_hash = hashlib.sha256(_canonical(_signature_preimage(packet)).encode("utf-8")).hexdigest()
    return (
        signed_manifest.get("sha256") == expected_hash
        and signed_manifest.get("signature") == f"sha256:{expected_hash}"
    )


def fail_closed_for_signed_manifest(packet: dict[str, Any]) -> dict[str, Any]:
    if verify_signed_manifest(packet):
        return {
            "fail_closed": False,
            "failure_code": None,
            "verification": "signed_manifest_valid",
        }
    return {
        "promotion_state": PROMOTION_STATE,
        "underwriting_state": UNDERWRITING_STATE,
        "underwriting_active_count": 0,
        "fail_closed": True,
        "failure_code": "signed_manifest_verification_failed",
    }


def scoring_active_runtime_contract(
    family: str,
    raw_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scored_runs = []
    for run in raw_runs:
        scored = {
            **run,
            "scored": True,
            "scoring": "scoring_active",
            "expected_result_contract": SCORING_ACTIVE_RUNTIME_CONTRACT,
            "score_weight": 0.0,
            "score_contribution": 0,
            "underwriting_state": UNDERWRITING_ACTIVE_STATE,
            "scoring_boundary": {
                "source_substrate_scoring": run["scoring"],
                "activation_layer": "rubric/probe_mappings binding.scoring_active=true",
                "score_math_changed": False,
            },
        }
        scored["sha256"] = hashlib.sha256(_canonical(scored).encode("utf-8")).hexdigest()
        scored_runs.append(scored)
    return scored_runs


def build_scoring_active_packet(*, commit_sha: str) -> dict[str, Any]:
    """Return the BEAAA-16972 activation packet for the exact Wave 1A tranche.

    This is a registry-state activation over the immutable BEAAA-16945 evidence:
    the families become scoring-active / underwriting-active eligibility gates
    with zero BackBond Score, pillar, band, or pricing movement. It intentionally
    does not import or edit rubric scoring math.
    """

    packet = build_candidate_packet(target=reference_authorization(), commit_sha=commit_sha)
    if packet.get("fail_closed"):
        return packet
    counts = dict(packet["counts"])
    counts["underwriting_active_count"] = counts["unique_scenario_count"]
    counts["underwriting_active_count_by_family"] = dict(counts["unique_scenario_count_by_family"])
    activated = {
        **packet,
        "ticket_ref": "BEAAA-16972",
        "source_candidate_ref": "BEAAA-16945",
        "underwriting_cosign_ref": UNDERWRITING_COSIGN_REF,
        "promotion_state": SCORING_ACTIVE_PROMOTION_STATE,
        "underwriting_state": UNDERWRITING_ACTIVE_STATE,
        "counts": counts,
        "score_impact": {
            "are_total_delta": 0,
            "pillar_deltas": {
                "GOVERNANCE": 0,
                "ROBUSTNESS": 0,
                "BIAS_FAIRNESS": 0,
                "OPS": 0,
            },
            "band_delta": 0,
            "pricing_delta": 0,
        },
        "false_positive_budget": {
            "known_false_positives_at_activation": 0,
            "first_month_disputed_fp_tolerance_per_100_applicable_executions": 1,
            "canary_regression_tolerance": 0,
        },
        "peril_mapping": {
            csdl.SPEC.name: [
                "data_leakage",
                "privacy_exposure",
                "tenant_session_boundary_failure",
            ],
            cac.SPEC.name: [
                "unauthorized_consequential_action",
                "authority_control_failure",
                "agentic_action_error",
            ],
        },
        "rollback": {
            "procedure": (
                "Restore both family mappings to reporting_status:reported_not_scored, "
                "binding.scoring_active:false, and "
                "binding.underwriting_active:false; active counts return to the prior "
                "reported-not-scored state without touching other registry rows. "
                "The scoring field stays reported_not_scored before and after rollback."
            ),
            "tested_by": "tests/test_beaaa16972_wave1a_scored_activation.py",
        },
    }
    manifest_hash = hashlib.sha256(_canonical(_signature_preimage(activated)).encode("utf-8")).hexdigest()
    activated["signed_manifest"] = {
        "algorithm": "sha256-canonical-json-self-signature",
        "preimage_excludes": ["candidate_commit_sha", "signed_manifest"],
        "sha256": manifest_hash,
        "signature": f"sha256:{manifest_hash}",
    }
    return activated
