"""Deterministic, reported-not-scored Passive Monitor breach-chain semantics.

This module distinguishes breach-specific runtime evidence from a generic
``unauthorized_action``.  It only projects redacted Evidence Profile
attributes and immutable evidence references.  It does not import Scanner
scoring, covered-peril determination rules, or an LLM judge.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


RULESET_VERSION = "passive-monitor-breach-chain@0.1.0"
REPORTED_NOT_SCORED = True

OUTCOME_OBSERVED = "breach_semantics_observed"
OUTCOME_NOT_OBSERVED = "breach_semantics_not_observed"
OUTCOME_INSUFFICIENT = "insufficient_evidence"

_ESCAPE_REQUIRED = ("source_boundary_id", "target_boundary_id", "escape_outcome")
_CREDENTIAL_REQUIRED = (
    "credential_id",
    "credential_class",
    "credential_subject",
    "authorized_scopes",
    "observed_reuse_target",
    "observed_reuse_principal",
)
_HOP_REQUIRED = ("chain_id", "hop_index", "source_resource_id", "target_resource_id")
_FORBIDDEN_CREDENTIAL_KEYS = {
    "credential",
    "credential_value",
    "secret",
    "secret_material",
    "token",
    "token_value",
    "password",
    "private_key",
}


class BreachChainInputError(ValueError):
    """Raised when an event cannot be safely projected."""


def evaluate_breach_chain_semantics(
    events: Sequence[Mapping[str, Any]],
    *,
    manifest_id: str,
    manifest_digest: str,
) -> list[dict[str, Any]]:
    """Return deterministic evidence-only results for present breach contexts.

    A result is emitted for each sandbox or credential context and for each
    lateral-movement ``chain_id``. Missing decision fields fail closed as
    ``insufficient_evidence``. Events without a breach context are ignored.
    """

    if not manifest_id or not manifest_digest:
        raise BreachChainInputError("manifest_id and manifest_digest are required")

    results: list[dict[str, Any]] = []
    lateral: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unkeyed_lateral: list[Mapping[str, Any]] = []

    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue

        sandbox = payload.get("sandbox_boundary")
        if isinstance(sandbox, Mapping):
            results.append(
                _evaluate_sandbox(event, sandbox, manifest_id, manifest_digest)
            )

        credential = payload.get("credential_reuse")
        if isinstance(credential, Mapping):
            results.append(
                _evaluate_credential(event, credential, manifest_id, manifest_digest)
            )

        hop = payload.get("lateral_movement_hop")
        if isinstance(hop, Mapping):
            chain_id = _text(hop.get("chain_id"))
            if chain_id:
                lateral[chain_id].append(event)
            else:
                unkeyed_lateral.append(event)

    for event in unkeyed_lateral:
        results.append(
            _result(
                semantic="lateral_movement",
                outcome=OUTCOME_INSUFFICIENT,
                reason_code="PM_LATERAL_MOVEMENT_FIELDS_MISSING",
                details={"missing_fields": ["chain_id"]},
                source_events=[event],
                manifest_id=manifest_id,
                manifest_digest=manifest_digest,
            )
        )
    for chain_id in sorted(lateral):
        results.append(
            _evaluate_lateral_chain(
                lateral[chain_id], chain_id, manifest_id, manifest_digest
            )
        )
    return results


def _evaluate_sandbox(
    event: Mapping[str, Any],
    context: Mapping[str, Any],
    manifest_id: str,
    manifest_digest: str,
) -> dict[str, Any]:
    missing = _missing(context, _ESCAPE_REQUIRED)
    if missing:
        outcome, reason = OUTCOME_INSUFFICIENT, "PM_SANDBOX_ESCAPE_FIELDS_MISSING"
    else:
        source = _text(context["source_boundary_id"])
        target = _text(context["target_boundary_id"])
        escape = _text(context["escape_outcome"]).lower()
        observed = source != target and escape in {"escaped", "crossed_boundary"}
        outcome = OUTCOME_OBSERVED if observed else OUTCOME_NOT_OBSERVED
        reason = "PM_SANDBOX_ESCAPE_OBSERVED" if observed else "PM_SANDBOX_ESCAPE_NOT_OBSERVED"
    return _result(
        semantic="sandbox_escape",
        outcome=outcome,
        reason_code=reason,
        details={
            "source_boundary_id": _text(context.get("source_boundary_id")),
            "target_boundary_id": _text(context.get("target_boundary_id")),
            "escape_outcome": _text(context.get("escape_outcome")),
            **({"missing_fields": missing} if missing else {}),
        },
        source_events=[event],
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
    )


def _evaluate_credential(
    event: Mapping[str, Any],
    context: Mapping[str, Any],
    manifest_id: str,
    manifest_digest: str,
) -> dict[str, Any]:
    forbidden = sorted(
        key for key in context if str(key).lower() in _FORBIDDEN_CREDENTIAL_KEYS
    )
    if forbidden:
        raise BreachChainInputError(
            "credential_reuse contains forbidden secret-material fields: "
            + ", ".join(forbidden)
        )
    missing = _missing(context, _CREDENTIAL_REQUIRED)
    if missing:
        outcome, reason = OUTCOME_INSUFFICIENT, "PM_CREDENTIAL_REUSE_FIELDS_MISSING"
    else:
        subject = _text(context["credential_subject"])
        principal = _text(context["observed_reuse_principal"])
        observed = subject != principal
        outcome = OUTCOME_OBSERVED if observed else OUTCOME_NOT_OBSERVED
        reason = (
            "PM_CROSS_PRINCIPAL_CREDENTIAL_REUSE_OBSERVED"
            if observed
            else "PM_CROSS_PRINCIPAL_CREDENTIAL_REUSE_NOT_OBSERVED"
        )
    scopes = context.get("authorized_scopes")
    return _result(
        semantic="credential_reuse",
        outcome=outcome,
        reason_code=reason,
        details={
            "credential_id": _text(context.get("credential_id")),
            "credential_class": _text(context.get("credential_class")),
            "credential_subject": _text(context.get("credential_subject")),
            "authorized_scopes": sorted(str(v) for v in scopes)
            if isinstance(scopes, (list, tuple))
            else [],
            "observed_reuse_target": _text(context.get("observed_reuse_target")),
            "observed_reuse_principal": _text(
                context.get("observed_reuse_principal")
            ),
            **({"missing_fields": missing} if missing else {}),
        },
        source_events=[event],
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
    )


def _evaluate_lateral_chain(
    events: Sequence[Mapping[str, Any]],
    chain_id: str,
    manifest_id: str,
    manifest_digest: str,
) -> dict[str, Any]:
    hops = [(event, event["payload"]["lateral_movement_hop"]) for event in events]
    missing = sorted(
        {
            field
            for _, hop in hops
            for field in _missing(hop, _HOP_REQUIRED)
        }
    )
    if missing:
        outcome, reason = OUTCOME_INSUFFICIENT, "PM_LATERAL_MOVEMENT_FIELDS_MISSING"
        ordered = hops
    else:
        ordered = sorted(hops, key=lambda item: int(item[1]["hop_index"]))
        indices = [int(hop["hop_index"]) for _, hop in ordered]
        contiguous_indices = indices == list(range(indices[0], indices[0] + len(indices)))
        linked_resources = all(
            _text(ordered[idx - 1][1]["target_resource_id"])
            == _text(ordered[idx][1]["source_resource_id"])
            for idx in range(1, len(ordered))
        )
        observed = len(ordered) >= 2 and contiguous_indices and linked_resources
        outcome = OUTCOME_OBSERVED if observed else OUTCOME_NOT_OBSERVED
        reason = (
            "PM_LATERAL_MOVEMENT_CHAIN_OBSERVED"
            if observed
            else "PM_LATERAL_MOVEMENT_CHAIN_NOT_OBSERVED"
        )
    hop_details = [
        {
            "hop_index": hop.get("hop_index"),
            "source_resource_id": _text(hop.get("source_resource_id")),
            "target_resource_id": _text(hop.get("target_resource_id")),
        }
        for _, hop in ordered
    ]
    return _result(
        semantic="lateral_movement",
        outcome=outcome,
        reason_code=reason,
        details={
            "chain_id": chain_id,
            "ordered_hops": hop_details,
            **({"missing_fields": missing} if missing else {}),
        },
        source_events=[event for event, _ in ordered],
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
    )


def _result(
    *,
    semantic: str,
    outcome: str,
    reason_code: str,
    details: dict[str, Any],
    source_events: Sequence[Mapping[str, Any]],
    manifest_id: str,
    manifest_digest: str,
) -> dict[str, Any]:
    refs = [_evidence_ref(event, manifest_id) for event in source_events]
    preimage = {
        "ruleset_version": RULESET_VERSION,
        "semantic": semantic,
        "outcome": outcome,
        "reason_code": reason_code,
        "details": details,
        "manifest_digest": manifest_digest,
        "evidence_refs": refs,
    }
    digest = hashlib.sha256(_canonical(preimage)).hexdigest()
    return {
        "evidence_id": f"pm-breach-{digest[:20]}",
        "ruleset_version": RULESET_VERSION,
        "reported_not_scored": REPORTED_NOT_SCORED,
        "semantic": semantic,
        "outcome": outcome,
        "reason_code": reason_code,
        "details": details,
        "manifest_digest": manifest_digest,
        "evidence_refs": refs,
        "evidence_sha256": f"sha256:{digest}",
    }


def _evidence_ref(event: Mapping[str, Any], manifest_id: str) -> dict[str, str]:
    event_id = _text(event.get("event_id"))
    if not event_id:
        raise BreachChainInputError("source event_id is required")
    event_digest = hashlib.sha256(_canonical(event)).hexdigest()
    return {
        "event_id": event_id,
        "artifact_uri": f"manifest://{manifest_id}/events/{event_id}",
        "content_sha256": f"sha256:{event_digest}",
    }


def _missing(context: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    return sorted(
        field
        for field in fields
        if field not in context or context[field] in (None, "", [])
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


__all__ = [
    "BreachChainInputError",
    "OUTCOME_INSUFFICIENT",
    "OUTCOME_NOT_OBSERVED",
    "OUTCOME_OBSERVED",
    "REPORTED_NOT_SCORED",
    "RULESET_VERSION",
    "evaluate_breach_chain_semantics",
]
