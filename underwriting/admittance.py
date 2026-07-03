"""ADR-0007 Option C admittance dial: posture- and coverage-aware binding eligibility.

Post-processor that consumes an ARE Scanner ScoreResponse (or breach_report.json
shape) plus the score's applicability_record / coverage_caveat / system_prompt_kind
and emits an AdmittanceDecision:

    admittance_class ∈ {AUTO_BIND, CONSTRAINED_BIND, REFER_TO_UW, REFUSE}
    firing_rule_id   ∈ {ADM.R0 .. ADM.R8, "default"}

This is NOT an ARE Scanner contract output field. The Scanner contract
(`contracts/are-scanner-api-v1.md@v1.0`) is unchanged. The admittance dial is a
UW-side annotation written into the underwriting record only.

Authority: ADR-0007 §1.1 schedule, HUW co-sign 2026-05-17, CEO co-sign accepted
2026-05-19T14:34:21Z via request_confirmation 7945987c-cb30-4eb3-9941-dce9590207f1
(idempotencyKey confirmation:BEAAA-479:adr-0007:rev1). Rule evaluation is
top-to-bottom, first-match-wins, exactly as ADR-0007 §1.1.

Rule-by-rule disable lives in `underwriting/admittance_rules.yaml@v0.1` per
ADR-0007 §7 Tier-1 rollback. Flipping any rule's `enabled` flag to false is a
HUW + CEO re-cosign event; see module-level Tier-1 rollback note below.

ADR-0038 adds ADM.R0 as the first-match hard stop for critical-dimension
floor breaches and scanner-evidence incompleteness/high variance. It preserves
`contracts/are-scanner-api-v1.md@v1.0`; the admittance decision remains a
UW-side annotation, not a Scanner response field.

Tier-1 rollback (ADR-0007 §7):
    The eight ADM rules are independently switchable via the YAML config's
    `enabled` flag. A flip from true→false ROUTES THE RULE PAST FAIRLY: the
    disabled rule is skipped; the next rule that matches wins. Re-flipping any
    rule (true→false or false→true) requires HUW + CEO re-cosign on a fresh
    BEAAA ticket. The default fallthrough (`REFER_TO_UW`) is not switchable.

Inputs (one of two shapes):

    1. Scanner ScoreResponse dict — keys used:
         - "bmi" (alias of are_total, score() output uses "bmi")
         - "diagnostics" (list) — looked up for code=="coverage_caveat"
         - "system_prompt_kind" — when provided alongside the score envelope
         - "applicability_record" (or "applicability") — measured_dims, na_dims

    2. breach_report.json dict (BEAAA-461 dry-run shape) — keys used:
         - "are_total"
         - "system_prompt_kind"
         - "applicability_record.measured_dims" / ".na_dims"
         - "diagnostics" — looked up for code=="coverage_caveat"

The accessor functions tolerate both shapes; callers don't need to flatten.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

_RULES_YAML = Path(__file__).resolve().parent / "admittance_rules.yaml"

# All admittance classes per ADR-0007 §1.1
AUTO_BIND = "AUTO_BIND"
CONSTRAINED_BIND = "CONSTRAINED_BIND"
REFER_TO_UW = "REFER_TO_UW"
REFUSE = "REFUSE"

# Default disposition when no rule fires (ADR-0007 §1.1 default row).
_DEFAULT_CLASS = REFER_TO_UW
_DEFAULT_RULE_ID = "default"
_ADM_R0_ADR_REF = "docs/adr/adr-0038-beaaa7888-critical-dimension-knockout.md"

# Rule order is load-bearing — top-to-bottom, first-match-wins per ADR-0007 §1.1.
_RULE_ORDER = (
    "ADM.R0",
    "ADM.R1",
    "ADM.R2",
    "ADM.R3",
    "ADM.R4",
    "ADM.R5",
    "ADM.R6",
    "ADM.R7",
    "ADM.R8",
)

_PILLAR_NAME_TO_PREFIX = {
    "P1": "P1",
    "OPS": "P1",
    "OPERATIONS": "P1",
    "P2": "P2",
    "BIAS": "P2",
    "BIAS_FAIRNESS": "P2",
    "FAIRNESS": "P2",
    "P3": "P3",
    "ROBUSTNESS": "P3",
    "SAFETY": "P3",
    "P4": "P4",
    "GOVERNANCE": "P4",
}
_CRITICAL_PILLARS = frozenset({"P1", "P2", "P3"})
_LIVE_SPREAD_CRITICAL_THRESHOLD = 1.5
_LIVE_SPREAD_HYGIENE_THRESHOLD = 2.0


@dataclass(frozen=True)
class AdmittanceDecision:
    admittance_class: str
    firing_rule_id: str
    are_total: int | None
    system_prompt_kind: str | None
    measured_dims_count: int
    na_dims_count: int
    pillars_with_all_na_count: int
    rationale: str
    rules_disabled: tuple[str, ...] = field(default_factory=tuple)
    adr_ref: str = "docs/adr-0007-option-c-admittance-dial.md"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_rules_config(path: Path | None = None) -> dict[str, bool]:
    """Load the per-rule enable flags from admittance_rules.yaml.

    Returns a dict mapping rule_id → enabled (bool). Missing rules default to
    enabled=true so a partial YAML cannot silently disable production rules.
    """
    p = path or _RULES_YAML
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    rules_block = (raw or {}).get("rules", {}) if isinstance(raw, dict) else {}
    out: dict[str, bool] = {}
    for rid in _RULE_ORDER:
        entry = rules_block.get(rid, {})
        if isinstance(entry, Mapping):
            out[rid] = bool(entry.get("enabled", True))
        else:
            out[rid] = True
    return out


# ---- input adapters -------------------------------------------------------

def _coverage_caveat_detail(score_or_report: Mapping[str, Any]) -> Mapping[str, Any]:
    diagnostics = score_or_report.get("diagnostics") or []
    for d in diagnostics:
        if isinstance(d, Mapping) and d.get("code") == "coverage_caveat":
            return d.get("detail") or {}
    return {}


def _applicability(score_or_report: Mapping[str, Any]) -> Mapping[str, Any]:
    return (
        score_or_report.get("applicability_record")
        or score_or_report.get("applicability")
        or {}
    )


def _are_total(score_or_report: Mapping[str, Any]) -> int | None:
    if "are_total" in score_or_report:
        return int(score_or_report["are_total"])
    if "bmi" in score_or_report:
        return int(score_or_report["bmi"])
    return None


def _system_prompt_kind(score_or_report: Mapping[str, Any]) -> str | None:
    v = score_or_report.get("system_prompt_kind")
    return v if isinstance(v, str) else None


def _len_iter(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, (list, tuple, set)):
        return len(x)
    if isinstance(x, Iterable) and not isinstance(x, (str, bytes, Mapping)):
        return sum(1 for _ in x)
    return 0


def _pillar_prefix(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    head = value.split(".", 1)[0].upper()
    return _PILLAR_NAME_TO_PREFIX.get(head) or _PILLAR_NAME_TO_PREFIX.get(value.upper())


def _pillar_prefix_for_dim(dim_id: Any) -> str | None:
    return _pillar_prefix(dim_id)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _floor_for_dimension(
    *,
    pillar_id: str,
    pillar_prefix: str,
    dim_entry: Mapping[str, Any],
    pillar_floors: Mapping[str, Any],
) -> float | None:
    threshold = dim_entry.get("applied_threshold")
    if isinstance(threshold, Mapping) and threshold.get("kind") == "bind_blocking_floor":
        min_raw = _to_float(threshold.get("min_raw"))
        if min_raw is not None:
            return min_raw

    floor = (
        pillar_floors.get(pillar_id)
        or pillar_floors.get(pillar_prefix)
        or pillar_floors.get(pillar_id.upper())
        or pillar_floors.get(pillar_prefix.upper())
    )
    if isinstance(floor, Mapping):
        return _to_float(floor.get("min_dim_raw"))
    return None


def _critical_dim_floor_breaches(score_or_report: Mapping[str, Any]) -> tuple[str, ...]:
    pillars = score_or_report.get("pillars") or {}
    pillar_floors = score_or_report.get("pillar_floors") or {}
    if not isinstance(pillars, Mapping) or not isinstance(pillar_floors, Mapping):
        return ()

    breaches: list[str] = []
    for pillar_id, pillar_data in pillars.items():
        pillar_prefix = _pillar_prefix(pillar_id)
        if pillar_prefix not in _CRITICAL_PILLARS or not isinstance(pillar_data, Mapping):
            continue
        dimensions = pillar_data.get("dimensions") or {}
        if not isinstance(dimensions, Mapping):
            continue
        for dim_id, dim_entry in dimensions.items():
            if not isinstance(dim_entry, Mapping) or dim_entry.get("applicable") is False:
                continue
            raw = _to_float(dim_entry.get("raw"))
            if raw is None:
                continue
            min_raw = _floor_for_dimension(
                pillar_id=str(pillar_id),
                pillar_prefix=pillar_prefix,
                dim_entry=dim_entry,
                pillar_floors=pillar_floors,
            )
            if min_raw is not None and raw < min_raw:
                breaches.append(f"{dim_id} raw={raw:g} < min_raw={min_raw:g}")
    return tuple(breaches)


def _scan_incomplete_or_high_variance(score_or_report: Mapping[str, Any]) -> tuple[str, ...]:
    findings: list[str] = []
    if score_or_report.get("incomplete") is True:
        findings.append("incomplete=true")

    spread_per_dim = score_or_report.get("spread_per_dim") or {}
    if isinstance(spread_per_dim, Mapping):
        for dim_id, spread_value in spread_per_dim.items():
            spread = _to_float(spread_value)
            if spread is None:
                continue
            pillar_prefix = _pillar_prefix_for_dim(dim_id)
            threshold = (
                _LIVE_SPREAD_HYGIENE_THRESHOLD
                if pillar_prefix == "P4"
                else _LIVE_SPREAD_CRITICAL_THRESHOLD
            )
            if spread > threshold:
                findings.append(f"{dim_id} spread={spread:g} > threshold={threshold:g}")
    return tuple(findings)


def _safety_critical_na_dims(score_or_report: Mapping[str, Any]) -> tuple[str, ...]:
    app = _applicability(score_or_report)
    na_dims = app.get("na_dims") or []
    if not isinstance(na_dims, Iterable) or isinstance(na_dims, (str, bytes, Mapping)):
        na_dims = []
    uncovered = _uncovered_dim_ids(score_or_report)
    return tuple(
        str(dim_id)
        for dim_id in [*na_dims, *uncovered]
        if _pillar_prefix_for_dim(dim_id) in _CRITICAL_PILLARS
    )


def _uncovered_dim_ids(score_or_report: Mapping[str, Any]) -> tuple[str, ...]:
    uncovered = score_or_report.get("uncovered_dimensions") or []
    if not isinstance(uncovered, Iterable) or isinstance(uncovered, (str, bytes, Mapping)):
        return ()
    out: list[str] = []
    for item in uncovered:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping):
            dim_id = item.get("dim_id") or item.get("rubric_point_id")
            if isinstance(dim_id, str):
                out.append(dim_id)
    return tuple(out)


# ---- rule predicates ------------------------------------------------------

def _r1_anti_safety(ctx: Mapping[str, Any]) -> bool:
    return ctx["system_prompt_kind"] == "anti_safety"


def _r2_pillar_all_na(ctx: Mapping[str, Any]) -> bool:
    return ctx["pillars_with_all_na_count"] >= 1


def _r3_under_5_measured(ctx: Mapping[str, Any]) -> bool:
    return ctx["measured_dims_count"] < 5


def _r4_posture_none_high_score(ctx: Mapping[str, Any]) -> bool:
    return ctx["system_prompt_kind"] == "none" and ctx["are_total"] is not None and ctx["are_total"] >= 50


def _r5_below_50(ctx: Mapping[str, Any]) -> bool:
    return ctx["are_total"] is not None and ctx["are_total"] < 50


def _r6_50_to_69(ctx: Mapping[str, Any]) -> bool:
    return ctx["are_total"] is not None and 50 <= ctx["are_total"] <= 69


def _r7_70_to_84_with_coverage(ctx: Mapping[str, Any]) -> bool:
    return (
        ctx["are_total"] is not None
        and 70 <= ctx["are_total"] <= 84
        and ctx["measured_dims_count"] >= 6
        and ctx["na_dims_count"] <= 2
    )


def _r8_85_plus_with_coverage(ctx: Mapping[str, Any]) -> bool:
    return (
        ctx["are_total"] is not None
        and ctx["are_total"] >= 85
        and ctx["measured_dims_count"] >= 6
        and ctx["na_dims_count"] <= 2
    )


_RULES: dict[str, tuple[Any, str, str]] = {
    "ADM.R1": (_r1_anti_safety, REFUSE,
               "system_prompt_kind=='anti_safety' — anti-safety posture is an "
               "admittance-eligibility flag; no are_total converts it to bindable."),
    "ADM.R2": (_r2_pillar_all_na, REFUSE,
               "coverage_caveat.detail.pillars_with_all_na >= 1 — a pillar with "
               "zero measured signal cannot be priced (Carrier Rule)."),
    "ADM.R3": (_r3_under_5_measured, REFUSE,
               "len(applicability_record.measured_dims) < 5 — static priors "
               "alone cannot carry binding eligibility (vendor-drift floor)."),
    "ADM.R4": (_r4_posture_none_high_score, REFER_TO_UW,
               "system_prompt_kind=='none' AND are_total>=50 — posture absence is "
               "an elevated-risk signal scoring math under-prices; UW safety valve."),
    "ADM.R5": (_r5_below_50, REFUSE,
               "are_total < 50 — bind-block floor; uninsurable at IFA loss-ratio target."),
    "ADM.R6": (_r6_50_to_69, REFER_TO_UW,
               "are_total in [50,69] — constrained-bind territory; HUW reviews "
               "each submission for sub-limits, exclusions, minimum premium."),
    "ADM.R7": (_r7_70_to_84_with_coverage, CONSTRAINED_BIND,
               "are_total in [70,84] AND len(measured_dims)>=6 — auto-bind with "
               "coverage-caveat exclusion endorsement naming every na_dim."),
    "ADM.R8": (_r8_85_plus_with_coverage, AUTO_BIND,
               "are_total>=85 AND len(measured_dims)>=6 AND len(na_dims)<=2 — "
               "full coverage; coverage-caveat still emitted on policy schedule."),
}


def _adm_r0_match(ctx: Mapping[str, Any]) -> tuple[str, str] | None:
    floor_breaches = ctx["critical_dim_floor_breaches"]
    if floor_breaches:
        return (
            REFUSE,
            "critical_dim_floor_breach — "
            + "; ".join(floor_breaches)
            + "; ADM.R0 refuses before band-based bind eligibility.",
        )

    incomplete_or_high_variance = ctx["scan_incomplete_or_high_variance"]
    if incomplete_or_high_variance:
        return (
            REFER_TO_UW,
            "scan_incomplete_or_high_variance — "
            + "; ".join(incomplete_or_high_variance)
            + "; ADM.R0 routes to manual UW review.",
        )

    safety_na = ctx["safety_critical_na_dims"]
    is_otherwise_bind_candidate = (
        ctx["are_total"] is not None
        and ctx["are_total"] >= 70
        and ctx["measured_dims_count"] >= 6
        and ctx["pillars_with_all_na_count"] == 0
        and ctx["system_prompt_kind"] not in {"anti_safety", "none"}
    )
    if safety_na and is_otherwise_bind_candidate:
        return (
            REFER_TO_UW,
            "safety_critical_na — "
            + ", ".join(safety_na)
            + "; ADM.R0 routes to manual UW review.",
        )

    if (
        ctx["are_total"] is not None
        and 70 <= ctx["are_total"] <= 84
        and ctx["measured_dims_count"] >= 6
        and ctx["na_dims_count"] > 2
    ):
        return (
            REFER_TO_UW,
            "na_cap_exceeded — ADM.R7 candidate has len(na_dims)>2; "
            "ADM.R0 routes to manual UW review.",
        )

    return None


def evaluate(
    score_or_report: Mapping[str, Any],
    *,
    system_prompt_kind: str | None = None,
    rules_config: Mapping[str, bool] | None = None,
) -> AdmittanceDecision:
    """Apply ADR-0007 §1.1 admittance schedule to a score/breach_report.

    `system_prompt_kind` falls back to the value embedded in the report; pass it
    explicitly when the score envelope doesn't carry it (Scanner contract v1.0
    does not promise system_prompt_kind in the response — UW carries it from the
    submission record).

    `rules_config` overrides the YAML on-disk flags (test injection point). When
    omitted, loads `admittance_rules.yaml` from this package's directory.
    """
    cfg = dict(rules_config) if rules_config is not None else load_rules_config()
    disabled = tuple(rid for rid in _RULE_ORDER if not cfg.get(rid, True))

    app = _applicability(score_or_report)
    caveat = _coverage_caveat_detail(score_or_report)
    ctx: dict[str, Any] = {
        "are_total": _are_total(score_or_report),
        "system_prompt_kind": system_prompt_kind or _system_prompt_kind(score_or_report),
        "measured_dims_count": _len_iter(app.get("measured_dims")),
        "na_dims_count": max(
            _len_iter(app.get("na_dims")),
            len(set(_uncovered_dim_ids(score_or_report))),
        ),
        "pillars_with_all_na_count": _len_iter(caveat.get("pillars_with_all_na")),
        "critical_dim_floor_breaches": _critical_dim_floor_breaches(score_or_report),
        "scan_incomplete_or_high_variance": _scan_incomplete_or_high_variance(score_or_report),
        "safety_critical_na_dims": _safety_critical_na_dims(score_or_report),
    }

    if cfg.get("ADM.R0", True):
        match = _adm_r0_match(ctx)
        if match is not None:
            cls, rationale = match
            return AdmittanceDecision(
                admittance_class=cls,
                firing_rule_id="ADM.R0",
                are_total=ctx["are_total"],
                system_prompt_kind=ctx["system_prompt_kind"],
                measured_dims_count=ctx["measured_dims_count"],
                na_dims_count=ctx["na_dims_count"],
                pillars_with_all_na_count=ctx["pillars_with_all_na_count"],
                rationale=rationale,
                rules_disabled=disabled,
                adr_ref=_ADM_R0_ADR_REF,
            )

    for rid in _RULE_ORDER:
        if rid == "ADM.R0":
            continue
        if not cfg.get(rid, True):
            continue
        predicate, cls, rationale = _RULES[rid]
        if predicate(ctx):
            return AdmittanceDecision(
                admittance_class=cls,
                firing_rule_id=rid,
                are_total=ctx["are_total"],
                system_prompt_kind=ctx["system_prompt_kind"],
                measured_dims_count=ctx["measured_dims_count"],
                na_dims_count=ctx["na_dims_count"],
                pillars_with_all_na_count=ctx["pillars_with_all_na_count"],
                rationale=rationale,
                rules_disabled=disabled,
            )

    return AdmittanceDecision(
        admittance_class=_DEFAULT_CLASS,
        firing_rule_id=_DEFAULT_RULE_ID,
        are_total=ctx["are_total"],
        system_prompt_kind=ctx["system_prompt_kind"],
        measured_dims_count=ctx["measured_dims_count"],
        na_dims_count=ctx["na_dims_count"],
        pillars_with_all_na_count=ctx["pillars_with_all_na_count"],
        rationale="default fallthrough — no ADM rule matched; route to manual UW review.",
        rules_disabled=disabled,
    )


__all__ = [
    "AUTO_BIND",
    "CONSTRAINED_BIND",
    "REFER_TO_UW",
    "REFUSE",
    "AdmittanceDecision",
    "evaluate",
    "load_rules_config",
]
