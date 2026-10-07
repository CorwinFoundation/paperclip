"""Equivalent-form measurements for Starter and Full Scan (BEAAA-38988).

The input is deliberately product-neutral: callers supply private form manifests
and result rows after execution.  This module never logs scenario payloads.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt
from statistics import pstdev
from typing import Iterable, Mapping, Sequence


class InvalidComparabilitySample(ValueError):
    """A form/result pair is unsafe or incomplete for equivalence claims."""


@dataclass(frozen=True)
class FormRun:
    form_id: str
    run_id: str
    scenario_ids: tuple[str, ...]
    score: float
    results: tuple[Mapping[str, object], ...]
    replay_of_run_id: str | None = None
    product: str = "starter"


def _interval(successes: int, total: int) -> dict[str, float | int | None]:
    """95% Wilson interval, suitable for small calibration samples."""
    if total == 0:
        return {"n": 0, "rate": None, "lower_95": None, "upper_95": None}
    z = 1.959963984540054
    p = successes / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / d
    return {
        "n": total,
        "rate": round(p, 6),
        "lower_95": round(max(0.0, centre - margin), 6),
        "upper_95": round(min(1.0, centre + margin), 6),
    }


def _validate(run: FormRun) -> None:
    if not run.form_id or not run.run_id or not run.scenario_ids:
        raise InvalidComparabilitySample("form_id, run_id and scenario_ids are required")
    if len(run.scenario_ids) != len(set(run.scenario_ids)):
        raise InvalidComparabilitySample(f"{run.form_id}: duplicate scenario id")
    if run.product not in ("starter", "full"):
        raise InvalidComparabilitySample("product must be starter or full")
    for row in run.results:
        if row.get("calibration_status") != "calibrated":
            raise InvalidComparabilitySample(f"{run.run_id}: uncalibrated result")
        if row.get("quality_status") not in (None, "accepted"):
            raise InvalidComparabilitySample(f"{run.run_id}: degraded generated case")
        if row.get("answer_leakage") is True:
            raise InvalidComparabilitySample(f"{run.run_id}: answer leakage")
        if row.get("binding_status") not in (None, "current"):
            raise InvalidComparabilitySample(f"{run.run_id}: stale canary/answer binding")
        if row.get("family_id") in (None, "") or row.get("expected_weak") not in (True, False):
            raise InvalidComparabilitySample(f"{run.run_id}: family_id and expected_weak required")
        if row.get("detected") not in (True, False):
            raise InvalidComparabilitySample(f"{run.run_id}: detected must be boolean")


def measure_comparability(runs: Sequence[FormRun]) -> dict[str, object]:
    """Measure form overlap, new-form variance, replay variance and family rates.

    ``replay_of_run_id`` identifies exact seed/form replay.  Those score pairs
    are reported separately and never folded into independently assembled-form
    variance.
    """
    if len(runs) < 2:
        raise InvalidComparabilitySample("at least two runs are required")
    for run in runs:
        _validate(run)
    products = {run.product for run in runs}
    if len(products) != 1:
        raise InvalidComparabilitySample("Starter and Full Scan samples must be measured separately")
    by_id = {r.run_id: r for r in runs}
    if len(by_id) != len(runs):
        raise InvalidComparabilitySample("run_id values must be unique")

    fresh = [r for r in runs if r.replay_of_run_id is None]
    if len(fresh) < 2:
        raise InvalidComparabilitySample("at least two independently assembled forms are required")
    overlaps = []
    for i, left in enumerate(fresh):
        a = set(left.scenario_ids)
        for right in fresh[i + 1 :]:
            b = set(right.scenario_ids)
            overlaps.append(len(a & b) / len(a | b))

    replay_deltas = []
    for run in runs:
        if run.replay_of_run_id is not None:
            source = by_id.get(run.replay_of_run_id)
            if source is None or source.form_id != run.form_id or source.scenario_ids != run.scenario_ids:
                raise InvalidComparabilitySample(f"{run.run_id}: replay does not match source form")
            replay_deltas.append(abs(run.score - source.score))

    families: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for run in fresh:
        for row in run.results:
            family = families[str(row["family_id"])]
            weak, detected = bool(row["expected_weak"]), bool(row["detected"])
            family["weak_total" if weak else "strong_total"] += 1
            if detected:
                family["detected_weak" if weak else "false_alarm"] += 1

    family_report = {}
    for name, counts in sorted(families.items()):
        family_report[name] = {
            "detection": _interval(counts["detected_weak"], counts["weak_total"]),
            "false_alarm": _interval(counts["false_alarm"], counts["strong_total"]),
        }
    scores = [r.score for r in fresh]
    return {
        "status": "measured",
        "product": next(iter(products)),
        "independent_form_count": len(fresh),
        "scenario_overlap": {
            "pair_count": len(overlaps),
            "mean_jaccard": round(sum(overlaps) / len(overlaps), 6),
            "max_jaccard": round(max(overlaps), 6),
        },
        "new_form_score_variance": {
            "population_stddev": round(pstdev(scores), 6),
            "range": round(max(scores) - min(scores), 6),
            "min": min(scores),
            "max": max(scores),
        },
        "seed_replay_variance": {
            "pair_count": len(replay_deltas),
            "max_absolute_delta": round(max(replay_deltas), 6) if replay_deltas else None,
        },
        "families": family_report,
    }


def unavailable_measurement(
    product: str, *, required_families: Iterable[str], available_families: Iterable[str]
) -> dict[str, object]:
    """Record that real-form comparability cannot yet be measured.

    This is the fail-closed result when the scenario bank cannot assemble the
    predeclared family coverage.  It contains no substitute forms or inferred
    calibration metrics.
    """
    if product not in ("starter", "full"):
        raise InvalidComparabilitySample("product must be starter or full")
    required = {str(value) for value in required_families if str(value)}
    available = {str(value) for value in available_families if str(value)}
    missing = sorted(required - available)
    if not required or not missing:
        raise InvalidComparabilitySample("unavailable result requires missing family supply")
    return {
        "status": "unavailable",
        "product": product,
        "reason": "insufficient_scenario_supply",
        "required_family_count": len(required),
        "available_family_count": len(required & available),
        "missing_families": missing,
        "scenario_overlap": None,
        "new_form_score_variance": None,
        "seed_replay_variance": None,
        "families": None,
    }


__all__ = ["FormRun", "InvalidComparabilitySample", "measure_comparability", "unavailable_measurement"]
