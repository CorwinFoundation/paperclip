from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.batch_b_family_target_runner import (
    BatchBRunError,
    BatchBTargetRunner,
    DevelopmentTarget,
    FAMILIES,
)

APPLICABLE_CAPABILITIES = {
    "produces_recommendations": True,
    "produces_eligibility_outcomes": True,
    "produces_routing_or_prioritization": True,
    "produces_refusal_allow_decisions": True,
    "has_tool_calling": True,
    "has_rag_retrieval": True,
    "has_document_attachments": True,
    "has_inline_html_rendering": True,
    "produces_summary": True,
    "produces_recommendation": True,
    "produces_rationale": True,
    "produces_refusal_text": True,
    "produces_qualified_positive_evaluations": True,
    "operator_evidence_uploaded": True,
    "scanner_import_control_artifacts": True,
    "binds_fairness_eval_set": True,
    "imports_scanner_fairness_corpus": True,
    "has_system_prompt_in_response_channel": True,
    "has_credentials_in_prompt": True,
    "has_honeytoken_canary_planted": True,
    "has_multi_tenant_context": True,
    "produces_freeform_rationale": True,
}


class _GoodTarget(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        payload = json.dumps({"label": body["expected_label"]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def target_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GoodTarget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/complete"
    finally:
        server.shutdown()


def test_ordinals_25_32_execute_all_96_cases_deterministically(
    target_url: str, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BACKBOND_BATCH_B_TEST_TOKEN", "test-only")
    config = DevelopmentTarget(
        target_url,
        "known-good-development-fixture",
        "BACKBOND_BATCH_B_TEST_TOKEN",
        capability_evidence=APPLICABLE_CAPABILITIES,
    )
    first = BatchBTargetRunner(config, tmp_path / "a")
    second = BatchBTargetRunner(config, tmp_path / "b")
    first_results = [first.run_family(family) for family in FAMILIES]
    second_results = [second.run_family(family) for family in FAMILIES]
    assert first.calls == second.calls == 96
    assert first.spend_usd == second.spend_usd == 0.0
    assert [r["raw"] for r in first_results] == [4.0] * 8
    assert [r["verdict"] for r in first_results] == ["pass"] * 8
    assert [
        Path(r["evidence"]["path"]).read_bytes() for r in first_results
    ] == [Path(r["evidence"]["path"]).read_bytes() for r in second_results]


def test_missing_test_credential_fails_closed(target_url: str, tmp_path: Path) -> None:
    runner = BatchBTargetRunner(
        DevelopmentTarget(
            target_url,
            "fixture",
            "BACKBOND_MISSING_TEST_TOKEN",
            capability_evidence=APPLICABLE_CAPABILITIES,
        ),
        tmp_path,
    )
    with pytest.raises(BatchBRunError, match="credential"):
        runner.run_family(FAMILIES[0])


def test_call_cap_fails_before_target_execution(
    target_url: str, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BACKBOND_BATCH_B_TEST_TOKEN", "test-only")
    runner = BatchBTargetRunner(
        DevelopmentTarget(
            target_url,
            "fixture",
            "BACKBOND_BATCH_B_TEST_TOKEN",
            call_cap=11,
            capability_evidence=APPLICABLE_CAPABILITIES,
        ),
        tmp_path,
    )
    with pytest.raises(BatchBRunError, match="call cap"):
        runner.run_family(FAMILIES[0])


def test_missing_capability_evidence_fails_closed(
    target_url: str, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BACKBOND_BATCH_B_TEST_TOKEN", "test-only")
    runner = BatchBTargetRunner(
        DevelopmentTarget(target_url, "fixture", "BACKBOND_BATCH_B_TEST_TOKEN"),
        tmp_path,
    )
    with pytest.raises(BatchBRunError, match="capability evidence"):
        runner.run_family(FAMILIES[0])


def test_proven_non_applicable_emits_frozen_na_without_calls(
    target_url: str, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BACKBOND_BATCH_B_TEST_TOKEN", "test-only")
    runner = BatchBTargetRunner(
        DevelopmentTarget(
            target_url,
            "fixture",
            "BACKBOND_BATCH_B_TEST_TOKEN",
            capability_evidence={key: False for key in APPLICABLE_CAPABILITIES},
        ),
        tmp_path,
    )
    result = runner.run_family("p2_1_demographic_parity_gap")
    assert result["verdict"] == "not_applicable"
    assert result["na_dim"] == "P2.1"
    assert result["na_justification_key"] == "P2.1.no_outcome_channel"
    assert runner.calls == 0
