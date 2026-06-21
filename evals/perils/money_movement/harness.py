"""pass^k money-movement runner for offline tau-bench/tau2-bench traces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .scorer import LedgerAction, MoneyMovementScenario, evidence_refs_for_run, verify_scenario

DEFAULT_PASS_K = 5
PROBE_SET_VERSION = "money-movement-passk@0.1.0"
TAU_BENCH_PIN = "sierra-research/tau-bench@59a200c6d575d595120f1cb70fea53cef0632f6b"
TAU2_BENCH_PIN = "sierra-research/tau2-bench@5ebebbe827b455b3ed04fcb9294235c6ef4e5fd6"


@dataclass(frozen=True)
class MoneyMovementRun:
    """Observed actions and state for one deterministic trial."""

    actions: list[LedgerAction]
    observed_state: dict


@dataclass(frozen=True)
class PassKResult:
    scenario_id: str
    k: int
    passes: int
    pass_k: bool
    trial_results: list[dict]
    evidence_refs: list[dict]
    probe_set_version: str = PROBE_SET_VERSION
    upstream_eval_version: str = f"{TAU_BENCH_PIN}; {TAU2_BENCH_PIN}"


RunFactory = Callable[[MoneyMovementScenario, int], MoneyMovementRun]


def run_pass_k(
    scenario: MoneyMovementScenario,
    run_factory: RunFactory,
    *,
    k: int = DEFAULT_PASS_K,
    artifact_prefix: str = "scans/offline/probes/money_movement",
) -> PassKResult:
    """Run k deterministic trials and require every trial to pass."""

    trial_results: list[dict] = []
    evidence_refs: list[dict] = []
    passes = 0
    for trial_index in range(k):
        run = run_factory(scenario, trial_index)
        result = verify_scenario(scenario, run.actions, run.observed_state)
        result["trial_index"] = trial_index
        trial_results.append(result)
        evidence_refs.extend(
            evidence_refs_for_run(
                result,
                artifact_prefix=f"{artifact_prefix}/trial-{trial_index}",
            )
        )
        passes += int(result["passed"])

    return PassKResult(
        scenario_id=scenario.scenario_id,
        k=k,
        passes=passes,
        pass_k=passes == k,
        trial_results=trial_results,
        evidence_refs=evidence_refs,
    )

