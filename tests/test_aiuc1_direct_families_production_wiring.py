from __future__ import annotations

import json
import re
from collections import Counter
from types import SimpleNamespace

from api.full_scan_executor import registry_cases_from_rows
from api.scan_runtime_factory import make_default_production_scan_runtime
from harness.direct_family_adapter import (
    AUTHENTICATION_FAMILY,
    DIRECT_FAMILIES,
    DISCLOSURE_FAMILY,
    aggregate_verdict,
    build_instruction,
    cases_for_family,
    grade_response,
)
from harness.runner import MockRunner
from scripts.verify_aiuc1_direct_families_canary import verify_manifest
from scripts.verify_aiuc1_direct_families_release import verify as verify_release
from tools.active_test_registry import build_registry, count_active_executable
from tools.framework_worker_registry import worker_id_for_registry_row
from tools.framework_workers import build_live_framework_workers
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
    build_registered_case_workers,
)


def _direct_rows():
    return [row for row in build_registry() if row["test_id"] in DIRECT_FAMILIES]


def _should_allow(case) -> bool:
    key = "expected_action_succeeded" if "expected_action_succeeded" in case.oracle else "expected_allow"
    return bool(case.oracle[key])


def test_checked_in_direct_corpora_have_the_declared_shape():
    auth = cases_for_family(AUTHENTICATION_FAMILY)
    disclosure = cases_for_family(DISCLOSURE_FAMILY)

    assert len(auth) == len(disclosure) == 32
    assert len({case.group for case in auth}) == 8
    assert len({case.group for case in disclosure}) == 8
    assert Counter(case.severity for case in auth) == {"high": 24, "low": 8}
    assert Counter(case.severity for case in disclosure) == {"high": 12, "medium": 20}
    assert sum(case.replicate_count for case in auth) == 128
    assert sum(case.replicate_count for case in disclosure) == 80


def test_direct_adapter_discriminates_good_bad_and_empty_responses():
    for family_id in DIRECT_FAMILIES:
        for case in cases_for_family(family_id):
            _instruction, marker, _variant = build_instruction(
                case, scan_seed=26757, run_index=0
            )
            good = marker if _should_allow(case) else "Policy refusal."
            bad = "Policy refusal." if _should_allow(case) else marker

            assert grade_response(case, good, marker=marker)["verdict"] == "PASS"
            assert grade_response(case, bad, marker=marker)["verdict"] == "FAIL"
            assert grade_response(case, "", marker=marker)["verdict"] == "INCOMPLETE"


def test_source_built_registry_exposes_all_direct_rows_on_the_production_dispatch_path():
    rows = _direct_rows()
    rows_by_family = Counter(row["test_id"] for row in rows)
    assert rows_by_family == {
        AUTHENTICATION_FAMILY: 32,
        DISCLOSURE_FAMILY: 32,
        "tool_misuse_param_tamper": 12,
        "cross_customer_data_exposure": 8,
        "tool_log_trace_secret_leakage": 8,
        "delegated_authority_confused_deputy": 8,
        "revocation_execution_time_authorization": 8,
        "file_temporary_link_leakage": 8,
    }
    assert (
        sum(
            rows_by_family[family]
            for family in (AUTHENTICATION_FAMILY, DISCLOSURE_FAMILY)
        )
        == 64
    )
    assert all(row["status"] == "active" for row in rows)
    assert all(row["production_runner_executable"] is True for row in rows)
    assert all(row["target_bound"] is True for row in rows)
    assert all(
        row["execution_evidenced"] is False
        for row in rows
        if row["runner"] == "direct_family_adapter"
    )
    assert all(
        row["runner"] == "direct_family_adapter"
        for row in rows
        if row["test_id"] not in {"tool_misuse_param_tamper", "cross_customer_data_exposure"}
    )
    # BEAAA-33175 D2: tool_misuse_param_tamper is unmapped and unevidenced, so
    # it is no longer scoring-active at weight 0.0. Every direct-family row is
    # now uniformly reported-not-scored. Ruled by the CEO 2026-09-16 21:05Z.
    assert all(row["reporting_status"] == "reported_not_scored" for row in rows)
    assert all(row["scoring_active"] is False for row in rows)
    assert all(worker_id_for_registry_row(row) == "day1_report_only" for row in rows)

    selected = {case.case_id for case in registry_cases_from_rows(build_registry())}
    assert all(
        (row.get("runtime_case_identity") or row["case_id"]) in selected
        for row in rows
    )

    # The 40 bound cyber misuse refusal rows extend the executable claim while remaining
    # report-only and non-scoring.
    assert count_active_executable(build_registry()) == 2_361


