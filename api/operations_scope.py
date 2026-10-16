"""Pinned scope-registry validation for Customer Operations scans.

The projection is deliberately score-neutral.  It decides only whether a
submitted selection is executable and what coverage language may accompany
the resulting evidence.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


SCOPE_MAP_VERSION = "scanner_scope_map_v1"


class ScopeValidationError(ValueError):
    """A submitted scope does not match the pinned registry snapshot."""


def project_targeted_scope_registry(
    *, catalog: Mapping[str, Any], policy: Mapping[str, Any],
    policy_revision: str, registry_key: str,
    founder_decision: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Project an exact policy pack over an immutable released catalog.

    A catalog proves release/executability, not approval. Runtime activation
    requires a Founder decision independently bound to the exact policy
    revision and pack artifact hash.
    """
    if not policy_revision:
        raise ScopeValidationError("policy_revision_required")
    packs = policy.get("targeted_packs")
    if not isinstance(packs, Mapping) or not isinstance(packs.get(registry_key), Mapping):
        raise ScopeValidationError("targeted_pack_unknown")
    pack = dict(packs[registry_key])
    if pack.get("registry_key") != registry_key:
        raise ScopeValidationError("targeted_pack_registry_key_mismatch")
    artifact = pack.get("pack_artifact")
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("sha256"), str):
        raise ScopeValidationError("targeted_pack_artifact_pin_required")
    catalog_revision = _catalog_revision(catalog)
    expected_revision = (pack.get("catalog") or {}).get("registry_revision")
    if expected_revision != catalog_revision:
        raise ScopeValidationError("targeted_pack_catalog_revision_mismatch")
    family = (pack.get("catalog") or {}).get("family")
    family_revision = (pack.get("catalog") or {}).get("family_revision")
    if not isinstance(family, str) or not isinstance(family_revision, str):
        raise ScopeValidationError("targeted_pack_family_pin_required")
    constraints = pack.get("supported_target_constraints")
    if not isinstance(constraints, Mapping) or not _nonempty_string_list(constraints.get("supported_adapters")):
        raise ScopeValidationError("supported_adapters_required")
    supported_adapters = list(constraints["supported_adapters"])
    catalog_rows = catalog.get("rows")
    if not isinstance(catalog_rows, Sequence) or isinstance(catalog_rows, (str, bytes)):
        raise ScopeValidationError("catalog_rows_invalid")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in catalog_rows:
        if not isinstance(row, Mapping):
            raise ScopeValidationError("catalog_row_invalid")
        case_id = row.get("case_id_or_probe_id")
        if isinstance(case_id, str) and row.get("corpus_or_family") == family:
            if case_id in by_id:
                raise ScopeValidationError("catalog_case_id_duplicate:" + case_id)
            by_id[case_id] = row
    approved = _founder_decision_approves(
        founder_decision, policy_revision=policy_revision, registry_key=registry_key,
        artifact_sha256=str(artifact["sha256"]),
    )
    projected: list[dict[str, Any]] = []
    for entry in _pack_case_entries(pack):
        case_id, case_revision = entry.get("case_id"), entry.get("case_revision")
        if not isinstance(case_id, str) or not isinstance(case_revision, str):
            raise ScopeValidationError("targeted_pack_case_pin_invalid")
        row = by_id.get(case_id)
        if row is None:
            raise ScopeValidationError("targeted_pack_case_missing:" + case_id)
        if row.get("stable_id") != case_revision.removeprefix("sha256:"):
            raise ScopeValidationError("targeted_pack_case_revision_mismatch:" + case_id)
        categories = row.get("categories") or {}
        supported = bool(
            row.get("corpus_or_family") == family
            and row.get("version_pin") == family_revision
            and categories.get("executable") is True
            and categories.get("executable_on_default_runtime") is True
            and row.get("worker_runner") in supported_adapters
        )
        projected.append({
            "case_id": case_id, "case_revision": case_revision, "scope_id": registry_key,
            "source_catalog_revision": catalog_revision, "source_policy_revision": policy_revision,
            "targeted_content_approved": approved, "targeted_supported": supported,
            "supported_adapters": supported_adapters,
            "reporting_status": "reported_not_scored", "numeric_score_eligible": False,
            "completion_verdicts": ["PASS", "FAIL"],
            "incomplete_verdicts": ["INCOMPLETE", "INVALID"],
            "evidence_semantics": "synthetic_marker_behavior_against_named_real_target",
        })
    return projected


