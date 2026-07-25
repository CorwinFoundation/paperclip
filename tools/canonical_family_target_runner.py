"""Canonical configured-target runner for the frozen 61-family registry.

The runner deliberately has one dispatch path for every family.  A smoke is
not allowed to activate a row unless the configured target was called and a
content-addressed evidence record containing a terminal verdict was written.
"""
from __future__ import annotations

import hashlib
import csv
import importlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REGISTRY_VERSION = "canonical-61-family-registry@2026-07-13"
SCANNER_VERSION = "0.5.0"
REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "IFA"
    / "scanner-test-registry-baseline-2026-07-13.csv"
)
OPS_CONTROL_FAMILIES = {
    "p4_audit_log",
    "p4_eval_cadence",
    "p4_incident_response",
    "p4_kill_switch",
    "p4_owner_matrix",
    "p4_version_control",
}
OPS_CONTROL_RUNNER_PATH = "harness.ops_control_target:evaluate_control"
OPS_CONTROL_REQUIRED_CASES = 7


class TargetRunError(RuntimeError):
    pass


def load_registry(path: Path = REGISTRY_PATH) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    ids = [row.get("family") for row in rows]
    if len(rows) != 61 or len(set(ids)) != 61 or any(not item for item in ids):
        raise TargetRunError("canonical registry must contain exactly 61 unique family IDs")
    normalized = [
        {
            "family_id": row["family"],
            "rubric_point_id": row["risk_dimensions"],
            "required_cases": _parse_required_cases(row["embedded_case_count"]),
            "runner_path": row["runner_path"],
        }
        for row in rows
    ]
    for row in normalized:
        if row["family_id"] in OPS_CONTROL_FAMILIES:
            row["required_cases"] = OPS_CONTROL_REQUIRED_CASES
            row["runner_path"] = OPS_CONTROL_RUNNER_PATH
    return normalized


@dataclass(frozen=True)
class TargetConfig:
    url: str
    identity: str
    config_class: str
    credential_env: str
    call_cap: int = 500
    spend_cap_usd: float = 100.0
    cost_per_call_usd: float = 0.0

    def credential(self) -> str:
        value = os.environ.get(self.credential_env, "")
        if not value or any(marker in self.credential_env.upper() for marker in ("PROD", "CUSTOMER")):
            raise TargetRunError("a non-production test credential environment variable is required")
        return value


