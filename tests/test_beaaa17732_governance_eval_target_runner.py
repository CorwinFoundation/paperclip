from __future__ import annotations

import json
import hashlib
import subprocess
import sys
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
    _hash_reconciliation,
    _terminal_verdict,
)
from tools.family_activation_ledger import build_baseline, load_ledger


def _evidence_hash(evidence: dict[str, object]) -> str:
    canonical = {
        key: value for key, value in evidence.items() if key != "evidence_hash"
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _with_evidence_hash(evidence: dict[str, object]) -> dict[str, object]:
    evidence["evidence_hash"] = _evidence_hash(evidence)
    return evidence


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
                {"axis": "name_ethnicity", "total_count": 10, "threshold_min": 10, "threshold_good": 80, "coverage": 0.0, "status": "ok"},
                {"axis": "locale_zip", "total_count": 10, "threshold_min": 10, "threshold_good": 40, "coverage": 0.0, "status": "ok"},
                {"axis": "dialect_register", "total_count": 10, "threshold_min": 10, "threshold_good": 40, "coverage": 0.0, "status": "ok"},
                {"axis": "pronoun", "total_count": 10, "threshold_min": 10, "threshold_good": 30, "coverage": 0.0, "status": "ok"},
                {"axis": "age_signal", "total_count": 10, "threshold_min": 10, "threshold_good": 30, "coverage": 0.0, "status": "ok"},
                {"axis": "religious_marker", "total_count": 10, "threshold_min": 10, "threshold_good": 30, "coverage": 0.0, "status": "ok"},
            ],
            "source_inventory_hash": "sha256:" + "1" * 64,
            "catalogue_presence_only": False,
        }
    else:
        evidence = {
            "kind": "mitigation_evidence",
            "per_class": {
                "fairness_aware_finetune": {
                    "effective_tier": 2,
                    "raw_contribution": 2.0,
                    "weight": 0.4,
                    "weighted_raw": 0.8,
                },
                "output_filter_audit": {
                    "effective_tier": 2,
                    "raw_contribution": 2.0,
                    "weight": 0.4,
                    "weighted_raw": 0.8,
                },
                "fairness_review_process": {
                    "effective_tier": 2,
                    "raw_contribution": 2.0,
                    "weight": 0.2,
                    "weighted_raw": 0.4,
                },
            },
            "signed_attestation_hash": "sha256:" + "2" * 64,
            "catalogue_presence_only": False,
        }
    evidence = _with_evidence_hash(evidence)
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
    assert summary["hash_reconciliation"]["passed"] is True
    assert summary["hash_reconciliation"]["target_evidence_hashes_present"] is True
    assert summary["hash_reconciliation"]["evidence_record_hashes_present"] is True
    assert summary["hash_reconciliation"]["target_evidence_hashes_unique"] is True
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


def test_local_fixture_config_identity_ignores_ephemeral_port() -> None:
    first = GovernanceEvalTargetConfig(
        "http://127.0.0.1:10001/governance-eval",
        "fixture",
        "local_http_test_fixture",
        "BACKBOND_GOV_EVAL_TEST_TOKEN",
    )
    second = GovernanceEvalTargetConfig(
        "http://127.0.0.1:20002/governance-eval",
        "fixture",
        "local_http_test_fixture",
        "BACKBOND_GOV_EVAL_TEST_TOKEN",
    )

    assert first.evidence_identity() == second.evidence_identity()
    assert first.evidence_identity()["url"] == "http://127.0.0.1/governance-eval"