def validate_scope_selection(
    selection: Mapping[str, Any],
    *,
    registry_revision: str,
    registry_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and normalize one selection against an exact registry.

    Registry rows name ``case_id`` and may name ``scope_id``; when scope_id is
    absent the rubric point is the stable scope identifier.  Targeted content
    remains held until a separately governed registry row explicitly sets
    ``targeted_content_approved=true``.
    """
    if not registry_revision:
        raise ScopeValidationError("registry_revision_required")
    if selection.get("registry_revision") != registry_revision:
        raise ScopeValidationError("registry_revision_mismatch")

    rows = [dict(row) for row in registry_rows]
    case_rows: dict[str, dict[str, Any]] = {}
    scope_cases: dict[str, set[str]] = {}
    for row in rows:
        case_id = row.get("case_id")
        scope_id = row.get("scope_id") or row.get("rubric_point_id")
        if not isinstance(case_id, str) or not case_id:
            raise ScopeValidationError("registry_case_id_invalid")
        if not isinstance(scope_id, str) or not scope_id:
            raise ScopeValidationError("registry_scope_id_invalid")
        if case_id in case_rows:
            raise ScopeValidationError("registry_case_id_duplicate")
        case_rows[case_id] = row
        scope_cases.setdefault(scope_id, set()).add(case_id)

    mode = selection.get("mode")
    if mode not in {"full", "targeted", "smoke", "scope_review"}:
        raise ScopeValidationError("scope_mode_invalid")
    scope_ids = _string_list(selection.get("scope_ids", []), "scope_ids_invalid")
    unknown_scopes = sorted(set(scope_ids) - set(scope_cases))
    if unknown_scopes:
        raise ScopeValidationError("scope_id_unknown:" + ",".join(unknown_scopes))

    submitted_cases = _string_list(
        selection.get("case_manifest", []), "case_manifest_invalid"
    )
    expected_cases = (
        set(case_rows)
        if mode in {"full", "smoke"} and not scope_ids
        else set().union(*(scope_cases[scope] for scope in scope_ids))
        if scope_ids
        else set()
    )
    if set(submitted_cases) != expected_cases or len(submitted_cases) != len(expected_cases):
        raise ScopeValidationError("case_manifest_mismatch")

    targeted_approved = bool(expected_cases) and all(
        bool(case_rows[case_id].get("targeted_content_approved"))
        for case_id in expected_cases
    )
    unsupported_cases = sorted(
        case_id for case_id in expected_cases
        if mode == "targeted" and not bool(case_rows[case_id].get("targeted_supported"))
    )
    held = mode == "targeted" and (not targeted_approved or bool(unsupported_cases))
    complete = mode == "full" and expected_cases == set(case_rows) and bool(case_rows)
    advisory = mode == "smoke"
    return {
        "schema_version": SCOPE_MAP_VERSION,
        "registry_revision": registry_revision,
        "mode": mode,
        "scope_ids": sorted(scope_ids),
        "case_manifest": sorted(submitted_cases),
        "requested_case_ids": sorted(expected_cases),
        # Compatibility is decided from these exact revisions, not from the
        # registry that happens to be active when an export is later read.
        # Older registries did not carry a per-case revision; for those rows
        # the immutable registry snapshot is the case revision.
        "case_revisions": {
            case_id: str(case_rows[case_id].get("case_revision") or registry_revision)
            for case_id in sorted(submitted_cases)
        },
        "complete": complete,
        "held": held,
        "held_reason": (
            "targeted_content_not_approved" if mode == "targeted" and not targeted_approved else
            "targeted_scope_requires_full_fallback" if unsupported_cases else None
        ),
        "unsupported_case_ids": unsupported_cases,
        "requires_full_fallback": bool(unsupported_cases),
        "advisory": advisory,
        "may_claim_full_score": complete,
        "unknown_impact_rule": (
            "published_full" if mode == "full" else
            "published_scope_review" if mode == "scope_review" else None
        ),
    }


def reconcile_scope_execution(
    scope: Mapping[str, Any], execution_rows: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Reconcile requested cases through attempted, terminal, and evidenced states."""
    requested = set(_string_list(scope.get("requested_case_ids", []), "requested_cases_invalid"))
    rows = [dict(row) for row in execution_rows]
    attempted = {str(row.get("case_id")) for row in rows if row.get("case_id")}
    terminal_states = {"complete", "completed", "passed", "failed", "pass", "fail", "error", "unsupported", "skipped", "incomplete", "invalid"}
    gradeable_states = {"complete", "completed", "passed", "failed", "pass", "fail"}
    terminal = {
        str(row["case_id"]) for row in rows
        if row.get("case_id") and str(row.get("status") or row.get("verdict") or "").lower() in terminal_states
    }
    gradeable = {
        str(row["case_id"]) for row in rows
        if row.get("case_id") and str(row.get("status") or row.get("verdict") or "").lower() in gradeable_states
    }
    ungradeable = terminal - gradeable
    evidenced = {
        str(row["case_id"]) for row in rows
        if row.get("case_id") and (
            row.get("evidence_ref") or row.get("evidence_refs") or row.get("artifact_uri")
        )
    }
    unexpected = attempted - requested
    complete = (
        requested == attempted == terminal == gradeable == evidenced
        and not unexpected
    )
    return {
        "requested": sorted(requested),
        "attempted": sorted(attempted),
        "terminal": sorted(terminal),
        "scorable": sorted(gradeable),
        "unscorable": sorted(ungradeable),
        "gradeable": sorted(gradeable),
        "ungradeable": sorted(ungradeable),
        "evidenced": sorted(evidenced),
        "missing_attempted": sorted(requested - attempted),
        "missing_terminal": sorted(requested - terminal),
        "missing_evidence": sorted(requested - evidenced),
        "unexpected": sorted(unexpected),
        "complete": complete,
        "may_claim_full_score": bool(scope.get("complete")) and complete,
    }


def _catalog_revision(catalog: Mapping[str, Any]) -> str:
    metadata = catalog.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ScopeValidationError("catalog_metadata_invalid")
    revision, closure = metadata.get("probe_set_version"), metadata.get("source_closure_sha")
    if not isinstance(revision, str) or not isinstance(closure, str):
        raise ScopeValidationError("catalog_revision_invalid")
    return f"{revision}+source-closure-{closure}"


def _pack_case_entries(pack: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cases = pack.get("cases")
    if not isinstance(cases, Mapping):
        raise ScopeValidationError("targeted_pack_cases_invalid")
    entries: list[Mapping[str, Any]] = []
    for group in ("target_specific", "mandatory_cross_cutting"):
        value = cases.get(group)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ScopeValidationError("targeted_pack_cases_invalid")
        entries.extend(value)
    if not entries:
        raise ScopeValidationError("targeted_pack_cases_empty")
    return entries


def _nonempty_string_list(value: Any) -> bool:
    return bool(value) and isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and all(isinstance(item, str) and item for item in value)


def _founder_decision_approves(decision: Mapping[str, Any] | None, *, policy_revision: str, registry_key: str, artifact_sha256: str) -> bool:
    return bool(
        decision and decision.get("decision") == "approved"
        and decision.get("approver_role") == "Founder"
        and decision.get("policy_revision") == policy_revision
        and decision.get("registry_key") == registry_key
        and decision.get("pack_artifact_sha256") == artifact_sha256
    )


def _string_list(value: Any, error: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ScopeValidationError(error)
    return list(value)
