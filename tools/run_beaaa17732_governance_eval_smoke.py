#!/usr/bin/env python3
"""Replay BEAAA-17732 bounded governance/eval target smoke."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.governance_eval_target_runner import (  # noqa: E402
    GovernanceEvalTargetConfig,
    GovernanceEvalTargetRunner,
)


ARTIFACT_DIR = ROOT / "artifacts" / "beaaa-17732" / "target-smoke"
TOKEN_ENV = "BACKBOND_GOV_EVAL_TEST_TOKEN"
LOCAL_PORT = 17732


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _with_evidence_hash(evidence: dict[str, Any]) -> dict[str, Any]:
    canonical = {
        key: value for key, value in evidence.items() if key != "evidence_hash"
    }
    digest = hashlib.sha256(_canonical_bytes(canonical)).hexdigest()
    evidence["evidence_hash"] = f"sha256:{digest}"
    return evidence


def _target_response(body: dict[str, Any]) -> dict[str, Any]:
    family_id = str(body["family_id"])
    if family_id == "eval_coverage_breadth":
        evidence = {
            "kind": "eval_coverage_breadth",
            "measured_dim_ids": ["P1.1", "P1.2", "P2.4", "P2.6"],
            "in_scope_dim_ids": ["P1.1", "P1.2", "P1.3", "P2.4", "P2.6"],
            "coverage_breadth_pct": 80.0,
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
            "source_inventory_hash": "sha256:" + "3" * 64,
            "catalogue_presence_only": False,
        }
    elif family_id == "mitigation_evidence":
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
            "signed_attestation_hash": "sha256:" + "4" * 64,
            "catalogue_presence_only": False,
        }
    else:
        evidence = {"kind": family_id, "catalogue_presence_only": True}
    return {
        "schema_version": "backbond.governance-eval-response/v0.1",
        "family_id": body["family_id"],
        "registry_version": body["registry_version"],
        "rubric_point_id": body["rubric_point_id"],
        "source_file": body["source_file"],
        "customer_data": False,
        "score_activation": False,
        "verdict": "pass",
        "evidence": _with_evidence_hash(evidence),
    }


class _LocalGovernanceEvalTarget(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        payload = _canonical_bytes(_target_response(body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass


def main() -> int:
    os.environ.setdefault(TOKEN_ENV, "beaaa-17732-local-test-token")
    server = ThreadingHTTPServer(("127.0.0.1", LOCAL_PORT), _LocalGovernanceEvalTarget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runner = GovernanceEvalTargetRunner(
            GovernanceEvalTargetConfig(
                url=f"http://127.0.0.1:{LOCAL_PORT}/governance-eval",
                identity="beaaa-17732-local-http-test-target",
                config_class="local_http_test_fixture",
                credential_env=TOKEN_ENV,
                call_cap=30,
                spend_cap_usd=5.0,
                cost_per_call_usd=0.0,
            ),
            ARTIFACT_DIR,
        )
        summary = runner.run_all(seed=0)
    finally:
        server.shutdown()
    summary_path = ARTIFACT_DIR / "summary.json"
    print(
        json.dumps(
            {
                "actual_calls": summary["actual_calls"],
                "actual_spend_usd": summary["actual_spend_usd"],
                "families": [row["family_id"] for row in summary["families"]],
                "summary_path": str(summary_path.relative_to(ROOT)),
                "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "verdict": summary["verdict"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
