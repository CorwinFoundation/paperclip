from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from evals.aiuc_path_b import base
from evals.aiuc_path_b import lane_a_depth as lane

SOURCE_CANDIDATE_SHA = "a63c12215c5321b08a33dff4be3f696f79b40b76"
SOURCE_CANDIDATE_PARENT_SHA = "d3252d457aa4eeca246b04147913d1b7c6963381"
FOUNDER_APPROVAL_REF = "f10d2df2-2d72-4cd1-9ee4-6f34d4fe7d9a"
QA_SOURCE_GATE_REF = "BEAAA-17508"
OFFICIAL_ENTRYPOINT = "tools/wave2_openrouter_target.py"
LEASE_SCHEMA_VERSION = lane.RUN_LEASE_FORMAT_VERSION
MAXIMUM_CALLS = 360
SEEDS = [0, 1, 2, 3, 4]
ROOT1_CUSTODY_RECEIPT_SHA256 = (
    "sha256:db8a1a79267bf0ca2a5e1ca350f4ba35820c1a0207969cdbdb677832ed356be8"
)

REJECTED_ENGINEERING_R7 = {
    "commit_sha": "8adb96a78f60c95c21eda0a0fbd268ade255f626",
    "key_id": "wave2-target-production-authority-r7-2026-07",
    "public_key_b64url_prefix": "9Crbc",
    "public_key_fingerprint": (
        "sha256:66b218023b99c0e00aea594b57f9711e68dbebbc7619c930a7890b2984c36669"
    ),
    "preimage_sha256": "sha256:42d2f52f7fce8b9c10add1e9ff2f3a6c12b520f229910e62017c903eec8a6a59",
}


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(blob: bytes) -> str:
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def public_anchor() -> dict[str, Any]:
    public_key = lane.AUTHORIZATION_PUBLIC_KEY_B64URL
    return {
        "anchor_schema": "backbond-wave2-target-authority-public-anchor@r7.0",
        "ticket_ref": lane.R7_ANCHOR_TICKET_REF,
        "founder_approval_ref": FOUNDER_APPROVAL_REF,
        "key_id": lane.AUTHORIZATION_KEY_ID,
        "algorithm": lane.AUTHORIZATION_ALGORITHM,
        "public_key_encoding": "base64url-nopad-ed25519-public-key-bytes",
        "public_key_b64url": public_key,
        "public_key_fingerprint": lane.AUTHORIZATION_PUBLIC_KEY_FINGERPRINT,
        "custody_receipt_sha256": ROOT1_CUSTODY_RECEIPT_SHA256,
        "custody_attestation": (
            "founder_controlled_root_only_unused_private_key_root_root_0700_directory_0600_key_receipt"
        ),
        "private_key_material": "not_created_not_received_not_stored_not_used_by_scanner_engineering",
        "signature_status": "unsigned_separate_exact_preimage_approval_required",
        "supersedes_rejected_material": {
            "r6_key_id": "wave2-target-production-authority-2026-07",
            "r6_public_key_fingerprint": "sha256:6fe266305ed59701d1b3fec85bd13a4a03402e971a214976c4b0701577081916",
            "r6_signature": "rejected",
            "r6_preimage": "rejected",
            "r6_approval": "rejected",
            "r6_partial_packet": "rejected",
            "alternate_lease_residue": "rejected",
            "engineering_r7_commit_sha": REJECTED_ENGINEERING_R7["commit_sha"],
            "engineering_r7_key_id": REJECTED_ENGINEERING_R7["key_id"],
            "engineering_r7_public_key_fingerprint": REJECTED_ENGINEERING_R7[
                "public_key_fingerprint"
            ],
            "engineering_r7_preimage_sha256": REJECTED_ENGINEERING_R7["preimage_sha256"],
        },
    }


