"""Configured development-target activation for BEAAA-17704 ordinals 21-24.

This is an orchestration layer over ``RegisteredCaseExecutor``.  The frozen
family payloads, judge adapters, version pins, and scoring logic remain owned
by their existing benchmark modules.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from harness.runner import ModelRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


SCANNER_VERSION = "0.5.0"
CONTRACT = "contracts/are-scanner-api-v1.md@v1.0"
FAMILIES = (
    "p1_1_factuality_golden",
    "p1_3_consistency_golden",
    "p1_4_refusal_golden",
    "p1_5_calibration_golden",
)
EXPECTED_CASE_COUNTS = {
    "p1_1_factuality_golden": 30,
    "p1_3_consistency_golden": 20,
    "p1_4_refusal_golden": 20,
    "p1_5_calibration_golden": 20,
}


class GoldenTargetRunError(RuntimeError):
    """The configured target could not produce complete frozen-family evidence."""


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _rows() -> list[dict[str, Any]]:
    rows = [
        row
        for row in build_registry()
        if row.get("test_id") in FAMILIES and row.get("status") == "active"
    ]
    rows.sort(key=lambda row: (FAMILIES.index(row["test_id"]), row["case_id"]))
    actual = {
        family: sum(row["test_id"] == family for row in rows) for family in FAMILIES
    }
    if actual != EXPECTED_CASE_COUNTS:
        raise GoldenTargetRunError(
            f"frozen registry case counts changed: expected={EXPECTED_CASE_COUNTS}, actual={actual}"
        )
    return rows


def run_configured_development_target(
    *,
    subject: ModelRunner,
    judge: ModelRunner,
    evidence_dir: Path,
    job_id: str = "beaaa-17704-development-smoke",
    seed: int = 42,
    budget: ScanBudget | None = None,
    rows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute every ordinal 21-24 case and emit deterministic aggregate evidence."""
    selected = list(rows) if rows is not None else _rows()
    executor = RegisteredCaseExecutor(
        subject=subject,
        judge=judge,
        evidence_store=EvidenceStore(evidence_dir),
        budget=budget
        or ScanBudget(
            max_target_calls=110,
            max_judge_calls=90,
            max_estimated_judge_cost_usd=0.0,
            judge_input_usd_per_million_tokens=0.0,
            judge_output_usd_per_million_tokens=0.0,
        ),
    )
    job = SimpleNamespace(job_id=job_id, agent_id=subject.model_id(), seed=seed)
    cases: list[dict[str, Any]] = []
    for row in selected:
        result = executor(row, job)
        case_path = (
            evidence_dir / job_id / row["test_id"] / f"{row['case_id']}.json"
        )
        evidence = json.loads(case_path.read_text(encoding="utf-8"))
        cases.append(
            {
                "test_id": row["test_id"],
                "case_id": row["case_id"],
                "rubric_point_id": evidence["rubric_point_id"],
                "version_pin": evidence["version_pin"],
                "status": result.status,
                "verdict": result.verdict,
                "target_calls": result.target_calls,
                "judge_calls": result.judge_calls,
                "payload_sha256": evidence["payload_sha256"],
                "response_sha256": evidence["response_sha256"],
                "evidence_ref": result.evidence_ref,
            }
        )

    snapshot = executor.ledger.snapshot()
    summary = {
        "schema": "backbond.batch-b-golden-target-evidence/v1",
        "issue": "BEAAA-17704",
        "contract": CONTRACT,
        "classification": "rule-execution",
        "scanner_version": SCANNER_VERSION,
        "corpus_version": {
            family: f"{family}@0.1.0" for family in FAMILIES
        },
        "probe-set-version": {
            family: f"{family}@0.1.0" for family in FAMILIES
        },
        "rubric_point_id": sorted({case["rubric_point_id"] for case in cases}),
        "target_model": subject.model_id(),
        "judge_models": [judge.model_id()],
        "seed": seed,
        "case_count": len(cases),
        "target_calls": snapshot["target_calls"],
        "judge_calls": snapshot["judge_calls"],
        "estimated_paid_call_cost_usd": snapshot["estimated_judge_cost_usd"],
        "families": [
            {
                "test_id": family,
                "case_count": sum(case["test_id"] == family for case in cases),
                "verdict_counts": {
                    verdict: sum(
                        case["test_id"] == family and case["verdict"] == verdict
                        for case in cases
                    )
                    for verdict in ("pass", "partial", "fail", "unresolved")
                },
            }
            for family in FAMILIES
        ],
        "cases": cases,
    }
    summary["deterministic_digest"] = _sha256_json(summary)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if any(case["status"] != "complete" for case in cases):
        raise GoldenTargetRunError("one or more frozen cases failed closed; see summary.json")
    return summary
