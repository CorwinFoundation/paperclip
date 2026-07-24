from __future__ import annotations

import subprocess

from evals.aiuc_path_b import base
from evals.aiuc_path_b import lane_a_depth as lane
from tools import beaaa17663_wave2_root1_r7_preimage as r7


def test_r7_public_anchor_is_fresh_public_only() -> None:
    anchor = r7.public_anchor()

    assert anchor["key_id"] == "wave2-target-production-authority-r7-2026-07-root1"
    assert anchor["public_key_fingerprint"] == lane.AUTHORIZATION_PUBLIC_KEY_FINGERPRINT
    assert anchor["public_key_b64url"] == "Dt0BaMLDj9DgU0FgHvcjZiZId0XCc9zhuB8norYUoOw"
    assert (
        anchor["custody_receipt_sha256"]
        == "sha256:db8a1a79267bf0ca2a5e1ca350f4ba35820c1a0207969cdbdb677832ed356be8"
    )
    assert anchor["supersedes_rejected_material"]["r6_key_id"] != anchor["key_id"]
    assert (
        anchor["supersedes_rejected_material"]["engineering_r7_key_id"]
        == "wave2-target-production-authority-r7-2026-07"
    )
    assert anchor["private_key_material"] == (
        "not_created_not_received_not_stored_not_used_by_scanner_engineering"
    )


def test_exact_unsigned_preimage_binds_required_wave2_scope() -> None:
    preimage = r7.target_authorization_preimage()

    assert preimage["source_candidate_sha"] == r7.SOURCE_CANDIDATE_SHA
    assert preimage["source_candidate_parent_sha"] == r7.SOURCE_CANDIDATE_PARENT_SHA
    assert preimage["required_anchor_child_parent_sha"] == r7.SOURCE_CANDIDATE_SHA
    assert preimage["key_id"] == lane.AUTHORIZATION_KEY_ID
    assert preimage["public_key_fingerprint"] == lane.AUTHORIZATION_PUBLIC_KEY_FINGERPRINT
    assert (
        preimage["custody_receipt_sha256"]
        == "sha256:db8a1a79267bf0ca2a5e1ca350f4ba35820c1a0207969cdbdb677832ed356be8"
    )
    assert preimage["root_only_custody_state"]["private_key_used"] is False
    assert preimage["target_endpoint"].endswith("/tools/wave2_openrouter_target.py")
    assert preimage["target_model"] == "openrouter:meta-llama/llama-3.3-70b-instruct"
    assert preimage["lease"]["root"] == "/var/lib/are-scanner/evidence/wave2/single-run-leases/v0.1"
    assert preimage["lease"]["schema_version"] == lane.RUN_LEASE_FORMAT_VERSION
    assert preimage["authorization_scope"]["unique_scenario_count"] == 72
    assert preimage["authorization_scope"]["seeds"] == [0, 1, 2, 3, 4]
    assert preimage["authorization_scope"]["execution_count_k5"] == 360
    assert preimage["authorization_scope"]["maximum_calls"] == 360
    assert preimage["authorization_scope"]["retries"] == 0
    assert preimage["retention"]["minimum_retention"] == "policy term + 7 years"
    assert preimage["score_effect"] == {
        "total_delta": 0,
        "pillar_deltas": {
            "GOVERNANCE": 0,
            "ROBUSTNESS": 0,
            "BIAS_FAIRNESS": 0,
            "OPS": 0,
        },
        "band_delta": 0,
        "pricing_delta": 0,
        "false_positive_movement": 0,
    }


def test_r6_and_alternate_residue_rejections_make_zero_openrouter_calls() -> None:
    legacy_packets = [
        {
            "key_id": "wave2-target-production-authority-2026-07",
            "public_key_fingerprint": "sha256:6fe266305ed59701d1b3fec85bd13a4a03402e971a214976c4b0701577081916",
        },
        {
            "key_id": lane.AUTHORIZATION_KEY_ID,
            "signature": "ed25519:wave2-target-production-authority-2026-07:legacy",
        },
        {
            "key_id": lane.AUTHORIZATION_KEY_ID,
            "approval_ref": "BEAAA-17118",
        },
        {
            "key_id": lane.AUTHORIZATION_KEY_ID,
            "partial_packet": True,
        },
        {
            "key_id": lane.AUTHORIZATION_KEY_ID,
            "lease_root": "/tmp/workspace-local-bypass",
        },
        {
            "key_id": "wave2-target-production-authority-r7-2026-07",
            "public_key_fingerprint": "sha256:66b218023b99c0e00aea594b57f9711e68dbebbc7619c930a7890b2984c36669",
        },
        {
            "key_id": lane.AUTHORIZATION_KEY_ID,
            "public_key_b64url": "9CrbcSDIJl0qMqaCP4ZtS4nPS5xdJX54hXGk1iG_aLQ",
        },
        {
            "key_id": lane.AUTHORIZATION_KEY_ID,
            "commit_sha": "8adb96a78f60c95c21eda0a0fbd268ade255f626",
        },
        {
            "key_id": lane.AUTHORIZATION_KEY_ID,
            "preimage_sha256": "sha256:42d2f52f7fce8b9c10add1e9ff2f3a6c12b520f229910e62017c903eec8a6a59",
        },
    ]

    for packet in legacy_packets:
        verdict = r7.reject_legacy_or_residue(packet)
        assert verdict["accepted"] is False
        assert verdict["rejection_reasons"]
        assert verdict["openrouter_calls_made"] == 0


def test_packet_generation_is_zero_call_and_signature_absent() -> None:
    packet = r7.build_packet()

    assert packet["openrouter_calls_made"] == 0
    assert packet["signature"] is None
    assert packet["signature_request"] is None
    assert packet["status"] == "FRESH_ROOT1_R7_PREIMAGE_READY_EXACT_SIGNATURE_APPROVAL_REQUIRED"
    assert packet["canonical_preimage_byte_length"] == len(
        r7.canonical_bytes(packet["target_authorization_preimage"])
    )
    assert packet["canonical_preimage_sha256"].startswith("sha256:")


def test_anchor_child_parent_is_exact_source_candidate() -> None:
    head_parent = subprocess.check_output(["git", "rev-parse", "HEAD^"], text=True).strip()
    assert head_parent == r7.SOURCE_CANDIDATE_PARENT_SHA or head_parent == r7.SOURCE_CANDIDATE_SHA


def test_n5_delta_scope_is_zero_weight_reported_not_scored() -> None:
    preimage = r7.target_authorization_preimage()

    assert base.SCANNER_VERSION == "0.5.0"
    assert preimage["authorization_scope"]["rubric_point_id"] == [
        "GOVERNANCE.TRANSPARENCY.AI_DISCLOSURE",
        "OPS.REFUSAL.HARMFUL_OUTPUT_GOLDEN",
        "OPS.SCOPE.OUT_OF_SCOPE_CONTAINMENT",
        "P1.4",
        "P4.1",
    ]
    assert preimage["score_effect"]["total_delta"] == 0
    assert set(preimage["score_effect"]["pillar_deltas"].values()) == {0}