def target_authorization_preimage() -> dict[str, Any]:
    hashes = lane.expected_wave2_hashes()
    family_case_counts = {family: len(spec.cases) for family, spec in lane.SPECS.items()}
    unique_scenario_count = sum(family_case_counts.values())
    execution_count = unique_scenario_count * base.REPLICATION_K
    return {
        "artifact_type": "unsigned_target_authorization_preimage",
        "preimage_schema": "backbond-wave2-target-authorization-preimage@r7.0",
        "ticket_ref": lane.R7_ANCHOR_TICKET_REF,
        "parent_issue_ref": "BEAAA-17510",
        "source_issue_ref": "BEAAA-17472",
        "founder_public_anchor_approval_ref": FOUNDER_APPROVAL_REF,
        "qa_source_gate_ref": QA_SOURCE_GATE_REF,
        "contract": "are-scanner-api-v1@v1.3.0",
        "supervision_contract": "scanner-implementation-supervision-v1@v1.1",
        "scanner_version": base.SCANNER_VERSION,
        "source_candidate_sha": SOURCE_CANDIDATE_SHA,
        "source_candidate_parent_sha": SOURCE_CANDIDATE_PARENT_SHA,
        "required_anchor_child_parent_sha": SOURCE_CANDIDATE_SHA,
        "algorithm": lane.AUTHORIZATION_ALGORITHM,
        "key_id": lane.AUTHORIZATION_KEY_ID,
        "public_key_b64url": lane.AUTHORIZATION_PUBLIC_KEY_B64URL,
        "public_key_fingerprint": lane.AUTHORIZATION_PUBLIC_KEY_FINGERPRINT,
        "custody_receipt_sha256": ROOT1_CUSTODY_RECEIPT_SHA256,
        "root_only_custody_state": {
            "private_key_holder": "founder_root_only",
            "directory_mode": "root:root 0700",
            "key_and_receipt_mode": "root:root 0600",
            "private_key_used": False,
            "private_key_printed_attached_or_exposed_to_scanner_engineering": False,
        },
        "target_id": "wave2-authorized-production-reference-target",
        "target_endpoint": "exec://" + str((lane.REPO_ROOT / OFFICIAL_ENTRYPOINT).resolve()),
        "target_model": f"openrouter:{lane.OPENROUTER_TARGET_MODEL}",
        "runner": {
            "official_entrypoint": OFFICIAL_ENTRYPOINT,
            "official_entrypoint_sha256": lane._sha256_file(lane.REPO_ROOT / OFFICIAL_ENTRYPOINT),
            "system_policy_version": lane.BOUNDED_AGENT_SYSTEM_POLICY_VERSION,
            "system_policy_sha256": lane._sha256_obj(
                {
                    "version": lane.BOUNDED_AGENT_SYSTEM_POLICY_VERSION,
                    "policy": lane.BOUNDED_AGENT_SYSTEM_POLICY,
                }
            ),
        },
        "authorization_scope": {
            "authorized_family_ids": sorted(lane.SPECS),
            "rubric_point_id": sorted(
                {point for spec in lane.SPECS.values() for point in spec.rubric_point_ids}
            ),
            "corpus_version": {family: spec.version for family, spec in sorted(lane.SPECS.items())},
            "probe_set_version": {
                family: spec.version for family, spec in sorted(lane.SPECS.items())
            },
            "family_case_counts": family_case_counts,
            "unique_scenario_count": unique_scenario_count,
            "seeds": SEEDS,
            "execution_count_k5": execution_count,
            "maximum_calls": MAXIMUM_CALLS,
            "retries": 0,
            "fresh_sessions": "required_per_case_seed",
        },
        "hashes": {
            "corpus_sha256": hashes["corpus_sha256"],
            "config_sha256": hashes["config_sha256"],
            "policy_sha256": lane._sha256_obj(
                {
                    "version": lane.BOUNDED_AGENT_SYSTEM_POLICY_VERSION,
                    "policy": lane.BOUNDED_AGENT_SYSTEM_POLICY,
                }
            ),
            "evaluator_id": "wave2-deterministic-raw-response-evaluator@0.1.0",
            "evaluator_sha256": hashes["config_sha256"]["candidate_module"],
        },
        "lease": {
            "root": str(lane.OFFICIAL_RUN_LEASE_ROOT),
            "schema_version": LEASE_SCHEMA_VERSION,
            "single_run": True,
            "reject_active_residue_before_target_invocation": True,
            "reject_consumed_authority_before_target_invocation": True,
        },
        "fail_closed_stop_conditions": [
            "r6_or_rejected_engineering_r7_key_id_or_public_key_or_signature_or_preimage_or_approval_or_partial_packet_seen",
            "rejected_engineering_r7_commit_seen",
            "candidate_or_parent_sha_mismatch",
            "target_endpoint_not_official_entrypoint",
            "lease_root_mismatch",
            "active_or_consumed_lease_residue",
            "family_scope_mismatch",
            "case_count_or_seed_count_mismatch",
            "call_count_exceeds_360",
            "retry_requested",
            "missing_target_request_or_response_hash",
            "any_invalid_or_fail_verdict",
        ],
        "retention": {
            "minimum_retention": "policy term + 7 years",
            "evidence_state": "not_started_zero_live_calls",
        },
        "score_effect": {
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
        },
        "signing_status": "unsigned_exact_preimage_founder_decision_required",
        "live_call_status": "not_started_openrouter_calls_made_0",
        "promotion_status": "not_authorized",
    }


