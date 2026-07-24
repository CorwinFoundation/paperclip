"""Canonical configured-target runner for the frozen 61-family registry.

The runner deliberately has one dispatch path for every family.  A smoke is
not allowed to activate a row unless the configured target was called and a
content-addressed evidence record containing a terminal verdict was written.
"""
from __future__ import annotations

import hashlib
import csv
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


class TargetRunError(RuntimeError):
    pass


def load_registry(path: Path = REGISTRY_PATH) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    ids = [row.get("family") for row in rows]
    if len(rows) != 61 or len(set(ids)) != 61 or any(not item for item in ids):
        raise TargetRunError("canonical registry must contain exactly 61 unique family IDs")
    return [
        {
            "family_id": row["family"],
            "rubric_point_id": row["risk_dimensions"],
            "required_cases": row["embedded_case_count"],
            "runner_path": row["runner_path"],
        }
        for row in rows
    ]


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
        body = json.dumps(
            {
                "family_id": row["family_id"],
                "registry_version": REGISTRY_VERSION,
                "rubric_point_id": row["rubric_point_id"],
                "runner_path": row["runner_path"],
                "required_cases": required_cases,
                "case": {
                    "case_id": f"{row['family_id']}:target-bound-development",
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
        verdict = _terminal_verdict(row, status, verdict_payload)
        record = {
            "family_id": row["family_id"],
            "case_id": f"{row['family_id']}:target-bound-development",
            "runner_path": row["runner_path"],
            "rubric_point_id": row["rubric_point_id"],
            "required_cases": required_cases,
            "cases_executed": verdict_payload.get("cases_executed"),
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
            "path": str(evidence_path),
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
    required_cases = row["required_cases"]
    cases_executed = payload.get("cases_executed")
    if isinstance(required_cases, int):
        if cases_executed != required_cases:
            return "fail"
    elif cases_executed != required_cases:
        return "fail"
    case_results = payload.get("case_results")
    if not isinstance(case_results, list) or not case_results:
        return "fail"
    if any(item.get("verdict") != "pass" for item in case_results if isinstance(item, dict)):
        return "fail"
    if not all(isinstance(item, dict) for item in case_results):
        return "fail"
    return "pass"