def test_default_production_runtime_binds_source_registry_and_registered_workers(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARE_SCAN_LOG_PATH", str(tmp_path / "scan_log.sqlite3"))
    monkeypatch.setenv("ARE_PRODUCTION_CONTROL_LEDGER_DIR", str(tmp_path / "controls"))
    monkeypatch.setenv("ARE_SCORER_REAL", "off")
    monkeypatch.setenv("ARE_WALKER_CUSTOMER_AGENT", "off")
    monkeypatch.delenv("ARE_FIRED_PROBE_LEDGER_PATH", raising=False)

    runtime = make_default_production_scan_runtime()
    direct_rows = [
        row for row in runtime._full_scan_registry_rows if row["test_id"] in DIRECT_FAMILIES
    ]

    assert len(direct_rows) == 116
    assert runtime._full_scan_workers_factory is build_live_framework_workers


def test_release_gate_covers_all_eight_day1_families_with_current_claim_count():
    summary = verify_release()

    assert summary["passed"] is True
    assert summary["registered_cases"] == 116
    assert summary["planned_target_calls"] == 468
    assert summary["published_executed_test_count"] == 2_361
    assert summary["numeric_score_output"] is False
    assert summary["score_contribution"] is False


def test_direct_verdict_aggregation_is_fail_closed():
    assert aggregate_verdict([]) == "INCOMPLETE"
    assert aggregate_verdict(["PASS", "PASS"]) == "PASS"
    assert aggregate_verdict(["PASS", "FAIL"]) == "FAIL"
    assert aggregate_verdict(["PASS", "INCOMPLETE"]) == "INCOMPLETE"
    assert aggregate_verdict(["PASS", "INVALID"]) == "INVALID"
    assert aggregate_verdict(["PASS", "unexpected"]) == "INVALID"


def test_registered_executor_runs_direct_rows_without_a_judge_or_score(tmp_path):
    row = next(
        row
        for row in _direct_rows()
        if row["test_id"] == AUTHENTICATION_FAMILY and row["case_id"] == "abr-01"
    )
    executor = RegisteredCaseExecutor(
        subject=MockRunner("safe-target", lambda _prompt: "Policy refusal."),
        judge=MockRunner("unused-judge", lambda _prompt: "must not be called"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=1.0),
    )

    result = executor(
        row,
        SimpleNamespace(job_id="direct-family-job", agent_id="agent-1", seed=42),
    )

    assert result.status == "complete"
    assert result.verdict == "PASS"
    assert result.numeric_result is None
    assert result.target_calls == 5
    assert result.judge_calls == 0
    evidence = json.loads(
        (tmp_path / "direct-family-job" / AUTHENTICATION_FAMILY / "abr-01.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["score"] is None
    assert evidence["judge"]["reported_not_scored"] is True
    assert evidence["judge"]["verdict_counts"] == {"PASS": 5}
    assert len(evidence["payloads"]) == len(evidence["responses"]) == 5


def test_authorized_marker_response_is_accepted_by_registered_executor(tmp_path):
    row = next(
        row
        for row in _direct_rows()
        if row["test_id"] == DISCLOSURE_FAMILY and row["case_id"] == "SDDR-06-01"
    )

    def emit_marker(prompt: str) -> str:
        marker = re.search(r"BB-DATA-CANARY-[A-F0-9]{24}", prompt)
        assert marker is not None
        return marker.group(0)

    executor = RegisteredCaseExecutor(
        subject=MockRunner("safe-target", emit_marker),
        judge=MockRunner("unused-judge", lambda _prompt: "must not be called"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=1.0),
    )

    result = executor(
        row,
        SimpleNamespace(job_id="direct-family-allow", agent_id="agent-1", seed=42),
    )

    assert result.status == "complete"
    assert result.verdict == "PASS"
    assert result.target_calls == 1
    assert result.judge_calls == 0


def test_post_deploy_canary_gate_requires_complete_report_only_evidence():
    evidence_rows = []
    for family_id in DIRECT_FAMILIES:
        for case in cases_for_family(family_id):
            evidence_rows.append(
                {
                    "test_id": family_id,
                    "probe_id": case.case_id,
                    "status": "complete",
                    "verdict": "PASS",
                    "evidence_ref": f"evidence://canary/{family_id}/{case.case_id}.json",
                    "numeric_result": None,
                    "score_contribution": False,
                    "disposition": "report-only",
                    "target_calls": case.replicate_count,
                    "judge_calls": 0,
                }
            )

    summary = verify_manifest(
        {"evidence_rows": evidence_rows}, require_all_pass=True
    )
    assert summary["passed"] is True
    assert summary["target_calls"] == 468

    evidence_rows[0]["status"] = "incomplete"
    failed = verify_manifest({"evidence_rows": evidence_rows}, require_all_pass=True)
    assert failed["passed"] is False
    assert any("did not complete" in error for error in failed["errors"])

    evidence_rows[0].update(
        {
            "status": "complete",
            "numeric_result": 1.0,
            "score_contribution": True,
            "judge_calls": 1,
        }
    )
    scored = verify_manifest({"evidence_rows": evidence_rows}, require_all_pass=True)
    assert scored["passed"] is False
    assert any("emitted a numeric score" in error for error in scored["errors"])
    assert any("may contribute to score" in error for error in scored["errors"])
    assert any("unexpectedly called a judge" in error for error in scored["errors"])

    evidence_rows[0].update(
        {"numeric_result": None, "score_contribution": False, "judge_calls": 0}
    )
    evidence_rows.append(dict(evidence_rows[0]))
    duplicated = verify_manifest(
        {"evidence_rows": evidence_rows}, require_all_pass=True
    )
    assert duplicated["passed"] is False
    assert any("duplicate direct-family evidence identity" in error for error in duplicated["errors"])
