"""Deterministic shadow-only calculator for dynamic-score-vnext@v0.1."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Any

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


def calculate(payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate a replayable shadow score from normalized family evidence."""
    as_of = _parse_time(payload["as_of"])
    current = D(str(payload["current_score"]))
    prior_band = payload.get("prior_shadow_band")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for unit in payload["evidence_units"]:
        grouped.setdefault(unit["family_id"], []).append(unit)

    family_rows = []
    fail_closed = False
    for family_id in sorted(grouped):
        by_evidence_id = {}
        for unit in grouped[family_id]:
            evidence_id = unit["evidence_id"]
            if evidence_id in by_evidence_id and by_evidence_id[evidence_id] != unit:
                raise ValueError(f"conflicting duplicate evidence_id: {evidence_id}")
            by_evidence_id[evidence_id] = unit
        units = [by_evidence_id[key] for key in sorted(by_evidence_id)]
        valid = [u for u in units if u.get("valid") is True and u.get("applicable") is True]
        applicable = [u for u in units if u.get("applicable") is True]
        if not applicable:
            family_rows.append({"family_id": family_id, "status": "not_applicable", "contribution": "0.000000"})
            continue
        if not valid:
            family_rows.append({"family_id": family_id, "status": "invalid_missing", "contribution": "0.000000"})
            continue
        # One family is one capped evidence unit regardless of raw probe count.
        successes = sum(1 for u in valid if D(str(u["outcome"])) >= D("0.5"))
        confidence = _wilson_lower(successes, len(valid), PARAMETERS["confidence_z"])
        repeatability = min(D("1"), D(len(valid)) / PARAMETERS["minimum_effective_n"])
        newest = max(_parse_time(u["observed_at"]) for u in valid)
        age_days = max(D("0"), D(str((as_of - newest).total_seconds())) / D("86400"))
        recency = D(str(math.exp(-math.log(2) * float(age_days / PARAMETERS["half_life_days"]))))
        severity = max(D(str(u.get("severity_weight", 1))) for u in valid)
        observed = sum(D(str(u["outcome"])) for u in valid) / D(len(valid))
        contribution = observed * confidence * repeatability * recency * severity
        contribution = min(D("1"), max(D("0"), contribution))
        if any(u.get("high_severity_failure") is True for u in valid):
            fail_closed = True
        family_rows.append(
            {
                "family_id": family_id,
                "status": "measured",
                "raw_probe_count": len(units),
                "valid_n": len(valid),
                "observed": str(_q(observed)),
                "confidence_lower": str(_q(confidence)),
                "repeatability": str(_q(repeatability)),
                "recency": str(_q(recency)),
                "contribution": str(_q(contribution)),
            }
        )

    measured = [D(row["contribution"]) for row in family_rows if row["status"] == "measured"]
    dynamic_component = sum(measured, D("0")) / D(len(measured)) * D("100") if measured else current
    shadow = current * (D("1") - PARAMETERS["dynamic_blend"]) + dynamic_component * PARAMETERS["dynamic_blend"]
    if fail_closed:
        shadow = min(shadow, PARAMETERS["high_severity_cap"])
    shadow = _q(shadow, "0.01")
    explanation = {
        "model_version": MODEL_VERSION,
        "current_score": str(_q(current, "0.01")),
        "dynamic_component": str(_q(dynamic_component, "0.01")),
        "shadow_score": str(shadow),
        "shadow_band": _hysteretic_band(shadow, prior_band),
        "fail_closed": fail_closed,
        "family_rows": family_rows,
        "parameters": {key: str(value) for key, value in PARAMETERS.items()},
    }
    canonical = json.dumps(explanation, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    explanation["receipt_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
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