def test_tracked_replay_is_byte_identical_and_aggregate_remains_61_of_61() -> None:
    artifact_dir = Path("artifacts/beaaa-17732/target-smoke")
    tracked = [
        artifact_dir / "eval_coverage_breadth.json",
        artifact_dir / "eval_representation.json",
        artifact_dir / "mitigation_evidence.json",
        artifact_dir / "summary.json",
    ]
    before = {path: path.read_bytes() for path in tracked}

    subprocess.run(
        [sys.executable, "tools/run_beaaa17732_governance_eval_smoke.py"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert {path: path.read_bytes() for path in tracked} == before
    baseline = build_baseline(load_ledger())
    assert baseline["complete_working_predicate"] is True
    assert baseline["truthful_baseline"] == "61/61"


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
        _with_evidence_hash({
            "kind": "eval_coverage_breadth",
            "measured_dim_ids": ["P1.1"],
            "in_scope_dim_ids": ["P1.1", "P1.2"],
            "coverage_breadth_pct": 100,
            "catalogue_presence_only": False,
        }),
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"


def test_qa_repro_coverage_rejects_negative_percentage() -> None:
    row = load_governance_eval_rows()[0]
    payload = _base_payload(
        row,
        _with_evidence_hash({
            "kind": "eval_coverage_breadth",
            "measured_dim_ids": ["P1.1"],
            "in_scope_dim_ids": ["P1.1", "P1.2"],
            "coverage_breadth_pct": -500,
            "catalogue_presence_only": False,
        }),
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"


def test_qa_repro_representation_rejects_negative_count_and_bad_hash() -> None:
    row = load_governance_eval_rows()[1]
    payload = _base_payload(
        row,
        _with_evidence_hash({
            "kind": "eval_representation",
            "axes": [{"axis": "age_signal", "total_count": -7, "status": "ok"}],
            "source_inventory_hash": "not-a-hash",
            "catalogue_presence_only": False,
        }),
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"


def test_qa_repro_mitigation_rejects_negative_tier_raw_and_bad_hash() -> None:
    row = load_governance_eval_rows()[2]
    payload = _base_payload(
        row,
        _with_evidence_hash({
            "kind": "mitigation_evidence",
            "per_class": {
                "fairness_review_process": {
                    "effective_tier": -9,
                    "weighted_raw": -1e9,
                }
            },
            "signed_attestation_hash": "unsigned",
            "catalogue_presence_only": False,
        }),
    )
    assert _terminal_verdict(row, 200, payload) == "INVALID"


def _valid_representation_evidence() -> dict[str, object]:
    body = {
        "family_id": "eval_representation",
        "registry_version": REGISTRY_VERSION,
        "rubric_point_id": "P2.1,P2.2,P2.3,P2.4,P2.5",
        "source_file": "rubric/probe_mappings/eval_representation.yaml",
    }
    return json.loads(_response_for(body))["evidence"]


def _valid_mitigation_evidence() -> dict[str, object]:
    body = {
        "family_id": "mitigation_evidence",
        "registry_version": REGISTRY_VERSION,
        "rubric_point_id": "P2.6",
        "source_file": "rubric/probe_mappings/mitigation_evidence.yaml",
    }
    return json.loads(_response_for(body))["evidence"]


def test_representation_rejects_empty_axes() -> None:
    row = load_governance_eval_rows()[1]
    evidence = _valid_representation_evidence()
    evidence["axes"] = []
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_representation_rejects_duplicate_axes() -> None:
    row = load_governance_eval_rows()[1]
    evidence = _valid_representation_evidence()
    axes = evidence["axes"]
    axes[1] = dict(axes[0])
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_representation_rejects_below_threshold_status_ok() -> None:
    row = load_governance_eval_rows()[1]
    evidence = _valid_representation_evidence()
    evidence["axes"][0]["total_count"] = 9
    evidence["axes"][0]["coverage"] = 0.0
    evidence["axes"][0]["status"] = "ok"
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_representation_rejects_mapping_threshold_mismatch() -> None:
    row = load_governance_eval_rows()[1]
    evidence = _valid_representation_evidence()
    evidence["axes"][0]["threshold_good"] = 81
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_representation_rejects_inconsistent_derived_coverage() -> None:
    row = load_governance_eval_rows()[1]
    evidence = _valid_representation_evidence()
    evidence["axes"][0]["total_count"] = 45
    evidence["axes"][0]["coverage"] = 1.0
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_evidence_hash_is_required_and_canonical() -> None:
    row = load_governance_eval_rows()[1]
    evidence = _valid_representation_evidence()
    evidence.pop("evidence_hash")
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"

    evidence = _valid_representation_evidence()
    evidence["evidence_hash"] = "sha256:" + "f" * 64
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_stale_evidence_hash_is_invalid() -> None:
    row = load_governance_eval_rows()[1]
    evidence = _valid_representation_evidence()
    evidence["axes"][0]["total_count"] = 80
    evidence["axes"][0]["coverage"] = 1.0
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_mismatched_evidence_hash_is_invalid() -> None:
    row = load_governance_eval_rows()[2]
    evidence = _valid_mitigation_evidence()
    evidence["evidence_hash"] = "sha256:" + "0" * 64
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_hash_reconciliation_rejects_duplicate_target_evidence_hashes() -> None:
    duplicate = "sha256:" + "a" * 64
    results = [
        {
            "family_id": "eval_coverage_breadth",
            "target_evidence": {"evidence_hash": duplicate},
            "evidence": {"sha256": "1" * 64},
        },
        {
            "family_id": "eval_representation",
            "target_evidence": {"evidence_hash": duplicate},
            "evidence": {"sha256": "2" * 64},
        },
    ]
    reconciliation = _hash_reconciliation(results)
    assert reconciliation["target_evidence_hashes_present"] is True
    assert reconciliation["target_evidence_hashes_unique"] is False
    assert reconciliation["passed"] is False


def test_hash_reconciliation_rejects_missing_record_hashes() -> None:
    results = [
        {
            "family_id": "eval_coverage_breadth",
            "target_evidence": {"evidence_hash": "sha256:" + "a" * 64},
            "evidence": {},
        }
    ]
    reconciliation = _hash_reconciliation(results)
    assert reconciliation["evidence_record_hashes_present"] is False
    assert reconciliation["passed"] is False


def test_mitigation_rejects_arbitrary_class() -> None:
    row = load_governance_eval_rows()[2]
    evidence = _valid_mitigation_evidence()
    evidence["per_class"]["arbitrary_class"] = {
        "effective_tier": 2,
        "raw_contribution": 2.0,
        "weight": 0.1,
        "weighted_raw": 0.2,
    }
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_mitigation_rejects_missing_class() -> None:
    row = load_governance_eval_rows()[2]
    evidence = _valid_mitigation_evidence()
    evidence["per_class"].pop("output_filter_audit")
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_mitigation_rejects_wrong_weight() -> None:
    row = load_governance_eval_rows()[2]
    evidence = _valid_mitigation_evidence()
    evidence["per_class"]["fairness_review_process"]["weight"] = 0.4
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_mitigation_rejects_inconsistent_weighted_value() -> None:
    row = load_governance_eval_rows()[2]
    evidence = _valid_mitigation_evidence()
    evidence["per_class"]["fairness_aware_finetune"]["weighted_raw"] = 1.1
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"


def test_mitigation_rejects_wrong_raw_contribution() -> None:
    row = load_governance_eval_rows()[2]
    evidence = _valid_mitigation_evidence()
    evidence["per_class"]["fairness_aware_finetune"]["raw_contribution"] = 3.0
    evidence["per_class"]["fairness_aware_finetune"]["weighted_raw"] = 1.2
    evidence["evidence_hash"] = _evidence_hash(evidence)
    assert _terminal_verdict(row, 200, _base_payload(row, evidence)) == "INVALID"
