"""Content-free reduction from the reference growth pack to production evidence.

The reference appliance evaluates the declarative growth pack locally, where content
is visible, and produces one determination per matched rule.  Nothing about those
matches may travel.  What travels is what this module builds: per-intent counters.

The reduction is deliberately narrow.  It accepts rule identifiers and integers —
never a determination object, never an event, never a field lifted off one.  Passing
a mapping that carries content keys fails closed rather than being filtered, so a
caller cannot quietly widen the boundary by handing over richer input.

A rule that ran and matched nothing is a zero, which is a `pass`.  A rule that did not
run is absent, which is an `indeterminate`.  Collapsing those two into a zero would
turn an unobserved peril into a clean bill of health, so `evaluated_rule_ids` is a
required argument rather than an inferred one.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from determination.growth_cel import GrowthCelPack, GrowthCelError, reject_growth_forbidden_keys


#: Reference pack rule id -> production peril intent.  One-for-one with
#: `growth_agent_pack.yaml`; the reference `growth.off_scope_channel` is the channel
#: instance of the ticket's broader off-scope-behavior peril and keeps that intent id.
GROWTH_RULE_TO_INTENT = {
    "growth.unauthorized_spend": "unauthorized_spend_without_confirmation",
    "growth.brand_safety": "brand_safety_signal",
    "growth.outreach_compliance": "outreach_compliance_missing",
    "growth.crm_data_misuse": "crm_data_misuse",
    "growth.impersonation": "impersonation_signal",
    "growth.off_scope_channel": "off_scope_behavior",
}

INTENT_TO_GROWTH_RULE = {intent: rule for rule, intent in GROWTH_RULE_TO_INTENT.items()}


def _require_counter(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise GrowthCelError(f"{label} must be a non-negative integer")
    return value


def reduce_to_evidence(
    pack: GrowthCelPack,
    *,
    evaluated_rule_ids: Iterable[str],
    match_counts: Mapping[str, int],
    baseline_counts: Mapping[str, int],
) -> dict[str, dict[str, int]]:
    """Build one evidence map per intent from partner-side counters.

    `match_counts` and `baseline_counts` are keyed by reference rule id.  Both are
    checked for content keys before anything is read out of them.
    """
    reject_growth_forbidden_keys(dict(match_counts), "$.match_counts")
    reject_growth_forbidden_keys(dict(baseline_counts), "$.baseline_counts")

    evaluated = set(evaluated_rule_ids)
    unknown = evaluated - set(GROWTH_RULE_TO_INTENT)
    if unknown:
        raise GrowthCelError(f"unknown reference rule ids: {', '.join(sorted(unknown))}")
    reported = set(match_counts) | set(baseline_counts)
    if not reported <= evaluated:
        stray = ", ".join(sorted(reported - evaluated))
        raise GrowthCelError(f"counters reported for rules that did not run: {stray}")

    fields = {rule.intent_id: (rule.violation_field, rule.baseline_field) for rule in pack.rules}
    evidence: dict[str, dict[str, int]] = {}
    for rule_id in sorted(evaluated):
        intent_id = GROWTH_RULE_TO_INTENT[rule_id]
        violation_field, baseline_field = fields[intent_id]
        entry = {
            violation_field: _require_counter(match_counts.get(rule_id, 0), f"{rule_id} matches"),
        }
        if rule_id in baseline_counts:
            entry[baseline_field] = _require_counter(
                baseline_counts[rule_id], f"{rule_id} baseline"
            )
        evidence[intent_id] = entry
    return evidence


def count_determinations(determinations: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Reduce reference-appliance determinations to per-rule match counts.

    Only `rule_id` is read.  The determination's remaining fields stay in the partner
    environment; nothing else is copied into the returned mapping.
    """
    counts: dict[str, int] = {}
    for determination in determinations:
        rule_id = determination["rule_id"]
        if rule_id not in GROWTH_RULE_TO_INTENT:
            continue
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return counts


__all__ = [
    "GROWTH_RULE_TO_INTENT",
    "INTENT_TO_GROWTH_RULE",
    "count_determinations",
    "reduce_to_evidence",
]
