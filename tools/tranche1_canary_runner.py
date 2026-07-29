"""BEAAA-19021 production-local exact-allowlist tranche-1 canary runner."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
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
_ED25519_SIGNATURE = re.compile(r"ed25519:[A-Za-z0-9_-]{86}")
PROMOTION_LEASE_VERSION = "tranche1-promotion-lease/v2"
FOUNDER_AUTHORIZATION_VERSION = "founder-production-authorization/v1"
PROMOTION_ISSUES = ["BEAAA-19023", "BEAAA-19024"]
LEASE_LEDGER_PATH = Path("/var/lib/backbond/tranche1-promotion-leases.sqlite3")
TRUSTED_PROMOTION_ISSUERS = {
    # Private material is not present in this repository or accepted from callers.
    "backbond-production-authority-2026-07":
        "2dljRZ5Z1kmjeRiCjRylWmrvs2oERFnnp2eMSwn62Yg="
}


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


ALLOWLIST_SHA256 = hashlib.sha256(_canonical_json(ALLOWLIST)).hexdigest()


def _exact_selection_sha256() -> str:
    from tools.content_candidate_inventory import load_candidate_inventory

    identities = sorted(
        [str(row["family_id"]), str(row["stable_case_id"])]
        for row in load_candidate_inventory()
        if row["family_id"] in ALLOWLIST
    )
    return hashlib.sha256(_canonical_json({"identities": identities})).hexdigest()


EXACT_SELECTION_SHA256 = _exact_selection_sha256()


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
    founder_authorization: Mapping[str, Any],
    tenant_id: str,
    authorization_ref: str,
    commit_sha: str,
    image_ref: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify all authority and runtime identity before any call or evidence write."""
    lease = dict(promotion_lease)
    signature = lease.pop("signature", None)
    encoded_key = TRUSTED_PROMOTION_ISSUERS.get(str(lease.get("key_id")))
    if encoded_key is None:
        raise Tranche1CanaryError("promotion lease issuer is unknown or revoked")
    if not isinstance(signature, str) or not _ED25519_SIGNATURE.fullmatch(signature):
        raise Tranche1CanaryError("promotion lease signature is absent or malformed")
    try:
        signature_bytes = base64.urlsafe_b64decode(signature.removeprefix("ed25519:") + "==")
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key))
        public_key.verify(
            signature_bytes, _canonical_json(lease)
        )
    except (InvalidSignature, ValueError) as exc:
        raise Tranche1CanaryError("promotion lease signature is invalid") from exc

    approval = dict(founder_authorization)
    approval_signature = approval.pop("signature", None)
    if (
        not isinstance(approval_signature, str)
        or not _ED25519_SIGNATURE.fullmatch(approval_signature)
        or approval.get("key_id") != lease.get("key_id")
    ):
        raise Tranche1CanaryError("Founder authorization signature or issuer is invalid")
    try:
        public_key.verify(
            base64.urlsafe_b64decode(
                approval_signature.removeprefix("ed25519:") + "=="
            ),
            _canonical_json(approval),
        )
    except (InvalidSignature, ValueError) as exc:
        raise Tranche1CanaryError("Founder authorization signature is invalid") from exc

    runtime_commit = os.environ.get("BACKBOND_RUNTIME_COMMIT_SHA", "")
    authorized_commit = os.environ.get("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", "")
    runtime_image = os.environ.get("BACKBOND_RUNTIME_IMAGE_REF", "")
    runtime_environment = os.environ.get("BACKBOND_RUNTIME_ENVIRONMENT", "")
    runtime_tenant = os.environ.get("BACKBOND_RUNTIME_TENANT_ID", "")
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
    if runtime_tenant != tenant_id:
        raise Tranche1CanaryError("tenant does not match independently observed runtime tenant")

    required = {
        "version": PROMOTION_LEASE_VERSION,
        "environment": runtime_environment,
        "tenant_id": tenant_id,
        "authorization_ref": authorization_ref,
        "commit_sha": commit_sha,
        "image_ref": image_ref,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "exact_selection_sha256": EXACT_SELECTION_SHA256,
        "target_call_ceiling": EXACT_TARGET_CALLS,
        "judge_call_ceiling": 0,
        "issues": PROMOTION_ISSUES,
    }
    for field, expected_value in required.items():
        if lease.get(field) != expected_value:
            raise Tranche1CanaryError(f"promotion lease {field} binding mismatch")
    if not lease.get("lease_id") or not lease.get("approval_ref") or not lease.get("key_id"):
        raise Tranche1CanaryError("promotion lease identity and approval reference are required")
    approval_required = {
        "version": FOUNDER_AUTHORIZATION_VERSION,
        "status": "accepted",
        "authorizer_role": "Founder",
        "approval_ref": lease["approval_ref"],
        "key_id": lease["key_id"],
        "environment": runtime_environment,
        "tenant_id": tenant_id,
        "authorization_ref": authorization_ref,
        "commit_sha": commit_sha,
        "image_ref": image_ref,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "exact_selection_sha256": EXACT_SELECTION_SHA256,
        "target_call_ceiling": EXACT_TARGET_CALLS,
        "judge_call_ceiling": 0,
        "issues": PROMOTION_ISSUES,
    }
    if any(approval.get(field) != value for field, value in approval_required.items()):
        raise Tranche1CanaryError("Founder authorization binding mismatch")
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
    return {**lease, "signature": signature}


