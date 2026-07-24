from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evals.aiuc_path_b import lane_a_depth as lane


ROOT1_SIGNATURE = (
    "ed25519:wave2-target-production-authority-r7-2026-07-root1:"
    "iJhEkAzG0SzUTjARD1ryHFk4BEwjV0fhYBVYs7v5Pquxqw2dWBHLQjxbzvLw-fX-kU7Gomvjysf93_lY3-qECg"
)
ROOT1_PUBLIC_KEY = "Dt0BaMLDj9DgU0FgHvcjZiZId0XCc9zhuB8norYUoOw"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _root2_test_authorization() -> lane.TargetProductionAuthorization:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    public_key_b64 = _b64url(public_key)
    fingerprint = "sha256:" + hashlib.sha256(public_key).hexdigest()
    unsigned = lane.TargetProductionAuthorization(
        target_id="wave2-authorized-production-reference-target",
        candidate_commit_sha="future-root2-child",
        parent_sha="7b2d156d039916b9e20acfe6404cdf0d68fa90ee",
        authorization_ref="TEST-ROOT2-EXACT-PREIMAGE-APPROVAL",
        authorization_issued_at="2026-07-24T00:00:00Z",
        authorization_expires_at="2026-12-31T23:59:59Z",
        target_endpoint=lane.OFFICIAL_TARGET_ENDPOINT,
        lease_root=str(lane.OFFICIAL_RUN_LEASE_ROOT),
        tenant_boundary="single authorized tenant; no cross-tenant context reuse",
        session_boundary="fresh target session per case and seed",
        allowed_family_ids=tuple(sorted(lane.SPECS)),
        evidence_immutable=True,
        signature="",
        key_id=lane.AUTHORIZATION_KEY_ID,
        public_key_b64url=public_key_b64,
        public_key_fingerprint=fingerprint,
        custody_receipt_sha256="sha256:" + ("ab" * 32),
    )
    signature = private_key.sign(lane._canonical(lane.authorization_preimage(unsigned)).encode("utf-8"))
    return replace(
        unsigned,
        signature=f"ed25519:{lane.AUTHORIZATION_KEY_ID}:{_b64url(signature)}",
    )


def test_r8_authorization_preimage_is_the_runtime_verifier_surface() -> None:
    auth = _root2_test_authorization()
    preimage = lane.authorization_preimage(auth)

    assert preimage["preimage_schema"] == "backbond-wave2-target-authorization-preimage@r8.0"
    assert preimage["key_id"] == "wave2-target-production-authority-r8-2026-07-root2"
    assert preimage["target_endpoint"] == lane.OFFICIAL_TARGET_ENDPOINT
    assert "/mnt/" not in preimage["target_endpoint"]
    assert preimage["runner"]["official_entrypoint"] == lane.OFFICIAL_TARGET_ENDPOINT
    assert preimage["lease"]["root"] == "/var/lib/are-scanner/evidence/wave2/single-run-leases/v0.1"
    assert preimage["authorization_scope"]["unique_scenario_count"] == 72
    assert preimage["authorization_scope"]["seeds"] == [0, 1, 2, 3, 4]
    assert preimage["authorization_scope"]["execution_count_k5"] == 360
    assert preimage["authorization_scope"]["maximum_calls"] == 360
    assert preimage["authorization_scope"]["retries"] == 0
    assert preimage["score_effect"]["total_delta"] == 0


def test_r8_signed_envelope_passes_real_runtime_verifier_without_call_or_lease() -> None:
    auth = _root2_test_authorization()

    lane.verify_authorization(
        auth,
        commit_sha="future-root2-child",
        parent_sha="7b2d156d039916b9e20acfe6404cdf0d68fa90ee",
    )


def test_root1_signed_receipt_is_rejected_before_call_1() -> None:
    auth = replace(
        _root2_test_authorization(),
        key_id="wave2-target-production-authority-r7-2026-07-root1",
        public_key_b64url=ROOT1_PUBLIC_KEY,
        public_key_fingerprint="sha256:45b1b136e040e1caf2778fffaf0a5754eecd2475de284ad32188757b9d16e3db",
        signature=ROOT1_SIGNATURE,
    )

    with pytest.raises(lane.TargetExecutionError, match="authorization_key_id_mismatch"):
        lane.verify_authorization(
            auth,
            commit_sha="future-root2-child",
            parent_sha="7b2d156d039916b9e20acfe6404cdf0d68fa90ee",
        )


def test_absolute_or_custom_target_endpoint_rejected_before_call_1() -> None:
    auth = replace(
        _root2_test_authorization(),
        target_endpoint="exec:///tmp/workspace-local/tools/wave2_openrouter_target.py",
    )

    with pytest.raises(lane.TargetExecutionError, match="custom_target_wrapper_forbidden"):
        lane.verify_authorization(
            auth,
            commit_sha="future-root2-child",
            parent_sha="7b2d156d039916b9e20acfe6404cdf0d68fa90ee",
        )


def test_official_endpoint_maps_to_checked_in_wrapper() -> None:
    auth = _root2_test_authorization()

    assert lane._authorized_target_command(auth) == lane.OFFICIAL_TARGET_WRAPPER.resolve()


def test_official_orchestrator_dry_verifies_receipt_without_lease_or_call(tmp_path) -> None:
    auth = _root2_test_authorization()
    proc = subprocess.run(
        [
            sys.executable,
            "tools/wave2_root2_official_run.py",
            "--candidate-sha",
            auth.candidate_commit_sha,
            "--parent-sha",
            auth.parent_sha,
            "--authorization-ref",
            auth.authorization_ref,
            "--issued-at",
            auth.authorization_issued_at,
            "--expires-at",
            auth.authorization_expires_at,
            "--public-key-b64url",
            auth.public_key_b64url,
            "--public-key-fingerprint",
            auth.public_key_fingerprint,
            "--custody-receipt-sha256",
            auth.custody_receipt_sha256,
            "--signature",
            auth.signature,
            "--out-dir",
            str(tmp_path),
            "--dry-verify-only",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "DRY_VERIFY_PASS_NO_LEASE_NO_CALLS"
    assert result["openrouter_calls_made"] == 0
    assert result["official_endpoint"] == lane.OFFICIAL_TARGET_ENDPOINT
    preimage = json.loads(Path(result["preimage_json"]).read_text(encoding="utf-8"))
    assert preimage == lane.authorization_preimage(auth)
