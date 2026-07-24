from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.governance_eval_target_runner import (
    GOVERNANCE_EVAL_ORDINALS,
    REGISTRY_VERSION,
    GovernanceEvalRunError,
    GovernanceEvalTargetConfig,
    GovernanceEvalTargetRunner,
    load_governance_eval_rows,
    _terminal_verdict,
)


def _response_for(body: dict[str, object], verdict: str = "pass") -> bytes:
    family_id = str(body["family_id"])
    evidence: dict[str, object]
    if family_id == "eval_coverage_breadth":
        evidence = {
            "kind": "eval_coverage_breadth",
            "measured_dim_ids": ["P1.1", "P2.4"],
            "in_scope_dim_ids": ["P1.1", "P2.4", "P2.6"],
            "coverage_breadth_pct": 66.6667,
            "catalogue_presence_only": False,
        }
    elif family_id == "eval_representation":
        evidence = {
            "kind": "eval_representation",
            "axes": [
                {"axis": "age_signal", "total_count": 12, "status": "ok"},
                {"axis": "pronoun", "total_count": 12, "status": "ok"},
            ],
            "source_inventory_hash": "sha256:" + "1" * 64,
            "catalogue_presence_only": False,
        }
    else:
        evidence = {
            "kind": "mitigation_evidence",
            "per_class": {
                "fairness_review_process": {
                    "effective_tier": 2,
                    "weighted_raw": 1.25,
                }
            },
            "signed_attestation_hash": "sha256:" + "2" * 64,
            "catalogue_presence_only": False,
        }
    return json.dumps(
        {
            "schema_version": "backbond.governance-eval-response/v0.1",
            "family_id": body["family_id"],
            "registry_version": body["registry_version"],
            "rubric_point_id": body["rubric_point_id"],
            "source_file": body["source_file"],
            "customer_data": False,
            "score_activation": False,
            "verdict": verdict,
            "evidence": evidence,
        },
        sort_keys=True,
    ).encode()


class _GovernanceEvalTarget(BaseHTTPRequestHandler):
    mode = "pass"
    invocations: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.invocations.append(body)
        status = 200
        if self.mode == "pass":
            payload = _response_for(body, "pass")
        elif self.mode == "fail":
            payload = _response_for(body, "fail")
        elif self.mode == "missing_evidence":
            decoded = json.loads(_response_for(body, "pass"))
            decoded.pop("evidence")
            payload = json.dumps(decoded, sort_keys=True).encode()
        elif self.mode == "tamper":
            decoded = json.loads(_response_for(body, "pass"))
            decoded["registry_version"] = "tampered"
            payload = json.dumps(decoded, sort_keys=True).encode()
        else:
            payload = b"{not-json"
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def target_server():
    _GovernanceEvalTarget.invocations = []
    _GovernanceEvalTarget.mode = "pass"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GovernanceEvalTarget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()


def _runner(server: ThreadingHTTPServer, tmp_path: Path, monkeypatch) -> GovernanceEvalTargetRunner:
    monkeypatch.setenv("BACKBOND_GOV_EVAL_TEST_TOKEN", "test-token")
    return GovernanceEvalTargetRunner(
        GovernanceEvalTargetConfig(
            url=f"http://127.0.0.1:{server.server_port}/governance-eval",
            identity="pytest-non-customer-governance-eval-target",
            config_class="local_http_test_fixture",
            credential_env="BACKBOND_GOV_EVAL_TEST_TOKEN",
            call_cap=30,
            spend_cap_usd=5.0,
            cost_per_call_usd=0.0,
        ),
        tmp_path,
    )


def test_ordinals_10_11_20_are_discovered_and_invoked(target_server, tmp_path, monkeypatch) -> None:
    rows = load_governance_eval_rows()
    assert [row["ordinal"] for row in rows] == list(GOVERNANCE_EVAL_ORDINALS)
    assert [row["family_id"] for row in rows] == [
        "eval_coverage_breadth",
        "eval_representation",
        "mitigation_evidence",
    ]
    runner = _runner(target_server, tmp_path, monkeypatch)
    summary = runner.run_all(seed=0)

    assert summary["verdict"] == "PASS"
    assert summary["actual_calls"] == 3
    assert summary["actual_spend_usd"] == 0.0
    assert summary["customer_data"] is False
    assert summary["production_credentials"] is False
    assert [body["family_id"] for body in _GovernanceEvalTarget.invocations] == [
        row["family_id"] for row in rows
    ]
    assert all(body["registry_version"] == REGISTRY_VERSION for body in _GovernanceEvalTarget.invocations)
    assert all(body["evidence_request"]["catalogue_presence_alone_can_pass"] is False for body in _GovernanceEvalTarget.invocations)
    assert all(row["verdict"] == "PASS" for row in summary["families"])
    assert all(len(row["evidence"]["sha256"]) == 64 for row in summary["families"])
    assert all(len(value) == 64 for row in summary["families"] for value in row["hashes"].values())


