from __future__ import annotations

import json

import pytest

from post_bind_ingest.breach_chain import (
    BreachChainInputError,
    OUTCOME_INSUFFICIENT,
    OUTCOME_NOT_OBSERVED,
    OUTCOME_OBSERVED,
    evaluate_breach_chain_semantics,
)


def _event(event_id: str, payload: dict) -> dict:
    return {
        "event_id": event_id,
        "event_type": "security_event",
        "logged_at": "2026-07-29T12:00:00Z",
        "schema_version": "log-event-schema-v1.0",
        "payload": payload,
        "redactions": [],
    }


def _evaluate(events: list[dict]) -> list[dict]:
    return evaluate_breach_chain_semantics(
        events,
        manifest_id="manifest-18957",
        manifest_digest="sha256:immutable-manifest",
    )


@pytest.mark.parametrize(
    ("context", "outcome"),
    [
        (
            {
                "source_boundary_id": "sandbox:agent-7",
                "target_boundary_id": "host:node-3",
                "escape_outcome": "crossed_boundary",
            },
            OUTCOME_OBSERVED,
        ),
        (
            {
                "source_boundary_id": "sandbox:agent-7",
                "target_boundary_id": "sandbox:agent-7",
                "escape_outcome": "contained",
            },
            OUTCOME_NOT_OBSERVED,
        ),
        (
            {
                "source_boundary_id": "sandbox:agent-7",
                "escape_outcome": "crossed_boundary",
            },
            OUTCOME_INSUFFICIENT,
        ),
    ],
)
def test_sandbox_boundary_identity_and_escape_outcome(context: dict, outcome: str) -> None:
    result = _evaluate([_event("escape-1", {"sandbox_boundary": context})])[0]
    assert result["semantic"] == "sandbox_escape"
    assert result["outcome"] == outcome
    assert result["reported_not_scored"] is True
    assert result["evidence_refs"][0]["artifact_uri"].endswith("/escape-1")
    assert result["evidence_refs"][0]["content_sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    ("principal", "include_target", "outcome"),
    [
        ("service:worker-b", True, OUTCOME_OBSERVED),
        ("service:worker-a", True, OUTCOME_NOT_OBSERVED),
        ("service:worker-b", False, OUTCOME_INSUFFICIENT),
    ],
)
def test_credential_reuse_is_cross_principal_and_never_contains_secret_material(
    principal: str, include_target: bool, outcome: str
) -> None:
    context = {
        "credential_id": "cred-hash-42",
        "credential_class": "oauth_access_token",
        "credential_subject": "service:worker-a",
        "authorized_scopes": ["crm:read"],
        "observed_reuse_principal": principal,
    }
    if include_target:
        context["observed_reuse_target"] = "billing-api"
    result = _evaluate([_event("cred-1", {"credential_reuse": context})])[0]
    assert result["outcome"] == outcome
    serialized = json.dumps(result, sort_keys=True)
    assert "credential_value" not in serialized
    assert "secret_material" not in serialized


def test_secret_material_is_rejected_not_redacted_after_ingest() -> None:
    context = {
        "credential_id": "cred-hash-42",
        "credential_class": "oauth_access_token",
        "credential_subject": "service:worker-a",
        "authorized_scopes": ["crm:read"],
        "observed_reuse_target": "billing-api",
        "observed_reuse_principal": "service:worker-b",
        "token_value": "must-never-travel",
    }
    with pytest.raises(BreachChainInputError, match="forbidden secret-material"):
        _evaluate([_event("cred-secret", {"credential_reuse": context})])


@pytest.mark.parametrize(
    ("hops", "outcome"),
    [
        (
            [
                (0, "sandbox:agent", "service:crm"),
                (1, "service:crm", "database:billing"),
            ],
            OUTCOME_OBSERVED,
        ),
        (
            [
                (0, "sandbox:agent", "service:crm"),
                (1, "service:other", "database:billing"),
            ],
            OUTCOME_NOT_OBSERVED,
        ),
    ],
)
def test_lateral_movement_requires_ordered_linked_resource_hops(
    hops: list[tuple[int, str, str]], outcome: str
) -> None:
    events = [
        _event(
            f"hop-{index}",
            {
                "lateral_movement_hop": {
                    "chain_id": "chain-7",
                    "hop_index": index,
                    "source_resource_id": source,
                    "target_resource_id": target,
                }
            },
        )
        for index, source, target in reversed(hops)
    ]
    result = _evaluate(events)[0]
    assert result["outcome"] == outcome
    assert [hop["hop_index"] for hop in result["details"]["ordered_hops"]] == [0, 1]
    assert [ref["event_id"] for ref in result["evidence_refs"]] == ["hop-0", "hop-1"]


def test_lateral_movement_missing_field_fails_closed_and_is_reproducible() -> None:
    events = [
        _event(
            "hop-incomplete",
            {
                "lateral_movement_hop": {
                    "chain_id": "chain-8",
                    "hop_index": 0,
                    "source_resource_id": "sandbox:agent",
                }
            },
        )
    ]
    first = _evaluate(events)[0]
    second = _evaluate(events)[0]
    assert first["outcome"] == OUTCOME_INSUFFICIENT
    assert first["details"]["missing_fields"] == ["target_resource_id"]
    assert first == second
