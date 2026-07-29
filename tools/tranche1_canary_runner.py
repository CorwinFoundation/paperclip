"""BEAAA-19021 production-local exact-allowlist tranche-1 canary runner."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import ModelRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget
from tools.tranche1_observational import (
    ALLOWLIST,
    CONFIG_SHA256,
    DISCLOSURE,
    ENVELOPE_VERSION,
    EVALUATOR_SHA256,
    INVENTORY_SHA256,
    MAPPING_SHA256,
    rank_families,
)

EXACT_TARGET_CALLS = 36 * 5
_FULL_LOWER_SHA = re.compile(r"[0-9a-f]{40}")
_IMMUTABLE_IMAGE_REF = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
_HMAC_SIGNATURE = re.compile(r"hmac-sha256:[0-9a-f]{64}")
PROMOTION_LEASE_VERSION = "tranche1-promotion-lease/v1"
PROMOTION_ISSUES = ["BEAAA-19023", "BEAAA-19024"]


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


ALLOWLIST_SHA256 = hashlib.sha256(_canonical_json(ALLOWLIST)).hexdigest()


class Tranche1CanaryError(RuntimeError):
    pass


class _NoJudge(ModelRunner):
    def model_id(self) -> str:
        return "judge:none"

    def complete(self, _prompt: str) -> str:
        raise AssertionError("tranche-1 canary must never call a judge")


@dataclass(frozen=True)
class _CanaryJob:
    job_id: str
    agent_id: str
    seed: int
    capability_manifest: Mapping[str, bool]


def validate_tranche1_authority(
    *,
    promotion_lease: Mapping[str, Any],
    promotion_lease_key: bytes,
    tenant_id: str,
    authorization_ref: str,
    commit_sha: str,
    image_ref: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify all authority and runtime identity before any call or evidence write."""
    if len(promotion_lease_key) < 32:
        raise Tranche1CanaryError("promotion lease verification key is absent or too short")
    lease = dict(promotion_lease)
    signature = lease.pop("signature", None)
    if not isinstance(signature, str) or not _HMAC_SIGNATURE.fullmatch(signature):
        raise Tranche1CanaryError("promotion lease signature is absent or malformed")
    expected = "hmac-sha256:" + hmac.new(
        promotion_lease_key, _canonical_json(lease), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise Tranche1CanaryError("promotion lease signature is invalid")

    runtime_commit = os.environ.get("BACKBOND_RUNTIME_COMMIT_SHA", "")
    authorized_commit = os.environ.get("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", "")
    runtime_image = os.environ.get("BACKBOND_RUNTIME_IMAGE_REF", "")
    runtime_environment = os.environ.get("BACKBOND_RUNTIME_ENVIRONMENT", "")
    if not _FULL_LOWER_SHA.fullmatch(commit_sha):
        raise Tranche1CanaryError("commit pin must be a full lowercase 40-hex SHA")
    if not _FULL_LOWER_SHA.fullmatch(runtime_commit):
        raise Tranche1CanaryError("runtime commit must be a full lowercase 40-hex SHA")
    if not _FULL_LOWER_SHA.fullmatch(authorized_commit):
        raise Tranche1CanaryError("authorized commit must be a full lowercase 40-hex SHA")
    if commit_sha != runtime_commit or commit_sha != authorized_commit:
        raise Tranche1CanaryError(
            "manifest commit must equal the runtime and authorized commits"
        )
    if not _IMMUTABLE_IMAGE_REF.fullmatch(image_ref):
        raise Tranche1CanaryError(
            "image pin must be an immutable repository@sha256:<64 lowercase hex> reference"
        )
    if image_ref != runtime_image:
        raise Tranche1CanaryError("image pin does not match independently observed runtime image")
    if runtime_environment != "production":
        raise Tranche1CanaryError("runtime environment must be production")

    required = {
        "version": PROMOTION_LEASE_VERSION,
        "environment": runtime_environment,
        "tenant_id": tenant_id,
        "authorization_ref": authorization_ref,
        "commit_sha": commit_sha,
        "image_ref": image_ref,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "target_call_ceiling": EXACT_TARGET_CALLS,
        "judge_call_ceiling": 0,
        "issues": PROMOTION_ISSUES,
    }
    for field, expected_value in required.items():
        if lease.get(field) != expected_value:
            raise Tranche1CanaryError(f"promotion lease {field} binding mismatch")
    if not lease.get("lease_id") or not lease.get("approval_ref") or not lease.get("key_id"):
        raise Tranche1CanaryError("promotion lease identity and approval reference are required")
    try:
        not_before = datetime.fromisoformat(str(lease["not_before"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(lease["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise Tranche1CanaryError("promotion lease validity window is malformed") from exc
    current = now or datetime.now(timezone.utc)
    if (
        not_before.tzinfo is None
        or expires_at.tzinfo is None
        or not_before > current
        or current >= expires_at
        or expires_at <= not_before
    ):
        raise Tranche1CanaryError("promotion lease is not currently valid")
    return lease


def select_exact_tranche(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    from tools.content_candidate_inventory import load_candidate_inventory

    selected = [
        dict(row)
        for row in rows
        if row.get("runner") == "content_candidate_worker"
        and row.get("test_id") in ALLOWLIST
        and row.get("scoring_active") is True
    ]
    counts = Counter(str(row.get("test_id")) for row in selected)
    identities = [(str(row.get("test_id")), str(row.get("case_id"))) for row in selected]
    expected_identities = {
        (str(row["family_id"]), str(row["stable_case_id"]))
        for row in load_candidate_inventory()
        if row["family_id"] in ALLOWLIST
    }
    if (
        dict(counts) != ALLOWLIST
        or len(identities) != len(set(identities))
        or set(identities) != expected_identities
    ):
        raise Tranche1CanaryError(
            f"pre-call exact-allowlist drift: counts={dict(counts)} unique={len(set(identities))}"
        )
    required = {
        "scoring_active": True,
        "underwriting_active": False,
        "effective_underwriting_weight": 0.0,
        "observational_contract_version": ENVELOPE_VERSION,
        "observational_disclosure": DISCLOSURE,
        "k_replicates": 5,
    }
    for row in selected:
        if any(row.get(key) != value for key, value in required.items()):
            raise Tranche1CanaryError(
                f"pre-call observational contract drift: {row.get('test_id')}/{row.get('case_id')}"
            )
    return sorted(selected, key=lambda row: (row["test_id"], row["case_id"]))


def run_tranche1_canary(
    *,
    registry_rows: Iterable[Mapping[str, Any]],
    subject: ModelRunner,
    evidence_root: Path,
    job_id: str,
    tenant_id: str,
    authorization_ref: str,
    commit_sha: str,
    image_ref: str,
    promotion_lease: Mapping[str, Any],
    promotion_lease_key: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not authorization_ref or not tenant_id:
        raise Tranche1CanaryError("opaque authorization reference and tenant are required")
    authority = validate_tranche1_authority(
        promotion_lease=promotion_lease,
        promotion_lease_key=promotion_lease_key,
        tenant_id=tenant_id,
        authorization_ref=authorization_ref,
        commit_sha=commit_sha,
        image_ref=image_ref,
        now=now,
    )
    rows = select_exact_tranche(registry_rows)

    executor = RegisteredCaseExecutor(
        subject=subject,
        judge=_NoJudge(),
        evidence_store=EvidenceStore(evidence_root),
        budget=ScanBudget(
            max_target_calls=EXACT_TARGET_CALLS,
            max_judge_calls=0,
            max_estimated_judge_cost_usd=0,
        ),
        tenant_id=tenant_id,
        audience="internal_observational",
    )
    job = _CanaryJob(
        job_id=job_id,
        agent_id=authorization_ref,
        seed=0,
        capability_manifest={"has_tool_calling": True},
    )
    results = [executor(row, job) for row in rows]
    if any(result.status != "complete" for result in results):
        raise Tranche1CanaryError("case failed or budget stopped the bounded run")
    budget = executor.ledger.snapshot()
    if budget["target_calls"] != EXACT_TARGET_CALLS or budget["judge_calls"] != 0:
        raise Tranche1CanaryError(f"exact execution budget mismatch: {budget}")

    records = [
        executor._observational_records[(row["test_id"], row["case_id"])]
        for row in rows
    ]
    rankings = rank_families(records)
    families = [
        {
            **ranking,
            "scoring_active": True,
            "underwriting_active": False,
            "effective_underwriting_weight": 0.0,
        }
        for ranking in rankings
    ]
    manifest = {
        "ticket": "BEAAA-19021",
        "job_id": job_id,
        "scanner_version": "0.5.0",
        "api_contract": "are-scanner-api-v1@v1.0",
        "envelope": ENVELOPE_VERSION,
        "authorization_ref": authorization_ref,
        "promotion_authority": {
            "lease_id": authority["lease_id"],
            "approval_ref": authority["approval_ref"],
            "key_id": authority["key_id"],
            "not_before": authority["not_before"],
            "expires_at": authority["expires_at"],
        },
        "capability_manifest": {"has_tool_calling": True},
        "pins": {
            "commit": commit_sha,
            "image": image_ref,
            "config": CONFIG_SHA256,
            "inventory_corpus": INVENTORY_SHA256,
            "mapping": MAPPING_SHA256,
            "evaluator": next(iter(set(EVALUATOR_SHA256.values()))),
        },
        "budget": budget,
        "families": families,
        "zero_weight_invariants": {
            "scoring_active": True,
            "underwriting_active": False,
            "effective_weight": 0.0,
            "absolute_delta": {
                "are_total": 0,
                "pillars": 0,
                "band": 0,
                "eligibility": 0,
                "pricing": 0,
                "premium": 0,
                "quote_policy_behavior": 0,
                "published_weighted_countable_totals": 0,
            },
            "other_candidate_families": "reported_not_scored",
        },
    }
    executor.write_manifest(manifest)
    return manifest


def _secret_from_args(args: argparse.Namespace) -> str:
    if args.secret_fd is not None:
        with os.fdopen(os.dup(args.secret_fd), "r", encoding="utf-8") as handle:
            secret = handle.read().rstrip("\r\n")
    else:
        secret = os.environ.get(args.secret_env, "")
    if not secret:
        raise Tranche1CanaryError("secret is absent")
    return secret


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-endpoint", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--authorization-ref", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--promotion-lease", type=Path, required=True)
    parser.add_argument("--promotion-lease-key-fd", type=int, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    secret = parser.add_mutually_exclusive_group(required=True)
    secret.add_argument("--secret-fd", type=int)
    secret.add_argument("--secret-env")
    args = parser.parse_args()
    credential = _secret_from_args(args)
    try:
        promotion_lease = json.loads(args.promotion_lease.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Tranche1CanaryError("promotion lease is unreadable") from exc
    with os.fdopen(os.dup(args.promotion_lease_key_fd), "rb") as handle:
        promotion_lease_key = handle.read().rstrip(b"\r\n")
    validate_tranche1_authority(
        promotion_lease=promotion_lease,
        promotion_lease_key=promotion_lease_key,
        tenant_id=args.tenant_id,
        authorization_ref=args.authorization_ref,
        commit_sha=args.commit_sha,
        image_ref=args.image_ref,
    )
    report = run_tranche1_canary(
        registry_rows=build_registry(),
        subject=customer_agent_http_runner_factory(args.agent_endpoint, credential),
        evidence_root=args.evidence_dir,
        job_id=args.job_id,
        tenant_id=args.tenant_id,
        authorization_ref=args.authorization_ref,
        commit_sha=args.commit_sha,
        image_ref=args.image_ref,
        promotion_lease=promotion_lease,
        promotion_lease_key=promotion_lease_key,
    )
    print(json.dumps({
        "job_id": report["job_id"],
        "authorization_ref": report["authorization_ref"],
        "target_calls": report["budget"]["target_calls"],
        "judge_calls": report["budget"]["judge_calls"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