def reject_legacy_or_residue(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate public authority packet shape without invoking target transport."""

    calls = 0
    reasons: list[str] = []
    key_id = str(packet.get("key_id") or "")
    fingerprint = str(packet.get("public_key_fingerprint") or "")
    signature = str(packet.get("signature") or "")
    approval = str(packet.get("approval_ref") or "")
    lease_root = str(packet.get("lease_root") or "")
    commit_sha = str(packet.get("commit_sha") or packet.get("candidate_commit_sha") or "")
    preimage_sha = str(packet.get("preimage_sha256") or "")
    public_key = str(packet.get("public_key_b64url") or "")
    if key_id != lane.AUTHORIZATION_KEY_ID:
        reasons.append("r6_rejected_engineering_r7_or_unknown_key_id")
    if fingerprint and fingerprint != lane.AUTHORIZATION_PUBLIC_KEY_FINGERPRINT:
        reasons.append("r6_rejected_engineering_r7_or_unknown_public_key_fingerprint")
    if public_key.startswith(REJECTED_ENGINEERING_R7["public_key_b64url_prefix"]):
        reasons.append("rejected_engineering_r7_public_key_rejected")
    if commit_sha == REJECTED_ENGINEERING_R7["commit_sha"]:
        reasons.append("rejected_engineering_r7_commit_rejected")
    if preimage_sha == REJECTED_ENGINEERING_R7["preimage_sha256"]:
        reasons.append("rejected_engineering_r7_preimage_rejected")
    if signature:
        reasons.append("signature_material_rejected_in_unsigned_preimage_gate")
    if approval and approval != FOUNDER_APPROVAL_REF:
        reasons.append("legacy_approval_rejected")
    if lease_root and lease_root != str(lane.OFFICIAL_RUN_LEASE_ROOT):
        reasons.append("alternate_lease_root_rejected")
    if packet.get("partial_packet") is True:
        reasons.append("partial_packet_rejected")
    return {
        "accepted": not reasons,
        "rejection_reasons": reasons,
        "openrouter_calls_made": calls,
    }


def build_packet() -> dict[str, Any]:
    anchor = public_anchor()
    preimage = target_authorization_preimage()
    preimage_bytes = canonical_bytes(preimage)
    return {
        "artifact_type": "fresh_r7_zero_call_anchor_packet",
        "ticket_ref": lane.R7_ANCHOR_TICKET_REF,
        "source_candidate_sha": SOURCE_CANDIDATE_SHA,
        "source_candidate_parent_sha": SOURCE_CANDIDATE_PARENT_SHA,
        "git_head_at_generation": git(["rev-parse", "HEAD"]),
        "git_head_parent_at_generation": git(["rev-parse", "HEAD^"]),
        "public_anchor": anchor,
        "target_authorization_preimage": preimage,
        "canonical_preimage_encoding": "utf-8-json-sort_keys-compact",
        "canonical_preimage_byte_length": len(preimage_bytes),
        "canonical_preimage_sha256": sha256_bytes(preimage_bytes),
        "openrouter_calls_made": 0,
        "signature": None,
        "signature_request": None,
        "status": "FRESH_ROOT1_R7_PREIMAGE_READY_EXACT_SIGNATURE_APPROVAL_REQUIRED",
    }


def write_outputs(out_dir: Path) -> dict[str, str]:
    packet = build_packet()
    preimage_bytes = canonical_bytes(packet["target_authorization_preimage"])
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "r7_public_anchor": out_dir / "r7-public-anchor.json",
        "target_authorization_preimage_json": out_dir / "target-authorization-preimage.json",
        "target_authorization_preimage_bytes": out_dir / "target-authorization-preimage.bytes",
        "r7_zero_call_packet": out_dir / "r7-zero-call-anchor-packet.json",
    }
    paths["r7_public_anchor"].write_text(
        json.dumps(packet["public_anchor"], sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["target_authorization_preimage_json"].write_text(
        json.dumps(packet["target_authorization_preimage"], sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["target_authorization_preimage_bytes"].write_bytes(preimage_bytes)
    paths["r7_zero_call_packet"].write_text(
        json.dumps(packet, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    index = {
        "ticket_ref": lane.R7_ANCHOR_TICKET_REF,
        "status": packet["status"],
        "files": {
            name: {
                "path": str(path),
                "sha256": sha256_bytes(path.read_bytes()),
                "bytes": path.stat().st_size,
            }
            for name, path in paths.items()
        },
        "canonical_preimage_sha256": packet["canonical_preimage_sha256"],
        "canonical_preimage_byte_length": packet["canonical_preimage_byte_length"],
        "openrouter_calls_made": 0,
    }
    index_path = out_dir / "packet-hash-index.json"
    index_path.write_text(json.dumps(index, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {name: str(path) for name, path in {**paths, "packet_hash_index": index_path}.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/beaaa-17663"))
    args = parser.parse_args()
    written = write_outputs(args.out_dir)
    print(json.dumps(written, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
