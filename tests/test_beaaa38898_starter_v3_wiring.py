"""BEAAA-38898 — starter-100@3 wiring: intake default, walker branch, judge
rubric, Starter score pack id, report files. @1 and @2 keep working."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.scan_handlers import ScanRuntime, _starter_score_fields, scan_handler
from scanners import customer_agent_walker as walker
from scanners.live_probe_orchestrator import ScanResult
from scanners.starter_judge import judge_starter_case, starter_pack_id_for_rows
from scanners.starter_pack import STARTER_PACK_ID
from scanners.starter_pack_v4 import STARTER_PACK_V4_ID
from scanners.starter_pack_v2 import STARTER_PACK_V2_ID, STARTER_V2_CASES
from scanners.starter_pack_v3 import (
    ANSWERS_NORMAL_REQUESTS,
    STARTER_PACK_V3_ID,
    STARTER_V3_CASES,
    STARTER_V3_RUBRICS,
)

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "backbond/operations/site-quickfix/release-2026-10-07-starter-v2"


def _submit(monkeypatch, pack_id):
    monkeypatch.setenv("ARE_SCAN_TIER", "on")
    monkeypatch.delenv("BACKBOND_STARTER_PACK_LEGACY_V1", raising=False)
    captured = {}

    def run_scan(request, secret):
        captured["request"] = request
        return ScanResult(
            scan_id=request.scan_id,
            status="complete",
            scanner_version=request.scanner_version,
            corpus_version=request.corpus_version,
            rows_emitted=100,
            k_replicates=request.k_replicates,
        )

    body = {
        "tenant_id": "tenant-001",
        "agent_endpoint": {"host": "agent.example.test"},
        "credential_ref": "partner.qa",
        "credential_secret": "ephemeral",
        "scanner_version": "0.5.0",
        "pack_id": pack_id,
        "scan_mode": "live_probe",
    }
    status, payload = scan_handler(
        body,
        tenant_header="tenant-001",
        runtime=ScanRuntime(run_scan_fn=run_scan),
        scan_id_factory=lambda: "starter-scan",
    )
    return status, payload, captured["request"]


@pytest.mark.parametrize(
    "sent, runs",
    [
        (STARTER_PACK_ID, STARTER_PACK_ID),
        (STARTER_PACK_V3_ID, STARTER_PACK_V3_ID),
        (STARTER_PACK_V2_ID, STARTER_PACK_V2_ID),
    ],
)
def test_intake_pack_selection(monkeypatch, sent, runs):
    status, payload, request = _submit(monkeypatch, sent)
    assert status == 202
    assert payload["pack_id"] == runs and request.pack_id == runs
    assert payload["test_count"] == 100


def test_legacy_flag_still_serves_v1(monkeypatch):
    monkeypatch.setenv("BACKBOND_STARTER_PACK_LEGACY_V1", "1")
    from api.scan_handlers import _effective_starter_pack_id

    assert _effective_starter_pack_id(STARTER_PACK_ID) == STARTER_PACK_ID


class _Judge:
    def __init__(self):
        self.users = []

    def complete(self, *, system, user):
        self.users.append(user)
        return '{"verdict": "failed", "reason": "it refused a normal request"}'


class _Runner:
    pack_id = STARTER_PACK_V3_ID

    def __init__(self):
        self.prompts, self.rows = [], []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return "I cannot take actions, disclose secrets, or verify external facts. Please ask an authorized person."

    def record_starter_verdict(self, *, test_id, family, verdict, reason=""):
        self.rows.append({"test_id": test_id, "family": family, "verdict": verdict, "reason": reason})


def test_walker_runs_v3_cases(monkeypatch):
    judge = _Judge()
    monkeypatch.setattr(walker, "judge_client_from_env", lambda: judge)
    runner = _Runner()
    out = list(walker.customer_agent_probe_run_fn(runner))
    assert len(out) == 100
    assert runner.prompts == [c.prompt for c in STARTER_V3_CASES]
    families = {r["family"] for r in runner.rows}
    assert ANSWERS_NORMAL_REQUESTS in families and "unsafe_output" not in families
    assert all("starter-100@3" in o[1]["evidence_uri"] for o in out)
    # The answers family is judged with its own rubric.
    ans_index = next(i for i, c in enumerate(STARTER_V3_CASES) if c.family_id == ANSWERS_NORMAL_REQUESTS)
    assert STARTER_V3_RUBRICS[ANSWERS_NORMAL_REQUESTS] in judge.users[ans_index]
    assert _starter_score_fields(runner.rows)["starter_pack_id"] == STARTER_PACK_V3_ID


def test_refusal_on_normal_request_is_judged_not_short_circuited():
    case = next(c for c in STARTER_V3_CASES if c.family_id == ANSWERS_NORMAL_REQUESTS)
    judge = _Judge()
    verdict, _ = judge_starter_case(case, "Please ask an authorized person.", judge_client=judge)
    assert verdict == "failed" and len(judge.users) == 1


def test_pack_id_for_stored_rows():
    v2_rows = [{"family": c.family_id, "verdict": "passed", "reason": "x"} for c in STARTER_V2_CASES]
    v3_rows = [{"family": c.family_id, "verdict": "passed", "reason": "x"} for c in STARTER_V3_CASES]
    assert starter_pack_id_for_rows(v2_rows) == STARTER_PACK_V2_ID
    assert starter_pack_id_for_rows(v3_rows) == STARTER_PACK_V3_ID
    assert _starter_score_fields(v2_rows)["starter_pack_id"] == STARTER_PACK_V2_ID
    # @1 rows carry no reason and get no score fields.
    assert _starter_score_fields([{"family": "p1_1_factuality_golden", "verdict": "passed"}]) == {}


def test_customer_payload_names_v3():
    from api.scan_response_envelopes import customer_payload

    result = ScanResult(
        scan_id="s", status="complete", scanner_version="v", corpus_version="c",
        rows_emitted=100, k_replicates=1,
    )
    rows = tuple({"test_id": c.case_id, "family": c.family_id, "verdict": "passed", "reason": "ok"}
                 for c in STARTER_V3_CASES)
    object.__setattr__(result, "starter_result_rows", rows)
    payload = customer_payload(result)
    assert payload["starter_pack_id"] == STARTER_PACK_V3_ID
    assert payload["starter_score"]["score"] == 100.0


def test_v3_gets_judged_deadline_and_single_pass():
    from scanners import live_probe_orchestrator as orch

    assert STARTER_PACK_V3_ID in orch._JUDGED_STARTER_PACK_IDS
    assert STARTER_PACK_V2_ID in orch._JUDGED_STARTER_PACK_IDS


def test_report_files_render_v3_and_old_versions():
    report = (SITE / "site/scan/report.js").read_text()
    for pack in ("'starter-100@1'", "'starter-100@2'", "'starter-100@3'"):
        assert pack in report
    assert "answers_normal_requests:'Answers normal requests'" in report
    assert "answers_normal_requests: ['" in report
    assert 'answers_normal_requests: "Answers normal requests"' in (SITE / "starter-report.js").read_text()
