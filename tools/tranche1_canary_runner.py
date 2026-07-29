"""BEAAA-19021 production-local exact-allowlist tranche-1 canary runner."""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass
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
) -> dict[str, Any]:
    rows = select_exact_tranche(registry_rows)
    if not authorization_ref or not tenant_id:
        raise Tranche1CanaryError("opaque authorization reference and tenant are required")
    if len(commit_sha) != 40:
        raise Tranche1CanaryError("exact 40-character commit pin is required")

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
    parser.add_argument("--evidence-dir", type=Path, required=True)
    secret = parser.add_mutually_exclusive_group(required=True)
    secret.add_argument("--secret-fd", type=int)
    secret.add_argument("--secret-env")
    args = parser.parse_args()
    credential = _secret_from_args(args)
    report = run_tranche1_canary(
        registry_rows=build_registry(),
        subject=customer_agent_http_runner_factory(args.agent_endpoint, credential),
        evidence_root=args.evidence_dir,
        job_id=args.job_id,
        tenant_id=args.tenant_id,
        authorization_ref=args.authorization_ref,
        commit_sha=args.commit_sha,
        image_ref=args.image_ref,
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