def test_terminal_negative_verdict_is_fail_not_invalid(target_server, tmp_path, monkeypatch) -> None:
    _GovernanceEvalTarget.mode = "fail"
    result = _runner(target_server, tmp_path, monkeypatch).run_family(
        load_governance_eval_rows()[0]
    )
    assert result["verdict"] == "FAIL"
    assert result["target_verdict"] == "fail"


@pytest.mark.parametrize("mode", ["malformed", "missing_evidence", "tamper"])
def test_malformed_missing_evidence_and_tamper_are_invalid(
    mode, target_server, tmp_path, monkeypatch
) -> None:
    _GovernanceEvalTarget.mode = mode
    result = _runner(target_server, tmp_path, monkeypatch).run_family(
        load_governance_eval_rows()[0]
    )
    assert result["verdict"] == "INVALID"
    assert result["evidence"]["sha256"]


def test_runner_is_deterministic_for_same_seed(target_server, tmp_path, monkeypatch) -> None:
    runner = _runner(target_server, tmp_path / "a", monkeypatch)
    first = runner.run_family(load_governance_eval_rows()[1], seed=2)
    second_runner = _runner(target_server, tmp_path / "b", monkeypatch)
    second = second_runner.run_family(load_governance_eval_rows()[1], seed=2)
    assert first["hashes"] == second["hashes"]
    assert first["verdict"] == second["verdict"] == "PASS"


def test_runner_rejects_production_credential_markers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CUSTOMER_PROD_TOKEN", "unsafe")
    runner = GovernanceEvalTargetRunner(
        GovernanceEvalTargetConfig(
            "http://127.0.0.1",
            "x",
            "x",
            "CUSTOMER_PROD_TOKEN",
        ),
        tmp_path,
    )
    with pytest.raises(GovernanceEvalRunError, match="production/customer"):
        runner.run_family(load_governance_eval_rows()[0])


def _base_payload(row: dict[str, object], evidence: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "backbond.governance-eval-response/v0.1",
        "family_id": row["family_id"],
        "registry_version": REGISTRY_VERSION,
        "rubric_point_id": row["rubric_point_id"],
        "source_file": row["source_file"],
        "customer_data": False,
        "score_activation": False,
        "verdict": "pass",
        "evidence": evidence,
    }


def test_qa_repro_coverage_rejects_contradictory_percentage() -> None:
    row = load_governance_eval_rows()[0]
    payload = _base_payload(
        row,
        {
            "kind": "eval_coverage_breadth",
            "measured_dim_ids": ["P1.1"],
            "in_scope_dim_ids": ["P1.1", "P1.2"],
            "coverage_breadth_pct": 100,
            "catalogue_presence_only": False,
        },
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"


def test_qa_repro_coverage_rejects_negative_percentage() -> None:
    row = load_governance_eval_rows()[0]
    payload = _base_payload(
        row,
        {
            "kind": "eval_coverage_breadth",
            "measured_dim_ids": ["P1.1"],
            "in_scope_dim_ids": ["P1.1", "P1.2"],
            "coverage_breadth_pct": -500,
            "catalogue_presence_only": False,
        },
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"


def test_qa_repro_representation_rejects_negative_count_and_bad_hash() -> None:
    row = load_governance_eval_rows()[1]
    payload = _base_payload(
        row,
        {
            "kind": "eval_representation",
            "axes": [{"axis": "age_signal", "total_count": -7, "status": "ok"}],
            "source_inventory_hash": "not-a-hash",
            "catalogue_presence_only": False,
        },
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"


def test_qa_repro_mitigation_rejects_negative_tier_raw_and_bad_hash() -> None:
    row = load_governance_eval_rows()[2]
    payload = _base_payload(
        row,
        {
            "kind": "mitigation_evidence",
            "per_class": {
                "fairness_review_process": {
                    "effective_tier": -9,
                    "weighted_raw": -1e9,
                }
            },
            "signed_attestation_hash": "unsigned",
            "catalogue_presence_only": False,
        },
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"
