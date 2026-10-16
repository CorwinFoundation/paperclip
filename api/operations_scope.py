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
    terminal_states = {"complete", "completed", "passed", "failed", "error", "unsupported", "skipped"}
    scorable_states = {"complete", "completed", "passed", "failed"}
    terminal = {
        str(row["case_id"]) for row in rows
        if row.get("case_id") and str(row.get("status") or "").lower() in terminal_states
    }
    scorable = {
        str(row["case_id"]) for row in rows
        if row.get("case_id") and str(row.get("status") or "").lower() in scorable_states
    }
    unscorable = terminal - scorable
    evidenced = {
        str(row["case_id"]) for row in rows
        if row.get("case_id") and (
            row.get("evidence_ref") or row.get("evidence_refs") or row.get("artifact_uri")
        )
    }
    unexpected = attempted - requested
    complete = (
        requested == attempted == terminal == scorable == evidenced
        and not unexpected
    )
    return {
        "requested": sorted(requested),
        "attempted": sorted(attempted),
        "terminal": sorted(terminal),
        "scorable": sorted(scorable),
        "unscorable": sorted(unscorable),
        "evidenced": sorted(evidenced),
        "missing_attempted": sorted(requested - attempted),
        "missing_terminal": sorted(requested - terminal),
        "missing_evidence": sorted(requested - evidenced),
        "unexpected": sorted(unexpected),
        "complete": complete,
        "may_claim_full_score": bool(scope.get("complete")) and complete,
    }


def _string_list(value: Any, error: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ScopeValidationError(error)
    return list(value)
