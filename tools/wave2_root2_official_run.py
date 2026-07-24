#!/usr/bin/env python3
"""Official BEAAA-17472 Wave 2 root2 orchestration entrypoint.

This script has two modes:
- dry verification: build the exact runtime authorization object and verify the
  supplied public receipt without acquiring the single-run lease.
- execution: after the same verification passes, call the lane builder that
  acquires the fixed durable lease before target call 1.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from evals.aiuc_path_b import lane_a_depth as lane  # noqa: E402


def _fingerprint(public_key_b64url: str) -> str:
    raw = base64.urlsafe_b64decode(public_key_b64url + ("=" * (-len(public_key_b64url) % 4)))
    if len(raw) != 32:
        raise lane.TargetExecutionError("authorization_public_key_length_invalid")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def build_authorization(args: argparse.Namespace) -> lane.TargetProductionAuthorization:
    fingerprint = args.public_key_fingerprint or _fingerprint(args.public_key_b64url)
    return lane.TargetProductionAuthorization(
        target_id="wave2-authorized-production-reference-target",
        candidate_commit_sha=args.candidate_sha,
        parent_sha=args.parent_sha,
        authorization_ref=args.authorization_ref,
        authorization_issued_at=args.issued_at,
        authorization_expires_at=args.expires_at,
        target_endpoint=lane.OFFICIAL_TARGET_ENDPOINT,
        lease_root=str(lane.OFFICIAL_RUN_LEASE_ROOT),
        tenant_boundary="single authorized tenant; no cross-tenant context reuse",
        session_boundary="fresh target session per case and seed",
        allowed_family_ids=tuple(sorted(lane.SPECS)),
        evidence_immutable=True,
        signature=args.signature,
        key_id=lane.AUTHORIZATION_KEY_ID,
        public_key_b64url=args.public_key_b64url,
        public_key_fingerprint=fingerprint,
        custody_receipt_sha256=args.custody_receipt_sha256,
    )


def write_preimage(out_dir: Path, auth: lane.TargetProductionAuthorization) -> dict[str, str]:
    preimage = lane.authorization_preimage(auth)
    blob = lane._canonical(preimage).encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    preimage_json = out_dir / "target-authorization-preimage.json"
    preimage_bytes = out_dir / "target-authorization-preimage.bytes"
    preimage_json.write_text(json.dumps(preimage, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    preimage_bytes.write_bytes(blob)
    return {
        "preimage_json": str(preimage_json),
        "preimage_bytes": str(preimage_bytes),
        "preimage_sha256": "sha256:" + hashlib.sha256(blob).hexdigest(),
        "preimage_bytes_len": str(len(blob)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--parent-sha", required=True)
    parser.add_argument("--authorization-ref", required=True)
    parser.add_argument("--issued-at", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--public-key-b64url", required=True)
    parser.add_argument("--public-key-fingerprint")
    parser.add_argument("--custody-receipt-sha256", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-verify-only", action="store_true")
    args = parser.parse_args()

    auth = build_authorization(args)
    written = write_preimage(args.out_dir, auth)
    lane.verify_authorization(auth, commit_sha=args.candidate_sha, parent_sha=args.parent_sha)
    lane.verify_official_target_wrapper(auth)

    if args.dry_verify_only:
        print(
            json.dumps(
                {
                    "status": "DRY_VERIFY_PASS_NO_LEASE_NO_CALLS",
                    "official_endpoint": lane.OFFICIAL_TARGET_ENDPOINT,
                    "lease_root": str(lane.OFFICIAL_RUN_LEASE_ROOT),
                    "openrouter_calls_made": 0,
                    **written,
                },
                sort_keys=True,
            )
        )
        return 0

    record_store = lane.DurableCaseRecordStore(args.out_dir / "case-records")
    packet = lane.build_unsigned_target_evidence_packet(
        commit_sha=args.candidate_sha,
        parent_sha=args.parent_sha,
        auth=auth,
        record_store=record_store,
    )
    packet_path = args.out_dir / "wave2-root2-target-evidence-packet.json"
    packet_path.write_text(json.dumps(packet, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "EXECUTION_COMPLETE",
                "packet_path": str(packet_path),
                "unique_scenario_count": packet["unique_scenario_count"],
                "execution_count_k5": packet["execution_count_k5"],
                "durable_case_record_set_sha256": packet["durable_case_record_set_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