def consume_promotion_lease(
    *,
    authority: Mapping[str, Any],
    ledger_path: Path,
    consumed_at: datetime | None = None,
) -> str:
    """Atomically consume one exact signed authority scope before side effects."""
    scope = {
        field: authority[field]
        for field in (
            "lease_id",
            "signature",
            "commit_sha",
            "image_ref",
            "tenant_id",
            "environment",
        )
    }
    scope_sha256 = hashlib.sha256(_canonical_json(scope)).hexdigest()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(ledger_path), timeout=30, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS promotion_lease_consumptions (
                scope_sha256 TEXT NOT NULL UNIQUE,
                lease_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                image_ref TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                consumed_at TEXT NOT NULL
            )
            """
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO promotion_lease_consumptions
            (scope_sha256, lease_id, signature, commit_sha, image_ref,
             tenant_id, environment, consumed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope_sha256,
                scope["lease_id"],
                scope["signature"],
                scope["commit_sha"],
                scope["image_ref"],
                scope["tenant_id"],
                scope["environment"],
                (consumed_at or datetime.now(timezone.utc)).isoformat(),
            ),
        )
        connection.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        if connection is not None:
            connection.execute("ROLLBACK")
        raise Tranche1CanaryError("promotion lease has already been consumed") from exc
    except (OSError, sqlite3.Error) as exc:
        if connection is not None and connection.in_transaction:
            connection.execute("ROLLBACK")
        raise Tranche1CanaryError("promotion lease replay ledger failed closed") from exc
    finally:
        if connection is not None:
            connection.close()
    return scope_sha256


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
    founder_authorization: Mapping[str, Any],
    promotion_lease_ledger: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not authorization_ref or not tenant_id:
        raise Tranche1CanaryError("opaque authorization reference and tenant are required")
    authority = validate_tranche1_authority(
        promotion_lease=promotion_lease,
        founder_authorization=founder_authorization,
        tenant_id=tenant_id,
        authorization_ref=authorization_ref,
        commit_sha=commit_sha,
        image_ref=image_ref,
        now=now,
    )
    rows = select_exact_tranche(registry_rows)
    lease_scope_sha256 = consume_promotion_lease(
        authority=authority,
        ledger_path=promotion_lease_ledger,
        consumed_at=now,
    )

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
        "api_contract": "are-scanner-api-v1@v1.3.0",
        "envelope": ENVELOPE_VERSION,
        "authorization_ref": authorization_ref,
        "promotion_authority": {
            "lease_id": authority["lease_id"],
            "approval_ref": authority["approval_ref"],
            "key_id": authority["key_id"],
            "not_before": authority["not_before"],
            "expires_at": authority["expires_at"],
            "lease_scope_sha256": lease_scope_sha256,
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
    parser.add_argument("--founder-authorization", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    secret = parser.add_mutually_exclusive_group(required=True)
    secret.add_argument("--secret-fd", type=int)
    secret.add_argument("--secret-env")
    args = parser.parse_args()
    credential = _secret_from_args(args)
    try:
        promotion_lease = json.loads(args.promotion_lease.read_text(encoding="utf-8"))
        founder_authorization = json.loads(
            args.founder_authorization.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise Tranche1CanaryError("promotion lease is unreadable") from exc
    validate_tranche1_authority(
        promotion_lease=promotion_lease,
        founder_authorization=founder_authorization,
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
        founder_authorization=founder_authorization,
        promotion_lease_ledger=LEASE_LEDGER_PATH,
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
