"""Canonical governance/eval target runner for BEAAA-17732 ordinals 10, 11, 20.

These families are control/evidence evaluations, not behavioral prompt suites.
They still must execute through a configured non-customer target and produce
target-authored evidence before they can be counted as working.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "IFA" / "scanner-test-registry-baseline-2026-07-13.csv"
REGISTRY_VERSION = "canonical-61-family-registry@2026-07-13"
SCANNER_VERSION = "0.5.0"
PROBE_SET_VERSION = "family-activation-ledger/v0.1"
GOVERNANCE_EVAL_ORDINALS = (10, 11, 20)
FAMILY_KINDS = {
    "eval_coverage_breadth": "eval_coverage_breadth",
    "eval_representation": "eval_representation",
    "mitigation_evidence": "mitigation_evidence",
}


class GovernanceEvalRunError(RuntimeError):
    """Fail-closed target or configuration error."""


@dataclass(frozen=True)
class GovernanceEvalTargetConfig:
    url: str
    identity: str
    config_class: str
    credential_env: str
    call_cap: int = 30
    spend_cap_usd: float = 5.0
    cost_per_call_usd: float = 0.0

    def credential(self) -> str:
        if any(marker in self.credential_env.upper() for marker in ("PROD", "CUSTOMER")):
            raise GovernanceEvalRunError(
                "production/customer credential markers are forbidden"
            )
        value = os.environ.get(self.credential_env)
        if not value:
            raise GovernanceEvalRunError("test credential is unavailable")
        return value


def load_governance_eval_rows(path: Path = REGISTRY_PATH) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 61:
        raise GovernanceEvalRunError("canonical registry must contain exactly 61 rows")
    selected: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows, 1):
        if ordinal not in GOVERNANCE_EVAL_ORDINALS:
            continue
        selected.append(
            {
                "ordinal": ordinal,
                "family_id": row["family"],
                "rubric_point_id": row["risk_dimensions"],
                "required_cases": int(row["embedded_case_count"] or 0),
                "runner_path": row["runner_path"],
                "source_file": row["source_file"],
            }
        )
    if [row["ordinal"] for row in selected] != list(GOVERNANCE_EVAL_ORDINALS):
        raise GovernanceEvalRunError("ordinals 10, 11, 20 were not discovered")
    return selected


class GovernanceEvalTargetRunner:
    def __init__(self, config: GovernanceEvalTargetConfig, evidence_dir: Path):
        self.config = config
        self.evidence_dir = evidence_dir
        self.calls = 0
        self.spend_usd = 0.0

    def run_family(self, row: Mapping[str, Any], *, seed: int = 0) -> dict[str, Any]:
        family_id = str(row["family_id"])
        if family_id not in FAMILY_KINDS:
            raise GovernanceEvalRunError(f"{family_id}: not in BEAAA-17732 scope")
        if self.calls + 1 > self.config.call_cap:
            raise GovernanceEvalRunError("call cap would be exceeded")
        projected_spend = self.spend_usd + self.config.cost_per_call_usd
        if projected_spend > self.config.spend_cap_usd:
            raise GovernanceEvalRunError("spend cap would be exceeded")

        request_payload = _request_payload(row, seed=seed)
        request_body = _canonical_bytes(request_payload)
        request = urllib.request.Request(
            self.config.url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {self.config.credential()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                output_body = response.read()
                http_status = response.status
        except urllib.error.HTTPError as exc:
            output_body = exc.read()
            http_status = exc.code
        self.calls += 1
        self.spend_usd = projected_spend

        output_payload = _parse_json_object(output_body)
        verdict = _terminal_verdict(row, http_status, output_payload)
        record = {
            "schema_version": "backbond.governance-eval-target-evidence/v0.1",
            "family_id": family_id,
            "ordinal": row["ordinal"],
            "rubric_point_id": row["rubric_point_id"],
            "source_file": row["source_file"],
            "runner_path": row["runner_path"],
            "scanner_version": SCANNER_VERSION,
            "corpus_version": REGISTRY_VERSION,
            "probe-set-version": PROBE_SET_VERSION,
            "seed": seed,
            "target_identity": self.config.identity,
            "target_config_class": self.config.config_class,
            "credential_class": "test_only",
            "customer_data": False,
            "score_activation": False,
            "call_cap": self.config.call_cap,
            "calls_attempted": self.calls,
            "spend_cap_usd": self.config.spend_cap_usd,
            "actual_spend_usd": self.spend_usd,
            "http_status": http_status,
            "target_verdict": output_payload.get("verdict"),
            "verdict": verdict,
            "target_evidence": output_payload.get("evidence"),
            "hashes": {
                "request_sha256": _sha256(request_body),
                "config_sha256": _sha256(_canonical_bytes(asdict(self.config))),
                "input_sha256": _sha256(_canonical_bytes(dict(row))),
                "output_sha256": _sha256(output_body),
                "verdict_sha256": _sha256(_canonical_bytes({"verdict": verdict})),
            },
            "immutable_pins": {
                "scanner_version": SCANNER_VERSION,
                "corpus_version": REGISTRY_VERSION,
                "probe-set-version": PROBE_SET_VERSION,
                "contract": "contracts/are-scanner-api-v1.md@v1.0",
            },
        }
        encoded = _canonical_bytes(record) + b"\n"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = self.evidence_dir / f"{family_id}.json"
        evidence_path.write_bytes(encoded)
        record["evidence"] = {
            "path": _portable_path(evidence_path),
            "sha256": _sha256(encoded),
        }
        return record

    def run_all(self, *, seed: int = 0) -> dict[str, Any]:
        rows = load_governance_eval_rows()
        results = [self.run_family(row, seed=seed) for row in rows]
        summary = {
            "schema_version": "backbond.governance-eval-target-smoke/v0.1",
            "issue": "BEAAA-17732",
            "scanner_version": SCANNER_VERSION,
            "corpus_version": REGISTRY_VERSION,
            "probe-set-version": PROBE_SET_VERSION,
            "families": results,
            "target_config_class": self.config.config_class,
            "call_cap": self.config.call_cap,
            "actual_calls": self.calls,
            "spend_cap_usd": self.config.spend_cap_usd,
            "actual_spend_usd": self.spend_usd,
            "customer_data": False,
            "production_credentials": False,
            "score_activation": False,
            "verdict": "PASS" if all(row["verdict"] == "PASS" for row in results) else "FAIL",
        }
        encoded = _canonical_bytes(summary) + b"\n"
        path = self.evidence_dir / "summary.json"
        path.write_bytes(encoded)
        summary["evidence"] = {"path": _portable_path(path), "sha256": _sha256(encoded)}
        return summary


def _request_payload(row: Mapping[str, Any], *, seed: int) -> dict[str, Any]:
    family_id = str(row["family_id"])
    return {
        "schema_version": "backbond.governance-eval-request/v0.1",
        "family_id": family_id,
        "ordinal": row["ordinal"],
        "registry_version": REGISTRY_VERSION,
        "rubric_point_id": row["rubric_point_id"],
        "source_file": row["source_file"],
        "runner_path": row["runner_path"],
        "required_cases": row["required_cases"],
        "seed": seed,
        "evidence_request": {
            "kind": FAMILY_KINDS[family_id],
            "terminal_verdict_required": True,
            "target_produced_evidence_required": True,
            "catalogue_presence_alone_can_pass": False,
            "customer_data": False,
            "score_activation": False,
        },
    }


def _terminal_verdict(row: Mapping[str, Any], status: int, payload: dict[str, Any]) -> str:
    if not 200 <= status < 300:
        return "INVALID"
    if payload.get("schema_version") != "backbond.governance-eval-response/v0.1":
        return "INVALID"
    if payload.get("family_id") != row["family_id"]:
        return "INVALID"
    if payload.get("registry_version") != REGISTRY_VERSION:
        return "INVALID"
    if payload.get("rubric_point_id") != row["rubric_point_id"]:
        return "INVALID"
    if payload.get("source_file") != row["source_file"]:
        return "INVALID"
    if payload.get("customer_data") is not False:
        return "INVALID"
    if payload.get("score_activation") is not False:
        return "INVALID"
    target_verdict = payload.get("verdict")
    if target_verdict not in {"pass", "fail"}:
        return "INVALID"
    evidence = payload.get("evidence")
    if not _valid_family_evidence(str(row["family_id"]), evidence):
        return "INVALID"
    return "PASS" if target_verdict == "pass" else "FAIL"


def _valid_family_evidence(family_id: str, evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    if evidence.get("kind") != FAMILY_KINDS[family_id]:
        return False
    if evidence.get("catalogue_presence_only") is True:
        return False
    if family_id == "eval_coverage_breadth":
        measured = evidence.get("measured_dim_ids")
        in_scope = evidence.get("in_scope_dim_ids")
        return (
            isinstance(measured, list)
            and bool(measured)
            and all(isinstance(item, str) and item for item in measured)
            and isinstance(in_scope, list)
            and set(measured).issubset(set(in_scope))
            and isinstance(evidence.get("coverage_breadth_pct"), (int, float))
        )
    if family_id == "eval_representation":
        axes = evidence.get("axes")
        return (
            isinstance(axes, list)
            and bool(axes)
            and all(
                isinstance(axis, dict)
                and isinstance(axis.get("axis"), str)
                and isinstance(axis.get("total_count"), int)
                and axis.get("status") in {"ok", "below_min", "no_threshold"}
                for axis in axes
            )
            and "source_inventory_hash" in evidence
        )
    if family_id == "mitigation_evidence":
        per_class = evidence.get("per_class")
        return (
            isinstance(per_class, dict)
            and bool(per_class)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("effective_tier"), int)
                and isinstance(item.get("weighted_raw"), (int, float))
                for item in per_class.values()
            )
            and isinstance(evidence.get("signed_attestation_hash"), str)
        )
    return False


def _parse_json_object(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
