"""Deterministic research-only Authority Frontier fixture evaluator.

Synthetic tolls and budgets have no financial or protection meaning.  This
module classifies and quotes immutable specifications; it never executes an
action, redeems a token, mutates a ledger, or asserts active protection.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable, Mapping

PACK_VERSION = "backbond.shadow-authority-fixtures.v3.0.0"
TAXONOMY_VERSION = "backbond.shadow-action-taxonomy.v3.0.0"
POLICY_VERSION = "backbond.shadow-authority-policy.v3.0.0"
ESTIMATOR_VERSION = "fixed-synthetic-toll.v1.0.0"
NO_PROTECTION = "NO_ACTIVE_PROTECTION_ASSERTION"

SAFE_DEFAULTS = {
    "read_only": "ALLOW_READ_ONLY",
    "additive_write": "DRAFT_WRITE",
    "modify_write": "DRY_RUN_DIFF",
    "destructive": "HUMAN_APPROVAL",
    "monetary_low": "DRAFT_TRANSACTION",
    "monetary_high": "HUMAN_APPROVAL",
    "external_commit": "INTENT_LOG_ONLY",
    "unclassified": "REQUIRE_MORE_EVIDENCE",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def canonicalize_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Lift aliases to their semantic operation before classification."""
    raw = dict(spec)
    operation = raw.get("operation")
    payload = raw.get("payload", {})
    if operation == "proxy" and isinstance(payload, Mapping):
        operation = payload.get("operation")
    return {
        "operation": operation,
        "resource": raw.get("resource"),
        "amount": raw.get("amount"),
        "external_commit": raw.get("external_commit", False),
    }


def derive_action_class(spec: Mapping[str, Any]) -> str:
    """Derive the total taxonomy from typed predicates, never caller claims."""
    typed = canonicalize_spec(spec)
    operation = typed["operation"]
    if operation == "read":
        return "read_only"
    if operation == "create":
        return "additive_write"
    if operation == "update":
        return "modify_write"
    if operation in {"delete", "destroy", "revoke"}:
        return "destructive"
    if operation == "transfer":
        amount = typed["amount"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            return "unclassified"
        return "monetary_low" if amount <= 100 else "monetary_high"
    if typed["external_commit"] is True or operation in {"publish", "send_external"}:
        return "external_commit"
    return "unclassified"


def _base_quote(action: Mapping[str, Any], budget: int) -> dict[str, Any]:
    spec = action.get("specification", {})
    derived = derive_action_class(spec) if isinstance(spec, Mapping) else "unclassified"
    reasons: list[str] = []
    pins = {
        "fixture_pack_version": PACK_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "policy_version": POLICY_VERSION,
        "estimator_version": ESTIMATOR_VERSION,
    }
    if any(action.get(key) != value for key, value in pins.items()):
        reasons.append("VERSION_PIN_MISMATCH")
    logical_time = action.get("logical_time")
    if not isinstance(logical_time, int) or isinstance(logical_time, bool):
        reasons.append("LOGICAL_TIME_INVALID")
    toll = action.get("synthetic_action_toll")
    if not isinstance(toll, int) or isinstance(toll, bool) or toll < 0:
        toll = None
        reasons.append("SYNTHETIC_TOLL_INVALID")
    if derived == "unclassified":
        reasons.append("ACTION_UNCLASSIFIED")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        reasons.append("SYNTHETIC_BUDGET_INVALID")
    return {
        "fixture_id": action.get("fixture_id"),
        "logical_time": logical_time,
        "caller_claimed_action_class": action.get("action_class"),
        "action_class": derived,
        "canonical_specification": canonicalize_spec(spec) if isinstance(spec, Mapping) else None,
        "canonical_action_hash": _sha(canonicalize_spec(spec)) if isinstance(spec, Mapping) else None,
        "recommended_safe_default": SAFE_DEFAULTS[derived],
        "synthetic_action_toll": toll,
        "reason_codes": sorted(reasons),
        **pins,
        "mode": "SHADOW_ONLY",
        "enforcement_effect": "NONE",
        "external_side_effect_count": 0,
        "protection_assertion": NO_PROTECTION,
    }


def replay(pack: Mapping[str, Any]) -> bytes:
    """Return a byte-stable ordered quote/frontier replay for one pinned pack."""
    budget = pack["replay_budget"]
    actions = sorted(pack["actions"], key=lambda a: (a["logical_time"], a["fixture_id"]))
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for action in actions:
        groups[str(action.get("boundary_id", action["fixture_id"]))].append(action)
    aggregate = {
        boundary: sum(action["synthetic_action_toll"] for action in members)
        for boundary, members in groups.items()
    }
    events = []
    cumulative = 0
    for action in actions:
        quote = _base_quote(action, budget)
        boundary = str(action.get("boundary_id", action["fixture_id"]))
        reserve = aggregate[boundary]
        cumulative += action["synthetic_action_toll"]
        valid = not quote["reason_codes"]
        preauthorized = valid and len(groups[boundary]) > 1
        quote["bundle_preauthorized"] = preauthorized
        quote["released"] = False
        quote["reason_codes"] = quote["reason_codes"] or [
            "BUNDLE_PREAUTHORIZED_SHADOW_ONLY" if preauthorized else "SHADOW_QUOTE_ELIGIBLE_NO_RELEASE"
        ]
        events.append({
            **quote,
            "boundary_id": boundary,
            "budget_before": budget,
            "budget_after": budget,
            "aggregate_reserve": reserve,
            "frontier_cumulative_toll": cumulative,
            "right_censored": cumulative > max(pack["budget_grid"]),
        })
    thresholds = []
    total = sum(action["synthetic_action_toll"] for action in actions)
    for point in pack["budget_grid"]:
        released_weight = sum(
            reserve for reserve in aggregate.values() if reserve <= point
        )
        fraction = 0 if total == 0 else min(released_weight, total) / total
        thresholds.append({"synthetic_risk_budget": point, "release_fraction": fraction})
    reached = next((p["synthetic_risk_budget"] for p in thresholds if p["release_fraction"] >= pack["release_threshold"]), None)
    result = {
        "contract_version": "are-scanner-api-v1@v1.3.0-unchanged",
        "fixture_pack_version": pack["fixture_pack_version"],
        "pack_hash": _sha(pack),
        "events": events,
        "frontier": {
            "points": thresholds,
            "threshold_budget_synthetic_units": reached,
            "right_censored": reached is None,
        },
        "ablations": pack["ablations"],
        "released_member_count": 0,
        "external_side_effect_count": 0,
        "enforcement_effect": "NONE",
        "protection_assertion": NO_PROTECTION,
    }
    return canonical_bytes(result)