class ConfiguredTargetRunner:
    def __init__(self, config: TargetConfig, evidence_dir: Path):
        self.config = config
        self.evidence_dir = evidence_dir
        self.calls = 0
        self.spend_usd = 0.0

    def run_family(self, row: dict[str, Any]) -> dict[str, Any]:
        if self.calls + 1 > self.config.call_cap:
            raise TargetRunError("call cap would be exceeded")
        projected = self.spend_usd + self.config.cost_per_call_usd
        if projected > self.config.spend_cap_usd:
            raise TargetRunError("spend cap would be exceeded")
        required_cases = row["required_cases"]
        case_ids = _expected_case_ids(row)
        body = json.dumps(
            {
                "family_id": row["family_id"],
                "registry_version": REGISTRY_VERSION,
                "rubric_point_id": row["rubric_point_id"],
                "runner_path": row["runner_path"],
                "required_cases": required_cases,
                "case": {
                    "case_id": f"{row['family_id']}:target-bound-development",
                    "case_ids": case_ids,
                    "kind": "family_runner_verdict",
                    "expected_runner_path": row["runner_path"],
                    "expected_required_cases": required_cases,
                },
                "evidence_request": {
                    "terminal_verdict_required": True,
                    "case_results_required": True,
                    "score_activation": False,
                    "customer_data": False,
                },
            },
            sort_keys=True,
        ).encode()
        request = urllib.request.Request(
            self.config.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.credential()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            response_body = exc.read()
            status = exc.code
        self.calls += 1
        self.spend_usd = projected
        verdict_payload = _parse_verdict_payload(response_body)
        if row["family_id"] in OPS_CONTROL_FAMILIES:
            verdict_payload = _evaluate_ops_control_payload(row, verdict_payload)
        verdict = _terminal_verdict(row, status, verdict_payload)
        record = {
            "family_id": row["family_id"],
            "case_id": f"{row['family_id']}:target-bound-development",
            "runner_path": row["runner_path"],
            "rubric_point_id": row["rubric_point_id"],
            "required_cases": required_cases,
            "cases_executed": verdict_payload.get("cases_executed"),
            "case_results": verdict_payload.get("case_results", []),
            "target_identity": self.config.identity,
            "target_config_class": self.config.config_class,
            "http_status": status,
            "response_sha256": hashlib.sha256(response_body).hexdigest(),
            "verdict": verdict,
            "target_verdict": verdict_payload.get("verdict"),
            "target_evidence_refs": verdict_payload.get("evidence_refs", []),
            "call_ordinal": self.calls,
            "cost_usd": self.config.cost_per_call_usd,
            "customer_data": False,
            "credential_class": "test_only",
        }
        encoded = (json.dumps(record, sort_keys=True) + "\n").encode()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = self.evidence_dir / f"{row['family_id']}.json"
        evidence_path.write_bytes(encoded)
        record["evidence"] = {
            "path": _portable_path(evidence_path),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
        return record


def _parse_verdict_payload(response_body: bytes) -> dict[str, Any]:
    if not response_body:
        return {}
    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_evaluator(runner_path: str):
    module_name, separator, attribute = runner_path.partition(":")
    if not separator or not module_name or not attribute:
        raise TargetRunError("runner_path must be module:callable")
    evaluator = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(evaluator):
        raise TargetRunError(f"runner_path is not callable: {runner_path}")
    return evaluator


def _evaluate_ops_control_payload(
    row: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Invoke the pinned evaluator over configured-target evidence."""
    cases = payload.get("evaluation_cases")
    if not isinstance(cases, list):
        return {}
    evaluator = _load_evaluator(row["runner_path"])
    case_results: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            return {}
        case_id = case.get("case_id")
        expected = case.get("expected_verdict")
        evidence = case.get("evidence")
        if (
            not isinstance(case_id, str)
            or expected not in {"PASS", "FAIL", "INVALID"}
            or not isinstance(evidence, dict)
        ):
            return {}
        try:
            evaluated = evaluator(row["family_id"], **evidence)
        except (TypeError, ValueError):
            return {}
        actual = evaluated.get("verdict")
        matched = actual == expected
        case_results.append(
            {
                "case_id": case_id,
                "runner_path": row["runner_path"],
                "verdict": "pass" if matched else "fail",
                "expected_verdict": expected,
                "evaluated_verdict": actual,
                "verdict_sha256": evaluated.get("verdict_sha256"),
            }
        )
        evidence_refs.append(
            {
                "kind": "configured_target_evaluator_result",
                "case_id": case_id,
                "runner_path": row["runner_path"],
                "verdict_sha256": evaluated.get("verdict_sha256"),
            }
        )
    return {
        "family_id": row["family_id"],
        "registry_version": REGISTRY_VERSION,
        "rubric_point_id": row["rubric_point_id"],
        "runner_path": row["runner_path"],
        "cases_executed": len(case_results),
        "case_results": case_results,
        "evidence_refs": evidence_refs,
        "customer_data": False,
        "score_activation": False,
        "verdict": "pass"
        if case_results and all(item["verdict"] == "pass" for item in case_results)
        else "fail",
    }


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _parse_required_cases(value: Any) -> int | str:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def _expected_case_ids(row: dict[str, Any]) -> list[str]:
    required_cases = _parse_required_cases(row["required_cases"])
    if isinstance(required_cases, int):
        return [
            f"{row['family_id']}:case-{index:03d}"
            for index in range(1, required_cases + 1)
        ]
    return [f"{row['family_id']}:external-suite"]


def _case_result_ids(case_results: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in case_results:
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            return []
        ids.append(case_id)
    return ids


def _evidence_ref_ids(evidence_refs: list[Any]) -> set[str]:
    ids: set[str] = set()
    for item in evidence_refs:
        if not isinstance(item, dict):
            return set()
        case_id = item.get("case_id")
        if isinstance(case_id, str) and case_id:
            ids.add(case_id)
    return ids


def _terminal_verdict(
    row: dict[str, Any], status: int, payload: dict[str, Any]
) -> str:
    if not 200 <= status < 300:
        return "fail"
    if payload.get("family_id") != row["family_id"]:
        return "fail"
    if payload.get("registry_version") != REGISTRY_VERSION:
        return "fail"
    if payload.get("runner_path") != row["runner_path"]:
        return "fail"
    if payload.get("rubric_point_id") != row["rubric_point_id"]:
        return "fail"
    if payload.get("verdict") != "pass":
        return "fail"
    if payload.get("customer_data") is not False:
        return "fail"
    if payload.get("score_activation") is not False:
        return "fail"
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        return "fail"
    required_cases = _parse_required_cases(row["required_cases"])
    cases_executed = payload.get("cases_executed")
    if isinstance(required_cases, int):
        if cases_executed != required_cases:
            return "fail"
    elif cases_executed != required_cases:
        return "fail"
    case_results = payload.get("case_results")
    if not isinstance(case_results, list):
        return "fail"
    if not all(isinstance(item, dict) for item in case_results):
        return "fail"
    if any(item.get("verdict") != "pass" for item in case_results):
        return "fail"
    if any(item.get("runner_path") != row["runner_path"] for item in case_results):
        return "fail"
    case_ids = _case_result_ids(case_results)
    if len(case_ids) != len(set(case_ids)):
        return "fail"
    evidence_case_ids = _evidence_ref_ids(evidence_refs)
    if isinstance(required_cases, int):
        expected_case_ids = _expected_case_ids(row)
        if len(case_results) != required_cases:
            return "fail"
        if case_ids != expected_case_ids:
            return "fail"
        if not set(expected_case_ids).issubset(evidence_case_ids):
            return "fail"
    else:
        if not case_results:
            return "fail"
        if not set(case_ids).issubset(evidence_case_ids):
            return "fail"
    return "pass"
