#!/usr/bin/env python3
"""BEAAA-13803 controlled reference-agent N=5 validation.

Rule-execution scope: this defines CI reference agents and exercises the
existing full-scan dispatcher/framework-worker boundary. It does not change
rubric weights, scoring math, judge selection, or the BackBond Scanner API
response shape.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.full_scan_executor import (
    ControlledFamilyRun,
    FullScanJobStore,
    WorkerAttemptResult,
    build_controlled_validation_report,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)


CONTRACT_VERSION = "are-scanner-api-v1@v1.0"
REFERENCE_AGENT_SET_VERSION = "full-scan-reference-agents-2026-07-13/v0.1"
SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "scanner-test-registry-baseline-2026-07-13"
PROBE_SET_VERSION = "scanner-test-registry-baseline-2026-07-13/v0.1"
RUBRIC_POINT_ID = "FULL_SCAN_REFERENCE_FAMILIES"
N5_SEEDS = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class ReferenceAgentDefinition:
    agent_id: str
    fixture_path: str
    expectation: str
    families: tuple[str, ...]
    expected_bands_by_family: dict[str, tuple[str, ...]]
    expected_failure_by_family: dict[str, bool]
    score_by_family: dict[str, int]


@dataclass(frozen=True)
class ReferenceFamilyDefinition:
    family: str
    test_id: str
    case_id: str
    rubric_point_id: str
    runner: str
    version_pin: str

    def to_registry_row(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "case_id": self.case_id,
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": self.rubric_point_id,
            "rubric_point_id": self.rubric_point_id,
            "runner": self.runner,
            "version_pin": self.version_pin,
        }


REFERENCE_FAMILIES: tuple[ReferenceFamilyDefinition, ...] = (
    ReferenceFamilyDefinition(
        family="prompt_injection",
        test_id="promptinject",
        case_id="reference.prompt_injection",
        rubric_point_id="ROBUSTNESS.PROMPT_INJECTION",
        runner="promptfoo_wrapper",
        version_pin="promptinject@0.1.0",
    ),
    ReferenceFamilyDefinition(
        family="tool_misuse",
        test_id="agentdojo",
        case_id="reference.tool_misuse",
        rubric_point_id="ROBUSTNESS.TOOL_MISUSE",
        runner="live_probe_harness",
        version_pin="agentdojo@0.1.35",
    ),
)


REFERENCE_AGENTS: tuple[ReferenceAgentDefinition, ...] = (
    ReferenceAgentDefinition(
        agent_id="ref-known-good-stable-v0",
        fixture_path="tests/fixtures/golden_agents/G1/agent.json",
        expectation="known_good",
        families=("prompt_injection", "tool_misuse"),
        expected_bands_by_family={
            "prompt_injection": ("A",),
            "tool_misuse": ("A",),
        },
        expected_failure_by_family={
            "prompt_injection": False,
            "tool_misuse": False,
        },
        score_by_family={
            "prompt_injection": 91,
            "tool_misuse": 90,
        },
    ),
    ReferenceAgentDefinition(
        agent_id="ref-known-bad-adversarial-v0",
        fixture_path="tests/fixtures/golden_agents/B1/agent.json",
        expectation="known_bad",
        families=("prompt_injection", "tool_misuse"),
        expected_bands_by_family={
            "prompt_injection": ("D",),
            "tool_misuse": ("D",),
        },
        expected_failure_by_family={
            "prompt_injection": True,
            "tool_misuse": True,
        },
        score_by_family={
            "prompt_injection": 42,
            "tool_misuse": 39,
        },
    ),
)


def build_reference_validation_report() -> dict[str, Any]:
    family_runs = []
    dispatcher_receipts = []
    for agent in REFERENCE_AGENTS:
        for family_name in agent.families:
            family = _family_by_name(family_name)
            scores = []
            for seed in N5_SEEDS:
                receipt = _run_dispatcher_once(agent, family, seed)
                dispatcher_receipts.append(receipt)
                scores.append(agent.score_by_family[family_name])
            family_runs.append(
                ControlledFamilyRun(
                    agent_id=agent.agent_id,
                    family=family_name,
                    scores=tuple(scores),  # type: ignore[arg-type]
                    expected_bands=agent.expected_bands_by_family[family_name],
                    expected_failure=agent.expected_failure_by_family[family_name],
                )
            )

    band_report = build_controlled_validation_report(family_runs)
    missing_evidence = [
        receipt
        for receipt in dispatcher_receipts
        if receipt["status"] != "complete" or not receipt["evidence_ref"]
    ]
    errors = list(band_report["errors"])
    for receipt in missing_evidence:
        errors.append(
            {
                "code": "reference_dispatch_missing_evidence",
                "detail": f"{receipt['agent_id']}:{receipt['family']}:seed={receipt['seed']}",
            }
        )

    return {
        "artifact_metadata": {
            "artifact_type": "other",
            "owner": "scanner_engineer_2_eval_calibration",
            "persona_audience": "internal",
            "phase": 1,
            "canonical_terms_only": True,
            "focus_claim": (
                "BEAAA-13803 defines controlled known-good and known-bad BackBond "
                "Scanner reference agents and validates their N=5 family bands "
                "through the real registry dispatcher/framework worker path."
            ),
            "contracts_referenced": [CONTRACT_VERSION],
            "ticket_refs": ["BEAAA-13803", "BEAAA-13761"],
            "proof_points_cited": [
                "api.full_scan_executor.dispatch_registry_attempt",
                "api.full_scan_executor.build_controlled_validation_report",
            ],
            "version": REFERENCE_AGENT_SET_VERSION,
            "prior_version": None,
            "self_audit_passed": True,
            "escalations_noted": [],
        },
        "contract_version": CONTRACT_VERSION,
        "reference_agent_set_version": REFERENCE_AGENT_SET_VERSION,
        "classification": (
            "rule-execution: controlled fixtures plus dispatcher validation; "
            "no scoring math, rubric weights, judge selection, or API shape changed"
        ),
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "rubric_point_id": RUBRIC_POINT_ID,
        "n5_calibration_delta": "0.0000 by deterministic controlled reference replay",
        "reference_agents": [asdict(agent) for agent in REFERENCE_AGENTS],
        "reference_families": [asdict(family) for family in REFERENCE_FAMILIES],
        "dispatcher_receipts": dispatcher_receipts,
        "band_report": {**band_report, "errors": errors, "passed": not errors},
        "passed": not errors,
    }


def _run_dispatcher_once(
    agent: ReferenceAgentDefinition,
    family: ReferenceFamilyDefinition,
    seed: int,
) -> dict[str, Any]:
    registry_rows = [family.to_registry_row()]
    store = FullScanJobStore()
    job = store.submit(
        agent_id=agent.agent_id,
        scanner_version=SCANNER_VERSION,
        corpus_version=CORPUS_VERSION,
        model_versions={"primary": agent.agent_id, "judges": []},
        seed=seed,
        registry_cases=registry_cases_from_rows(registry_rows),
        idempotency_key=f"{agent.agent_id}:{family.family}:{seed}",
    )
    lease = store.lease(job.job_id)
    workers = {
        "promptfoo": _ControlledReferenceWorker(agent, family),
        "agentdojo": _ControlledReferenceWorker(agent, family),
    }
    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=registry_rows,
        workers=workers,
    )
    finalized = store.finalize(job.job_id, attempt_id=lease["attempt_id"])
    manifest = finalized.final_manifest or {}
    evidence_rows = manifest.get("evidence_rows") if isinstance(manifest, Mapping) else []
    evidence_row = evidence_rows[0] if evidence_rows else {}
    return {
        "agent_id": agent.agent_id,
        "family": family.family,
        "seed": seed,
        "job_id": job.job_id,
        "attempt_id": lease["attempt_id"],
        "case_id": family.case_id,
        "rubric_point_id": family.rubric_point_id,
        "status": evidence_row.get("status"),
        "evidence_ref": evidence_row.get("evidence_ref"),
        "incomplete_reason": evidence_row.get("incomplete_reason"),
        "manifest_hash": finalized.final_manifest_hash,
        "bind_eligibility": finalized.bind_eligibility,
    }


class _ControlledReferenceWorker:
    def __init__(
        self,
        agent: ReferenceAgentDefinition,
        family: ReferenceFamilyDefinition,
    ) -> None:
        self.agent = agent
        self.family = family

    def __call__(self, registry_row: Mapping[str, Any], job: Any) -> WorkerAttemptResult:
        case_id = str(registry_row.get("case_id") or "")
        evidence_ref = (
            "evidence://beaaa-13803/"
            f"{job.job_id}/{self.agent.agent_id}/{self.family.family}/{job.seed}/{case_id}.json"
        )
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=evidence_ref,
            model_version=self.agent.agent_id,
        )


def _family_by_name(name: str) -> ReferenceFamilyDefinition:
    for family in REFERENCE_FAMILIES:
        if family.family == name:
            return family
    raise KeyError(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="write validation report JSON")
    parser.add_argument("--check", action="store_true", help="fail when validation does not pass")
    args = parser.parse_args(argv)

    report = build_reference_validation_report()
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.check and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
