from __future__ import annotations

import base64
import json
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tools.c10_activation_manifest import (
    C10ActivationManifestError,
    PENDING_SIGNATURE_STATE,
    SCHEMA,
    SIGNED_SIGNATURE_STATE,
    canonical_payload_bytes,
    build_qa_manifest,
    validate_qa_candidate,
    verify_production_activation,
)
from tools import active_test_registry
from tools import validate_content_candidate_registration
from tools.active_test_registry import build_registry
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV

FAMILIES = {
    "ai_disclosure", "cbrn_refusal", "cyber_misuse_refusal",
    "harmful_output_golden", "insecure_code_generation",
    "out_of_scope_containment", "p3_5_multiturn_jailbreak",
    "p3_6_output_attack",
}


def _unsigned_manifest() -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "activation_issue": "BEAAA-26259",
        "signature_state": PENDING_SIGNATURE_STATE,
        "production_activation_allowed": False,
        "stable_case_ids": ["case-001"],
        "signature": None,
    }


def _signed_manifest(private_key: Ed25519PrivateKey) -> dict[str, object]:
    manifest = _unsigned_manifest()
    manifest["signature_state"] = SIGNED_SIGNATURE_STATE
    manifest["production_activation_allowed"] = True
    manifest["signature"] = {
        "algorithm": "ed25519",
        "key_id": "fixture-production-authority",
        "value": "",
    }
    signature = private_key.sign(canonical_payload_bytes(manifest))
    manifest["signature"]["value"] = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return manifest


def test_unsigned_candidate_is_qa_only_and_rejected_for_production() -> None:
    manifest = _unsigned_manifest()
    validate_qa_candidate(manifest)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with pytest.raises(C10ActivationManifestError, match="signature is missing"):
        verify_production_activation(
            manifest,
            authority_public_key=public,
            expected_key_id="fixture-production-authority",
        )


def test_exact_signed_payload_verifies_and_tampering_fails_closed() -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    manifest = _signed_manifest(key)
    verify_production_activation(
        manifest,
        authority_public_key=public,
        expected_key_id="fixture-production-authority",
    )
    manifest["stable_case_ids"] = ["case-002"]
    with pytest.raises(C10ActivationManifestError, match="signature is invalid"):
        verify_production_activation(
            manifest,
            authority_public_key=public,
            expected_key_id="fixture-production-authority",
        )


def test_manifest_cannot_substitute_its_own_key_identity() -> None:
    key = Ed25519PrivateKey.generate()
    manifest = _signed_manifest(key)
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with pytest.raises(C10ActivationManifestError, match="key identity is invalid"):
        verify_production_activation(
            manifest,
            authority_public_key=public,
            expected_key_id="pinned-real-authority",
        )


# 172 -> 180: BEAAA-33810 agent-escape incident tranche (+8, out_of_scope_containment).
# E018 adds twelve report-only disclosure cases outside the promoted tranche.
def test_exact_c10_tranche_and_e018_rows_follow_founder_dev_mode_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    bypass_ids = {f"AD-BYP-{i:02d}" for i in range(1, 13)}

    def is_bypass(row) -> bool:
        return row["test_id"] == "ai_disclosure" and row["case_id"] in bypass_ids

    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    off_rows = [
        row for row in build_registry(include_content_candidates=False)
        if row["test_id"] in FAMILIES
    ]
    assert len(off_rows) == 192
    assert len({row["case_id"] for row in off_rows}) == 192
    off_bypass = [row for row in off_rows if is_bypass(row)]
    off_tranche = [row for row in off_rows if not is_bypass(row)]
    assert len(off_bypass) == 12
    assert {row["case_id"] for row in off_bypass} == bypass_ids
    assert len(off_tranche) == 180
    assert {row["scoring_active"] for row in off_rows} == {False}
    assert {row["reporting_status"] for row in off_rows} == {"reported_not_scored"}

    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    on_rows = [
        row for row in build_registry(include_content_candidates=False)
        if row["test_id"] in FAMILIES
    ]
    assert len(on_rows) == 192
    assert {row["case_id"] for row in on_rows} == {row["case_id"] for row in off_rows}
    on_bypass = [row for row in on_rows if is_bypass(row)]
    on_tranche = [row for row in on_rows if not is_bypass(row)]
    assert len(on_tranche) == 180
    assert {row["case_id"] for row in on_tranche} == {row["case_id"] for row in off_tranche}
    assert {row["scoring_active"] for row in on_tranche} == {True}
    assert {row["reporting_status"] for row in on_tranche} == {"scoring_active"}
    assert len(on_bypass) == 12
    assert {row["case_id"] for row in on_bypass} == bypass_ids
    assert {row["scoring_active"] for row in on_bypass} == {False}
    assert {row["reporting_status"] for row in on_bypass} == {"reported_not_scored"}
    assert {row["attestation_tier"] for row in on_rows} == {"founder_dev_mode"}


def test_qa_manifest_binds_exact_immutable_inputs() -> None:
    manifest = build_qa_manifest(
        roster_path="artifacts/BEAAA-25646/c10-roster-v1.0.json",
        ledger_path="artifacts/BEAAA-25646/c10-mapping-ledger-v1.0.jsonl",
    )
    validate_qa_candidate(manifest)
    assert len(manifest["stable_case_ids"]) == 172
    assert manifest["immutable_inputs"]["roster_sha256"] == "e0046d6698aaa2ae44418a8d2bdf91098e87d19f31500a2d86500adde7402976"
    assert manifest["immutable_inputs"]["mapping_ledger_sha256"] == "c0be24afea3ed424be9d9bddad96815dcf17ff3e58ef5f5f238839ff9af35184"


@pytest.mark.parametrize(
    "entrypoint, argv",
    [
        (active_test_registry.main, ["active_test_registry", "--check"]),
        (validate_content_candidate_registration.main, ["validate_content_candidate_registration"]),
    ],
)
def test_pending_qa_boundary_is_structured_gate_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entrypoint,
    argv: list[str],
) -> None:
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    monkeypatch.setattr(sys, "argv", argv)
    assert entrypoint() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "FAIL"
    assert output["disposition"] == "production_boundary_refused"
    assert "production attestation" in output["error"]
