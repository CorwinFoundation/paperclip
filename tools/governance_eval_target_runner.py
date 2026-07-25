"""Canonical governance/eval target runner for BEAAA-17732 ordinals 10, 11, 20.

These families are control/evidence evaluations, not behavioral prompt suites.
They still must execute through a configured non-customer target and produce
target-authored evidence before they can be counted as working.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "IFA" / "scanner-test-registry-baseline-2026-07-13.csv"
EVAL_REPRESENTATION_MAPPING_PATH = (
    ROOT / "rubric" / "probe_mappings" / "eval_representation.yaml"
)
MITIGATION_EVIDENCE_MAPPING_PATH = (
    ROOT / "rubric" / "probe_mappings" / "mitigation_evidence.yaml"
)
REGISTRY_VERSION = "canonical-61-family-registry@2026-07-13"
SCANNER_VERSION = "0.5.0"
PROBE_SET_VERSION = "family-activation-ledger/v0.1"
GOVERNANCE_EVAL_ORDINALS = (10, 11, 20)
FAMILY_KINDS = {
    "eval_coverage_breadth": "eval_coverage_breadth",
    "eval_representation": "eval_representation",
    "mitigation_evidence": "mitigation_evidence",
}
SHA256_REF_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COVERAGE_RECONCILIATION_TOLERANCE = 0.01
FLOAT_RECONCILIATION_TOLERANCE = 0.000001


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
        hash_reconciliation = _hash_reconciliation(results)
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
            "hash_reconciliation": hash_reconciliation,
            "verdict": (
                "PASS"
                if all(row["verdict"] == "PASS" for row in results)
                and hash_reconciliation["passed"]
                else "FAIL"
            ),
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


def _hash_reconciliation(results: list[dict[str, Any]]) -> dict[str, Any]:
    target_hashes: dict[str, str] = {}
    record_hashes: dict[str, str] = {}
    for result in results:
        family_id = str(result.get("family_id"))
        target_evidence = result.get("target_evidence")
        if isinstance(target_evidence, dict):
            target_hash = target_evidence.get("evidence_hash")
            if isinstance(target_hash, str):
                target_hashes[family_id] = target_hash
        evidence_record = result.get("evidence")
        if isinstance(evidence_record, dict):
            record_hash = evidence_record.get("sha256")
            if isinstance(record_hash, str):
                record_hashes[family_id] = f"sha256:{record_hash}"

    expected_families = {str(result.get("family_id")) for result in results}
    target_values = list(target_hashes.values())
    record_values = list(record_hashes.values())
    target_present = set(target_hashes) == expected_families and all(
        _valid_sha256_ref(value) for value in target_values
    )
    record_present = set(record_hashes) == expected_families and all(
        _valid_sha256_ref(value) for value in record_values
    )
    target_unique = len(target_values) == len(set(target_values))
    record_unique = len(record_values) == len(set(record_values))
    return {
        "target_evidence_hashes": target_hashes,
        "evidence_record_hashes": record_hashes,
        "target_evidence_hashes_present": target_present,
        "evidence_record_hashes_present": record_present,
        "target_evidence_hashes_unique": target_unique,
        "evidence_record_hashes_unique": record_unique,
        "passed": target_present and record_present and target_unique and record_unique,
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
    if not _valid_evidence_hash(evidence):
        return False
    if family_id == "eval_coverage_breadth":
        measured = evidence.get("measured_dim_ids")
        in_scope = evidence.get("in_scope_dim_ids")
        pct = evidence.get("coverage_breadth_pct")
        if (
            not isinstance(measured, list)
            or not measured
            or not _all_unique_strings(measured)
            or not isinstance(in_scope, list)
            or not _all_unique_strings(in_scope)
            or not set(measured).issubset(set(in_scope))
            or not _bounded_number(pct, minimum=0.0, maximum=100.0)
        ):
            return False
        expected_pct = 100.0 * len(measured) / len(in_scope)
        return abs(float(pct) - expected_pct) <= COVERAGE_RECONCILIATION_TOLERANCE
    if family_id == "eval_representation":
        axes = evidence.get("axes")
        thresholds = _eval_representation_thresholds()
        expected_axes = set(thresholds)
        if not isinstance(axes, list) or not axes:
            return False
        axis_ids = [axis.get("axis") for axis in axes if isinstance(axis, dict)]
        if set(axis_ids) != expected_axes or len(axis_ids) != len(expected_axes):
            return False
        return (
            all(_valid_representation_axis(axis, thresholds) for axis in axes)
            and _valid_sha256_ref(evidence.get("source_inventory_hash"))
        )
    if family_id == "mitigation_evidence":
        per_class = evidence.get("per_class")
        class_weights, tier_raw = _mitigation_evidence_mapping()
        if not isinstance(per_class, dict) or set(per_class) != set(class_weights):
            return False
        return (
            all(
                _valid_mitigation_class(class_id, item, class_weights, tier_raw)
                for class_id, item in per_class.items()
            )
            and _valid_sha256_ref(evidence.get("signed_attestation_hash"))
        )
    return False


def _eval_representation_thresholds() -> dict[str, dict[str, int]]:
    mapping = yaml.safe_load(
        EVAL_REPRESENTATION_MAPPING_PATH.read_text(encoding="utf-8")
    )
    thresholds: dict[str, dict[str, int]] = {}
    for axis, row in mapping["coverage_thresholds"].items():
        if row.get("uw_review_status") != "signed":
            raise GovernanceEvalRunError(
                f"{axis}: eval_representation threshold is unsigned"
            )
        thresholds[axis] = {"min": int(row["min"]), "good": int(row["good"])}
    return thresholds


def _mitigation_evidence_mapping() -> tuple[dict[str, float], dict[int, float]]:
    mapping = yaml.safe_load(
        MITIGATION_EVIDENCE_MAPPING_PATH.read_text(encoding="utf-8")
    )
    class_weights = {
        class_id: float(row["weight"])
        for class_id, row in mapping["artifact_classes"].items()
    }
    tier_raw = {
        int(tier): float(row["raw_contribution"])
        for tier, row in mapping["evidence_tiers"].items()
    }
    return class_weights, tier_raw


def _valid_representation_axis(
    axis: Any, thresholds: dict[str, dict[str, int]]
) -> bool:
    if not isinstance(axis, dict):
        return False
    axis_id = axis.get("axis")
    if not isinstance(axis_id, str) or axis_id not in thresholds:
        return False
    count = axis.get("total_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return False
    threshold = thresholds[axis_id]
    if axis.get("threshold_min") != threshold["min"]:
        return False
    if axis.get("threshold_good") != threshold["good"]:
        return False
    status = axis.get("status")
    if count < threshold["min"]:
        if status != "below_min":
            return False
    elif status != "ok":
        return False
    coverage = axis.get("coverage")
    if not _bounded_number(coverage, minimum=0.0, maximum=1.0):
        return False
    return _numbers_close(float(coverage), _axis_coverage(count, threshold))


def _axis_coverage(count: int, threshold: dict[str, int]) -> float:
    minimum = threshold["min"]
    good = threshold["good"]
    if count < minimum:
        return 0.0
    if count >= good:
        return 1.0
    return (count - minimum) / max(1, good - minimum)


def _valid_mitigation_class(
    class_id: str,
    item: Any,
    class_weights: dict[str, float],
    tier_raw: dict[int, float],
) -> bool:
    if not isinstance(item, dict):
        return False
    tier = item.get("effective_tier")
    if not isinstance(tier, int) or isinstance(tier, bool) or tier not in tier_raw:
        return False
    expected_weight = class_weights[class_id]
    expected_raw = tier_raw[tier]
    if not _bounded_number(item.get("weight"), minimum=0.0, maximum=1.0):
        return False
    if not _bounded_number(item.get("raw_contribution"), minimum=0.0, maximum=4.0):
        return False
    if not _bounded_number(item.get("weighted_raw"), minimum=0.0, maximum=4.0):
        return False
    return (
        _numbers_close(float(item["weight"]), expected_weight)
        and _numbers_close(float(item["raw_contribution"]), expected_raw)
        and _numbers_close(float(item["weighted_raw"]), expected_raw * expected_weight)
    )


def _all_unique_strings(values: list[Any]) -> bool:
    return (
        all(isinstance(item, str) and bool(item) for item in values)
        and len(values) == len(set(values))
    )


def _bounded_number(value: Any, *, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _valid_sha256_ref(value: Any) -> bool:
    return isinstance(value, str) and SHA256_REF_PATTERN.fullmatch(value) is not None


def _valid_evidence_hash(evidence: Mapping[str, Any]) -> bool:
    digest = evidence.get("evidence_hash")
    if not _valid_sha256_ref(digest):
        return False
    canonical_evidence = {
        key: value for key, value in evidence.items() if key != "evidence_hash"
    }
    expected = "sha256:" + _sha256(_canonical_bytes(canonical_evidence))
    return digest == expected


def _numbers_close(left: float, right: float) -> bool:
    return abs(left - right) <= FLOAT_RECONCILIATION_TOLERANCE


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
