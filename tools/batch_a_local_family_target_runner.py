"""Target-bound adapters for BEAAA-17708 batch-A local families.

The adapters only orchestrate existing corpus loaders and scorers. They do
not duplicate rubric or judge logic.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from harness.bench import (
    bbq,
    citation_integrity_legal,
    confidence_calibration,
    counterfactual_pairs,
    donotanswer,
    hallucination_legal,
    indirect_injection,
    p2_paired_attr,
)
from harness.runner import MockRunner, ModelRunner, RunnerError

ROOT = Path(__file__).resolve().parents[1]
SCANNER_VERSION = "0.5.0"
CONTRACT = "contracts/are-scanner-api-v1.md@v1.3.0"
FAMILIES = (
    "bbq",
    "citation_integrity_legal",
    "confidence_calibration",
    "counterfactual_stability",
    "demographic_parity",
    "donotanswer",
    "equal_opportunity",
    "hallucination_legal",
    "indirect_injection",
)

@dataclass(frozen=True)
class LocalExecutionPolicy:
    """Immutable authorization facts for the zero-cost local fixture lane."""

    subject_model_id: str = "mock:configured-development-target@fixture"
    judge_model_id: str = "mock:development-judge@pinned"
    credential_class: str = "test_only"
    paid_calls: int = 0
    customer_data: bool = False
    score_activation: bool = False


LOCAL_FIXTURE_POLICY = LocalExecutionPolicy()


class BatchALocalRunError(RuntimeError):
    """A local family did not produce complete target-bound evidence."""


def _authorize_local_fixture(
    subject: ModelRunner,
    judge: ModelRunner,
    policy: LocalExecutionPolicy,
) -> None:
    """Reject adapters that can reach a provider before either runner is called."""
    if type(subject) is not MockRunner or subject.model_id() != policy.subject_model_id:
        raise BatchALocalRunError(
            "subject is not the authorized test-only development fixture"
        )
    if type(judge) is not MockRunner or judge.model_id() != policy.judge_model_id:
        raise BatchALocalRunError("judge is not the authorized pinned test-only fixture")


class _CountingRunner(ModelRunner):
    def __init__(self, delegate: ModelRunner, cap: int) -> None:
        self.delegate = delegate
        self.cap = cap
        self.calls = 0

    def model_id(self) -> str:
        return self.delegate.model_id()

    def complete(self, prompt: str) -> str:
        if self.calls >= self.cap:
            raise BatchALocalRunError("configured development-target call cap exceeded")
        self.calls += 1
        try:
            result = self.delegate.complete(prompt)
        except Exception as exc:
            raise BatchALocalRunError("configured development target unavailable") from exc
        if not isinstance(result, str) or not result.strip():
            raise BatchALocalRunError("configured development target returned empty evidence")
        return result


def _indirect_items() -> list[indirect_injection.IndirectInjectionItem]:
    data = yaml.safe_load((ROOT / "rubric/probe_mappings/indirect_injection.yaml").read_text())
    items: list[indirect_injection.IndirectInjectionItem] = []
    for surface_id, surface in data["probe_categories"].items():
        for seed in surface["seeds"]:
            payload = seed["payload"]
            tokens = [
                token for token in ("PWN-IMG", "MD-PWN", "CANARY", "EXFIL")
                if token.casefold() in payload.casefold()
            ]
            canary = tokens[0] if tokens else seed["id"]
            items.append(indirect_injection.IndirectInjectionItem(
                id=seed["id"], category=surface_id, prompt=payload, canary=canary
            ))
    if not items:
        raise BatchALocalRunError("indirect-injection corpus is empty")
    return items


def _execute(
    family: str, subject: ModelRunner, judge: ModelRunner, progress: Callable[..., None]
) -> dict[str, Any]:
    mapping = ROOT / "rubric/probe_mappings" / f"{family}.yaml"
    if family == "bbq":
        return bbq.run(subject, bbq.load_items(ROOT / "corpus/bias/bbq/bbq_subset_n200_seed42.jsonl"),
                       ensemble_n=1, seed=42, on_progress=progress)
    if family == "citation_integrity_legal":
        return citation_integrity_legal.run_family_aware(subject, judge, mapping, ensemble_n=1)
    if family == "confidence_calibration":
        return confidence_calibration.run(subject, confidence_calibration.load_corpus(mapping),
                                          on_progress=progress)
    if family == "counterfactual_stability":
        return counterfactual_pairs.run(subject, judge, mapping, ensemble_n=1,
                                        on_progress=progress)
    if family in ("demographic_parity", "equal_opportunity"):
        pairs = p2_paired_attr.load_pairs(
            ROOT / "rubric/probe_mappings/counterfactual_stability.yaml"
        )
        mode = "parity"
        if family == "equal_opportunity":
            mode = "equal_opportunity"
            pairs = p2_paired_attr.load_eo_labeled_subset(pairs, mapping)
        if not pairs:
            raise BatchALocalRunError(f"{family} corpus is empty")
        return p2_paired_attr.run(mode, subject, judge, pairs, ensemble_n=1,
                                  minimum_n=1, on_progress=progress)
    if family == "donotanswer":
        return donotanswer.run(
            subject,
            donotanswer.load_items(ROOT / "corpus/bias/donotanswer/donotanswer_subset_n100_seed42.jsonl"),
            judge_runner=judge, ensemble_n=1, seed=42, on_progress=progress,
        )
    if family == "hallucination_legal":
        rollup = hallucination_legal.run_f2_case_citation(
            subject, judge, hallucination_legal.load_f2_probes(mapping), ensemble_n=1,
            on_progress=progress,
        )
        return {"dim_id": "P1.1", "benchmark": "hallucination_legal-v0.3.0",
                "raw": hallucination_legal.pass_rate_to_raw(rollup["pass_rate"]),
                "trace": rollup["trace"], "rollup": rollup}
    if family == "indirect_injection":
        return indirect_injection.run(subject, _indirect_items(), on_progress=progress)
    raise BatchALocalRunError(f"unsupported family: {family}")


def run_family(
    family: str,
    *,
    subject: ModelRunner,
    judge: ModelRunner,
    evidence_dir: Path,
    call_cap: int = 2000,
    policy: LocalExecutionPolicy = LOCAL_FIXTURE_POLICY,
) -> dict[str, Any]:
    if family not in FAMILIES:
        raise BatchALocalRunError(f"family is not in batch A local set: {family}")
    if policy is not LOCAL_FIXTURE_POLICY:
        raise BatchALocalRunError("unrecognized local execution policy")
    _authorize_local_fixture(subject, judge, policy)
    counted_subject = _CountingRunner(subject, call_cap)
    counted_judge = _CountingRunner(judge, call_cap)
    case_evidence: list[dict[str, Any]] = []

    def progress(current: int, total: int, row: dict[str, Any]) -> None:
        case_evidence.append({
            "ordinal": current,
            "total": total,
            "case_id": str(row.get("id", current)),
            "result_sha256": hashlib.sha256(
                json.dumps(row, sort_keys=True, default=str).encode()
            ).hexdigest(),
            "verdict": "terminal",
        })

    try:
        scored = _execute(family, counted_subject, counted_judge, progress)
    except (BatchALocalRunError, RunnerError):
        raise
    except Exception as exc:
        raise BatchALocalRunError(f"{family} scorer failed closed") from exc
    raw = scored.get("raw", scored.get("dim_raw"))
    if raw is None:
        raise BatchALocalRunError(f"{family} produced no terminal raw score")
    record = {
        "schema": "backbond.batch-a-local-target-evidence/v1",
        "issue": "BEAAA-17708",
        "contract": CONTRACT,
        "classification": "rule-execution",
        "family_id": family,
        "scanner_version": SCANNER_VERSION,
        "target_model": subject.model_id(),
        "judge_model": judge.model_id(),
        "target_calls": counted_subject.calls,
        "judge_calls": counted_judge.calls,
        "call_cap": call_cap,
        "paid_calls": policy.paid_calls,
        "credential_class": policy.credential_class,
        "customer_data": policy.customer_data,
        "score_activation": policy.score_activation,
        "raw": raw,
        "status": "complete",
        "case_evidence": case_evidence,
        "scored": scored,
    }
    encoded = (json.dumps(record, sort_keys=True, default=str) + "\n").encode()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{family}.json"
    path.write_bytes(encoded)
    record["evidence"] = {"path": str(path), "sha256": hashlib.sha256(encoded).hexdigest()}
    return record
