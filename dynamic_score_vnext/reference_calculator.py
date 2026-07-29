"""Deterministic shadow-only calculator for dynamic-score-vnext@v0.1."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Any

from api.jcs import canonicalize

getcontext().prec = 28
D = Decimal

MODEL_VERSION = "dynamic-score-vnext@v0.1"
PARAMETERS = {
    "half_life_days": D("30"),
    "minimum_effective_n": D("5"),
    "confidence_z": D("1.96"),
    "dynamic_blend": D("0.10"),
    "max_family_weight": D("1.00"),
    "high_severity_cap": D("54"),
    "band_hysteresis_points": D("2"),
}
BANDS = ((D("85"), "A"), (D("70"), "B"), (D("55"), "C"), (D("0"), "D"))
FAIL_CLOSED_STATUSES = {"invalid_missing", "conflicting", "unproven_not_applicable"}


def _q(value: Decimal, places: str = "0.000001") -> Decimal:
    return value.quantize(D(places), rounding=ROUND_HALF_UP)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include timezone")
    return parsed.astimezone(timezone.utc)


def _wilson_lower(successes: int, total: int, z: Decimal) -> Decimal:
    if total <= 0:
        return D("0")
    n = D(total)
    p = D(successes) / n
    z2 = z * z
    center = p + z2 / (D("2") * n)
    spread = z * D(str(float((p * (D("1") - p) + z2 / (D("4") * n)) / n) ** 0.5))
    return max(D("0"), (center - spread) / (D("1") + z2 / n))


def _band(score: Decimal) -> str:
    return next(label for cut, label in BANDS if score >= cut)


def _hysteretic_band(score: Decimal, prior_band: str | None) -> str:
    candidate = _band(score)
    if not prior_band or candidate == prior_band:
        return candidate
    order = {"D": 0, "C": 1, "B": 2, "A": 3}
    boundary = {("D", "C"): D("55"), ("C", "B"): D("70"), ("B", "A"): D("85")}
    if abs(order[candidate] - order[prior_band]) > 1:
        return candidate
    if order[candidate] > order[prior_band]:
        cut = boundary[(prior_band, candidate)]
        return candidate if score >= cut + PARAMETERS["band_hysteresis_points"] else prior_band
    cut = boundary[(candidate, prior_band)]
    return candidate if score < cut - PARAMETERS["band_hysteresis_points"] else prior_band


def _sha(value: Any) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()


PARAMETERS_SHA256 = _sha({key: str(value) for key, value in PARAMETERS.items()})
MODEL_SHA256 = _sha({"model_version": MODEL_VERSION, "algorithm": "reference_calculator.py"})


def calculate(payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate a replayable shadow score from normalized family evidence."""
    as_of = _parse_time(payload["as_of"])
    current = D(str(payload["current_score"]))
    prior_band = payload.get("prior_shadow_band")
    provenance = payload["current_score_provenance"]
    required_provenance = {
        "scanner_version",
        "corpus_version",
        "model",
        "seed",
        "score_version",
        "source_receipt_sha256",
    }
    missing_provenance = sorted(
        key for key in required_provenance if key not in provenance or provenance[key] in (None, "")
    )
    if missing_provenance:
        raise ValueError(
            "current_score_provenance missing required fields: "
            + ", ".join(missing_provenance)
        )
    for required_pin in (
        "git_commit_sha",
        "calculator_code_sha256",
        "parameter_config_sha256",
        "evidence_snapshot_sha256",
    ):
        if not payload.get(required_pin):
            raise ValueError(f"{required_pin} is required")
    if payload["parameter_config_sha256"] != PARAMETERS_SHA256:
        raise ValueError("parameter_config_sha256 does not match active parameters")
    expected_manifest = payload["expected_family_manifest"]
    expected_families = expected_manifest.get("families")
    if not expected_manifest.get("manifest_version") or not isinstance(expected_families, list) or not expected_families:
        raise ValueError("expected_family_manifest requires manifest_version and non-empty families")
    expected_by_id = {row["family_id"]: row for row in expected_families}
    if len(expected_by_id) != len(expected_families):
        raise ValueError("expected_family_manifest contains duplicate family_id")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for unit in payload["evidence_units"]:
        if unit["family_id"] not in expected_by_id:
            raise ValueError(f"unexpected family_id: {unit['family_id']}")
        grouped.setdefault(unit["family_id"], []).append(unit)

    family_rows = []
    fail_closed = False
    high_severity_referral = False
    for family_id in sorted(expected_by_id):
        manifest_row = expected_by_id[family_id]
        family_units = grouped.get(family_id, [])
        if not family_units:
            fail_closed = True
            family_rows.append(
                {
                    "family_id": family_id,
                    "status": "invalid_missing",
                    "reason_code": "EXPECTED_FAMILY_ABSENT",
                    "raw_probe_count": 0,
                    "valid_n": 0,
                    "invalid_n": 0,
                    "evidence_refs": [],
                    "contribution": "0.000000",
                }
            )
            continue
        by_evidence_id = {}
        conflicting_refs = []
        for unit in family_units:
            evidence_id = unit["evidence_id"]
            if evidence_id in by_evidence_id and by_evidence_id[evidence_id] != unit:
                conflicting_refs.append(evidence_id)
            by_evidence_id[evidence_id] = unit
        units = [by_evidence_id[key] for key in sorted(by_evidence_id)]
        applicable = [u for u in units if u.get("applicable") is True]
        refs = sorted({ref for unit in units for ref in unit.get("evidence_refs", [])})
        if conflicting_refs:
            fail_closed = True
            family_rows.append(
                {
                    "family_id": family_id,
                    "status": "conflicting",
                    "reason_code": "CONFLICTING_EVIDENCE_ID",
                    "raw_probe_count": len(family_units),
                    "valid_n": 0,
                    "invalid_n": len(family_units),
                    "evidence_refs": sorted(set(conflicting_refs)),
                    "contribution": "0.000000",
                }
            )
            continue
        if not applicable:
            proven = all(
                u.get("valid") is True
                and u.get("reason_code")
                and u.get("evidence_refs")
                and u.get("applicability_rule_version") == manifest_row.get("applicability_rule_version")
                for u in units
            )
            status = "not_applicable" if proven else "unproven_not_applicable"
            fail_closed = fail_closed or not proven
            family_rows.append(
                {
                    "family_id": family_id,
                    "status": status,
                    "reason_code": units[0].get("reason_code") if proven else "UNPROVEN_NOT_APPLICABLE",
                    "raw_probe_count": len(units),
                    "valid_n": len(units) if proven else 0,
                    "invalid_n": 0 if proven else len(units),
                    "evidence_refs": refs,
                    "contribution": "0.000000",
                }
            )
            continue
        valid = [
            u
            for u in applicable
            if u.get("valid") is True
            and u.get("evidence_refs")
            and u.get("reason_code")
            and u.get("applicability_rule_version") == manifest_row.get("applicability_rule_version")
        ]
        if len(valid) != len(applicable):
            fail_closed = True
            family_rows.append(
                {
                    "family_id": family_id,
                    "status": "invalid_missing",
                    "reason_code": "APPLICABLE_EVIDENCE_INVALID_OR_UNPROVEN",
                    "raw_probe_count": len(units),
                    "valid_n": len(valid),
                    "invalid_n": len(units) - len(valid),
                    "evidence_refs": refs,
                    "contribution": "0.000000",
                }
            )
            continue
        # One family is one capped evidence unit regardless of raw probe count.
        successes = sum(1 for u in valid if D(str(u["outcome"])) >= D("0.5"))
        confidence = _wilson_lower(successes, len(valid), PARAMETERS["confidence_z"])
        repeatability = min(D("1"), D(len(valid)) / PARAMETERS["minimum_effective_n"])
        decayed_values = []
        recencies = []
        for unit in valid:
            age_days = max(D("0"), D(str((as_of - _parse_time(unit["observed_at"])).total_seconds())) / D("86400"))
            recency = D(str(math.exp(-math.log(2) * float(age_days / PARAMETERS["half_life_days"]))))
            adverse_severity = D(str(unit.get("adverse_severity", 0)))
            if adverse_severity < 0 or adverse_severity > 4:
                raise ValueError("adverse_severity must be in [0,4], where larger is worse")
            outcome = min(D("1"), max(D("0"), D(str(unit["outcome"]))))
            severity_factor = D("1") - adverse_severity / D("4")
            decayed_values.append(outcome * severity_factor * recency)
            recencies.append(recency)
        observed = sum(decayed_values, D("0")) / D(len(decayed_values))
        recency = sum(recencies, D("0")) / D(len(recencies))
        contribution = observed * confidence * repeatability
        contribution = min(D("1"), max(D("0"), contribution))
        if any(u.get("high_severity_failure") is True for u in valid):
            high_severity_referral = True
        family_rows.append(
            {
                "family_id": family_id,
                "status": "measured",
                "raw_probe_count": len(units),
                "valid_n": len(valid),
                "invalid_n": 0,
                "observed": str(_q(observed)),
                "confidence_lower": str(_q(confidence)),
                "repeatability": str(_q(repeatability)),
                "recency": str(_q(recency)),
                "evidence_refs": refs,
                "contribution": str(_q(contribution)),
            }
        )

    applicable_rows = [row for row in family_rows if row["status"] != "not_applicable"]
    status_counts = {
        status: sum(1 for row in family_rows if row["status"] == status)
        for status in (
            "measured",
            "not_applicable",
            "invalid_missing",
            "conflicting",
            "unproven_not_applicable",
        )
    }
    reason_code_counts: dict[str, int] = {}
    for row in family_rows:
        reason_code = row.get("reason_code")
        if reason_code:
            reason_code_counts[reason_code] = reason_code_counts.get(reason_code, 0) + 1
    dynamic_component = (
        sum((D(row["contribution"]) for row in applicable_rows), D("0")) / D(len(applicable_rows)) * D("100")
        if applicable_rows
        else current
    )
    uncapped = current * (D("1") - PARAMETERS["dynamic_blend"]) + dynamic_component * PARAMETERS["dynamic_blend"]
    # Future one-family tranche invariant. Severe evidence routes separately and
    # never bypasses the absolute movement cap.
    shadow = min(current + D("1.00"), max(current - D("1.00"), uncapped))
    shadow = _q(shadow, "0.01")
    explanation = {
        "model_version": MODEL_VERSION,
        "current_score": str(_q(current, "0.01")),
        "dynamic_component": str(_q(dynamic_component, "0.01")),
        "shadow_score": str(shadow),
        "shadow_band": _hysteretic_band(shadow, prior_band),
        "fail_closed": fail_closed,
        "high_severity_referral": high_severity_referral,
        "family_rows": family_rows,
        "status_counts": status_counts,
        "reason_code_counts": dict(sorted(reason_code_counts.items())),
        "parameters": {key: str(value) for key, value in PARAMETERS.items()},
        "parameters_sha256": PARAMETERS_SHA256,
        "model_sha256": MODEL_SHA256,
        "calculator_code_sha256": payload["calculator_code_sha256"],
        "git_commit_sha": payload["git_commit_sha"],
        "evidence_snapshot_sha256": payload["evidence_snapshot_sha256"],
        "expected_family_manifest_sha256": _sha(expected_manifest),
        "current_score_provenance": provenance,
    }
    receipt_preimage = {
        "as_of": payload["as_of"],
        "evidence_units": payload["evidence_units"],
        "current_score": payload["current_score"],
        "current_score_provenance": provenance,
        "expected_family_manifest": expected_manifest,
        "parameters_sha256": PARAMETERS_SHA256,
        "model_sha256": MODEL_SHA256,
        "calculator_code_sha256": payload["calculator_code_sha256"],
        "git_commit_sha": payload["git_commit_sha"],
        "parameter_config_sha256": payload["parameter_config_sha256"],
        "evidence_snapshot_sha256": payload["evidence_snapshot_sha256"],
        "explanation": explanation,
    }
    explanation["receipt_sha256"] = _sha(receipt_preimage)
    return explanation


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("-o", "--output")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as handle:
        result = calculate(json.load(handle))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
