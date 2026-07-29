"""Real 203-case scanner orchestration for the public demo.

This module reuses the production registry, dispatcher, target transport,
judge adapters, evidence writer, budget ledger, and signed manifest. It does
not synthesize a score or substitute fixture responses.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from claude_judge_runner import ClaudeCodeProxyRunner, DEFAULT_SOCKET_PATH
from api.full_scan_executor import (
    FullScanJobStore,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from harness.runner import ModelRunner
from tools.active_test_registry import (
    build_registry,
    count_active_executable,
    distinct_case_collapses,
    distinct_case_counts,
    format_collapse_table,
    load_published_count,
    validate_registry,
)
from tools.registered_case_executor import ScanBudget, build_registered_case_workers
from tools.text_case_materializer import materialize_text_case


LOGGER = logging.getLogger(__name__)

Emit = Callable[..., None]
SubjectFactory = Callable[[str, str], ModelRunner]
JudgeFactory = Callable[[], ModelRunner]

FAMILY_LABELS = {
    "p1_1_factuality_golden": "Factuality golden set",
    "p1_3_consistency_golden": "Consistency under paraphrase",
    "p1_4_refusal_golden": "Refusal accuracy",
    "p1_5_calibration_golden": "Confidence calibration golden set",
}
CORE_FAMILY_COUNTS = {
    "p1_1_factuality_golden": 30,
    "p1_3_consistency_golden": 20,
    "p1_4_refusal_golden": 20,
    "p1_5_calibration_golden": 20,
}
TEXT_FAMILY_COUNTS = {
    "advbench": 1,
    "backbond-output-attack-fixtures": 4,
    "citation_integrity_legal": 4,
    "confidence_calibration": 1,
    "crescendo": 4,
    "factscore": 2,
    "garak": 9,
    "garak_beaaa152": 18,
    "hallucination_legal": 4,
    "halueval": 5,
    "harmbench": 7,
    "jailbreakbench": 8,
    "p3_2_indirect_injection_rob": 12,
    "p3_3_secret_exfiltration_rob": 12,
    "perturbation_seeds": 1,
    "privacybench": 5,
    "promptbench": 7,
    "promptinject": 2,
    "pyrit": 5,
    "truthfulqa": 2,
}
EXPECTED_FAMILY_COUNTS = {**CORE_FAMILY_COUNTS, **TEXT_FAMILY_COUNTS}
PLANNED_TARGET_CALLS = 246
PLANNED_JUDGE_CALLS = 203


@dataclass(frozen=True)
class RealScanConfig:
    agent_endpoint: str = "https://apichat.syllable.cloud"
    agent_secret: str = ""
    agent_id: str = "syllable-insurance-demo"
    scanner_version: str = "0.4.2"
    corpus_version: str = "execution-registry@2026-07-14"
    seed: int = 42
    max_target_calls: int = PLANNED_TARGET_CALLS
    max_judge_calls: int = PLANNED_JUDGE_CALLS
    max_estimated_judge_cost_usd: float = 100.0
    max_wall_clock_seconds: int = 8 * 60 * 60

    @classmethod
    def from_env(cls) -> "RealScanConfig":
        return cls(
            agent_endpoint=os.environ.get(
                "DEMO_AGENT_ENDPOINT", "https://apichat.syllable.cloud"
            ).rstrip("/"),
            agent_secret=os.environ.get("DEMO_AGENT_SECRET", ""),
            agent_id=os.environ.get("DEMO_AGENT_ID", "syllable-insurance-demo"),
            scanner_version=os.environ.get("DEMO_SCANNER_VERSION", "0.4.2"),
            corpus_version=os.environ.get(
                "DEMO_CORPUS_VERSION", "execution-registry@2026-07-14"
            ),
            seed=int(os.environ.get("DEMO_SCAN_SEED", "42")),
            max_target_calls=int(
                os.environ.get("ARE_FULL_SCAN_MAX_TARGET_CALLS", str(PLANNED_TARGET_CALLS))
            ),
            max_judge_calls=int(
                os.environ.get("ARE_FULL_SCAN_MAX_JUDGE_CALLS", str(PLANNED_JUDGE_CALLS))
            ),
            max_estimated_judge_cost_usd=float(
                os.environ.get("ARE_FULL_SCAN_MAX_JUDGE_COST_USD", "100")
            ),
            max_wall_clock_seconds=int(
                os.environ.get("ARE_FULL_SCAN_MAX_WALL_CLOCK_SECONDS", "28800")
            ),
        )

    def budget(self) -> ScanBudget:
        return ScanBudget(
            max_target_calls=self.max_target_calls,
            max_judge_calls=self.max_judge_calls,
            max_estimated_judge_cost_usd=self.max_estimated_judge_cost_usd,
            max_wall_clock_seconds=self.max_wall_clock_seconds,
            judge_input_usd_per_million_tokens=float(
                os.environ.get("ARE_FULL_SCAN_JUDGE_INPUT_USD_PER_MILLION", "3")
            ),
            judge_output_usd_per_million_tokens=float(
                os.environ.get("ARE_FULL_SCAN_JUDGE_OUTPUT_USD_PER_MILLION", "15")
            ),
            judge_max_output_tokens_per_call=int(
                os.environ.get("ARE_FULL_SCAN_JUDGE_MAX_OUTPUT_TOKENS", "256")
            ),
        )


def _family_label(family_id: str) -> str:
    return FAMILY_LABELS.get(
        family_id,
        family_id.replace("-", " ").replace("_", " ").title(),
    )


def _planned_calls_for_row(row: Mapping[str, Any]) -> tuple[int, int]:
    family_id = str(row.get("test_id") or "")
    if family_id == "p1_3_consistency_golden":
        return 2, 1
    if family_id in CORE_FAMILY_COUNTS:
        return 1, 1
    spec = materialize_text_case(
        family_id,
        str(row.get("case_id") or ""),
        row,
    )
    return len(spec.prompts), 1


def registry_inventory() -> dict[str, Any]:
    rows = build_registry()
    published = load_published_count()
    errors = validate_registry(rows, published)
    if errors:
        raise RuntimeError("active registry invalid: " + "; ".join(errors))

    evidenced_executable = count_active_executable(rows)
    if evidenced_executable != published:
        raise RuntimeError(
            "published executed count does not equal evidence-gated registry count"
        )

    # This historical demo executes the pre-content-inventory 203-case plan.
    # Validate the complete production registry above, but build the demo plan
    # from the compatibility view so newly registered content candidates do not
    # silently expand its fixed call and cost budget.
    compatibility_rows = build_registry(include_content_candidates=False)
    runnable = [
        {key: value for key, value in row.items() if key != "runtime_case_identity"}
        for row in compatibility_rows
        if row.get("status") == "active"
        and row.get("target_bound") is True
        and str(row.get("test_id") or "") in EXPECTED_FAMILY_COUNTS
    ]
    by_family = Counter(str(row["test_id"]) for row in runnable)
    if len(runnable) != 203 or dict(by_family) != EXPECTED_FAMILY_COUNTS:
        raise RuntimeError(
            "real demo is pinned to the 203-case target-bound execution plan; "
            f"observed {len(runnable)} cases across {dict(sorted(by_family.items()))}"
        )

    calls_by_family: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in runnable:
        family_id = str(row["test_id"])
        target_calls, judge_calls = _planned_calls_for_row(row)
        calls_by_family[family_id][0] += target_calls
        calls_by_family[family_id][1] += judge_calls

    planned_target_calls = sum(value[0] for value in calls_by_family.values())
    planned_judge_calls = sum(value[1] for value in calls_by_family.values())
    if (
        planned_target_calls != PLANNED_TARGET_CALLS
        or planned_judge_calls != PLANNED_JUDGE_CALLS
    ):
        raise RuntimeError(
            "203-case call plan drifted: "
            f"{planned_target_calls} target / {planned_judge_calls} judge"
        )

    # Distinct-pair coverage (BEAAA-14423 / F10): several text-compatible rows
    # materialize to byte-identical (prompts, expected) payloads via
    # ``_stable_select`` hash-collisions. All 203 rows still execute, but they do
    # not all represent independent evidence — coverage keys off distinct pairs,
    # never the raw row count. Surface it as evidence and WARN on collapse.
    distinct = distinct_case_counts(rows)
    collapses = distinct_case_collapses(rows)
    if collapses or distinct["cross_family_collapsed"] > 0:
        LOGGER.warning(
            "distinct-pair coverage collapse: %d runnable rows -> %d distinct "
            "(prompts, expected) pairs (-%d byte-identical: -%d within-family, "
            "-%d shared across families); within-family table:\n%s",
            distinct["declared_total"],
            distinct["total"],
            distinct["declared_total"] - distinct["total"],
            sum(c["collapsed"] for c in collapses),
            distinct["cross_family_collapsed"],
            format_collapse_table(collapses),
        )
    distinct_by_family = distinct["by_family"]

    return {
        "rows": rows,
        "runnable_rows": runnable,
        "runnable_case_count": len(runnable),
        "distinct_case_count": distinct["total"],
        "distinct_by_family": distinct_by_family,
        "distinct_coverage_collapses": collapses,
        "published_executed_case_count": published,
        "evidenced_executable_case_count": evidenced_executable,
        "planned_target_calls": planned_target_calls,
        "planned_judge_calls": planned_judge_calls,
        "families": [
            {
                "family_id": family_id,
                "label": _family_label(family_id),
                "cases": count,
                "distinct_cases": distinct_by_family.get(family_id, count),
                "planned_target_calls": calls_by_family[family_id][0],
                "planned_judge_calls": calls_by_family[family_id][1],
            }
            for family_id, count in sorted(by_family.items())
        ],
    }


def _claude_proxy_enabled() -> bool:
    return os.environ.get("DEMO_JUDGE_BACKEND") == "claude-code-proxy"


def _claude_proxy_runner() -> ClaudeCodeProxyRunner:
    return ClaudeCodeProxyRunner(
        socket_path=os.environ.get("CLAUDE_JUDGE_SOCKET", DEFAULT_SOCKET_PATH),
        token=os.environ.get("CLAUDE_JUDGE_PROXY_TOKEN", ""),
        model=os.environ.get("CLAUDE_JUDGE_MODEL", "sonnet"),
        timeout_seconds=float(os.environ.get("CLAUDE_JUDGE_TIMEOUT_SECONDS", "120")),
    )


def judge_configured() -> bool:
    if os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if not _claude_proxy_enabled():
        return False
    token = os.environ.get("CLAUDE_JUDGE_PROXY_TOKEN", "")
    socket_path = Path(
        os.environ.get("CLAUDE_JUDGE_SOCKET", DEFAULT_SOCKET_PATH)
    )
    return len(token) >= 32 and socket_path.exists()


def validate_public_configuration(config: RealScanConfig) -> None:
    parsed = urlsplit(config.agent_endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("DEMO_AGENT_ENDPOINT must be a valid HTTPS endpoint")
    if parsed.hostname != "apichat.syllable.cloud" and not config.agent_secret:
        raise RuntimeError("non-Syllable demo target requires DEMO_AGENT_SECRET")
    if not judge_configured():
        raise RuntimeError(
            "real demo requires an API judge credential or ready Claude Max bridge"
        )
    if config.max_estimated_judge_cost_usd > 100.0:
        raise RuntimeError("judge budget may not exceed the approved $100 ceiling")
    if (
        config.max_target_calls != PLANNED_TARGET_CALLS
        or config.max_judge_calls != PLANNED_JUDGE_CALLS
    ):
        raise RuntimeError(
            "public demo budget must be exactly "
            f"{PLANNED_TARGET_CALLS} target / {PLANNED_JUDGE_CALLS} judge calls"
        )
    registry_inventory()


def execute_real_scan(
    output_dir: Path,
    run_id: str,
    emit: Emit,
    *,
    config: RealScanConfig | None = None,
    subject_factory: SubjectFactory | None = None,
    judge_factory: JudgeFactory | None = None,
) -> dict[str, Any]:
    """Execute the pinned target-bound registry plan and return a public-safe summary."""
    config = config or RealScanConfig.from_env()
    inventory = registry_inventory()
    runnable_rows = inventory["runnable_rows"]
    output_dir.mkdir(parents=True, exist_ok=False)

    emit(
        "registry",
        f"Execution plan locked: {len(runnable_rows)} target-bound cases",
        stage="registry",
        runnable_cases=len(runnable_rows),
        planned_target_calls=inventory["planned_target_calls"],
        planned_judge_calls=inventory["planned_judge_calls"],
        families=inventory["families"],
    )
    emit(
        "stage",
        "Real authenticated-agent evaluation started",
        stage="scan",
        state="running",
    )

    request = SimpleNamespace(agent_endpoint=config.agent_endpoint)
    if judge_factory is None and _claude_proxy_enabled():
        judge_factory = _claude_proxy_runner
    workers = build_registered_case_workers(
        request,
        config.agent_secret,
        budget=config.budget(),
        evidence_root=output_dir / "evidence",
        subject_factory=subject_factory,
        judge_factory=judge_factory,
    )
    first_worker = next(iter(workers.values()))
    metadata = first_worker.manifest_metadata()
    completed = {"count": 0}

    def wrap_worker(worker: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(row: Mapping[str, Any], job: Any) -> Any:
            result = worker(row, job)
            completed["count"] += 1
            family_id = str(row.get("test_id") or "")
            emit(
                "case_result",
                f"{row.get('case_id')}: {result.verdict or result.status}",
                stage="scan",
                completed=completed["count"],
                total=len(runnable_rows),
                case_id=str(row.get("case_id") or ""),
                family_id=family_id,
                family=_family_label(family_id),
                execution_status=result.status,
                verdict=result.verdict or "unresolved",
                target_calls=result.target_calls,
                judge_calls=result.judge_calls,
                estimated_judge_cost_usd=result.estimated_judge_cost_usd,
                duration_ms=result.duration_ms,
                incomplete_reason=result.incomplete_reason,
                evidence_ref=result.evidence_ref,
            )
            return result

        return wrapped

    wrapped_workers = {
        worker_id: wrap_worker(worker) for worker_id, worker in workers.items()
    }
    store = FullScanJobStore()
    job = store.submit(
        agent_id=config.agent_id,
        scanner_version=config.scanner_version,
        corpus_version=config.corpus_version,
        model_versions={
            "primary": metadata["target_model"],
            "judges": metadata["judge_models"],
            "execution_budget": metadata["execution_budget"],
        },
        seed=config.seed,
        registry_cases=registry_cases_from_rows(
            runnable_rows, execution_flag="target_bound"
        ),
        idempotency_key=f"demo:{run_id}:real-203",
    )
    lease = store.lease(job.job_id, ttl_seconds=config.max_wall_clock_seconds)
    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=str(lease["attempt_id"]),
        registry_rows=runnable_rows,
        workers=wrapped_workers,
        execution_flag="target_bound",
    )
    finalized = store.finalize(job.job_id)
    manifest = dict(finalized.final_manifest or {})
    first_worker.write_manifest(manifest)

    counts = manifest.get("counts", {})
    reconciliation = manifest.get("reconciliation", {})
    expected = len(runnable_rows)
    reconciled = (
        int(counts.get("expected") or 0),
        int(reconciliation.get("attempted") or 0),
        int(reconciliation.get("terminal") or 0),
        int(counts.get("evidence_rows") or 0),
    )
    if reconciled != (expected, expected, expected, expected):
        raise RuntimeError(
            "manifest reconciliation mismatch: "
            f"expected/attempted/terminal/evidenced={reconciled}"
        )

    row_by_case = {str(row["case_id"]): row for row in runnable_rows}
    cases: list[dict[str, Any]] = []
    family_rollups: dict[str, Counter[str]] = defaultdict(Counter)
    for evidence in manifest.get("evidence_rows", []):
        case_id = str(evidence.get("probe_id") or "")
        registry_row = row_by_case.get(case_id, {})
        family_id = str(
            registry_row.get("test_id") or evidence.get("test_id") or ""
        )
        verdict = str(evidence.get("verdict") or "unresolved")
        family_rollups[family_id][verdict] += 1
        cases.append(
            {
                "case_id": case_id,
                "family_id": family_id,
                "family": _family_label(family_id),
                "rubric_point_id": evidence.get("rubric_point_id"),
                "execution_status": evidence.get("status"),
                "verdict": verdict,
                "target_calls": evidence.get("target_calls", 0),
                "judge_calls": evidence.get("judge_calls", 0),
                "estimated_judge_cost_usd": evidence.get(
                    "estimated_judge_cost_usd", 0.0
                ),
                "duration_ms": evidence.get("duration_ms", 0),
                "incomplete_reason": evidence.get("incomplete_reason"),
                "evidence_ref": evidence.get("evidence_ref"),
            }
        )

    families = []
    for item in inventory["families"]:
        rollup = family_rollups[item["family_id"]]
        families.append(
            {
                **item,
                "pass": rollup["pass"],
                "fail": rollup["fail"],
                "partial": rollup["partial"],
                "unresolved": rollup["unresolved"],
            }
        )

    summary = {
        "run_id": run_id,
        "job_id": finalized.job_id,
        "analysis_status": manifest.get("analysis_status"),
        "runnable_case_count": expected,
        "target_host": urlsplit(config.agent_endpoint).hostname,
        "target_model": metadata["target_model"],
        "judge_models": metadata["judge_models"],
        "seed": config.seed,
        "families": families,
        "cases": cases,
        "verdict_counts": manifest.get("verdict_counts", {}),
        "reconciliation": reconciliation,
        "counts": counts,
        "budget": manifest.get("budget", {}),
        "incomplete": manifest.get("incomplete", False),
        "incomplete_reasons": manifest.get("incomplete_reasons", []),
        "registry_snapshot_hash": manifest.get("registry_snapshot_hash"),
        "manifest_hash": manifest.get("manifest_hash"),
        "manifest_evidence_ref": manifest.get("manifest_evidence_ref"),
        "finished_at": manifest.get("finished_at"),
        "score": None,
        "score_note": (
            "No aggregate BackBond score is synthesized from this 203-case execution set. "
            "The measured output is the per-case verdict manifest."
        ),
        "approved_budget": asdict(config.budget()),
    }
    emit(
        "stage",
        (
            f"Evaluation finished: {expected} attempted, "
            f"{summary['verdict_counts'].get('fail', 0)} behavioral failures"
        ),
        stage="scan",
        state="passed" if not summary["incomplete"] else "incomplete",
        summary={
            "runnable_case_count": expected,
            "verdict_counts": summary["verdict_counts"],
            "budget": summary["budget"],
            "analysis_status": summary["analysis_status"],
        },
    )
    return summary


__all__ = [
    "CORE_FAMILY_COUNTS",
    "EXPECTED_FAMILY_COUNTS",
    "FAMILY_LABELS",
    "PLANNED_JUDGE_CALLS",
    "PLANNED_TARGET_CALLS",
    "TEXT_FAMILY_COUNTS",
    "RealScanConfig",
    "execute_real_scan",
    "judge_configured",
    "registry_inventory",
    "validate_public_configuration",
]
