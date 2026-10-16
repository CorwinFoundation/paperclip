"""Customer Operations export and pair-specific comparison contract.

This module is intentionally a projection over persisted Scanner results.  It
does not calculate or modify scores.  Callers must supply the immutable pins
captured when the request identity was reserved.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


EXPORT_VERSION = "scan_export_v1"
INTEGRITY_ENVELOPE_VERSION = "scan_export_integrity_v1"


class OperationsExportError(ValueError):
    """The persisted result or its immutable pins are incomplete."""


def canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_export_integrity(export: Mapping[str, Any]) -> bool:
    """Independently verify the canonical payload covered by an export."""
    envelope = export.get("integrity_envelope")
    if not isinstance(envelope, Mapping):
        return False
    if envelope.get("version") != INTEGRITY_ENVELOPE_VERSION:
        return False
    covered = envelope.get("covered_payload")
    if not isinstance(covered, Mapping):
        return False
    expected = canonical_digest(covered)
    if envelope.get("digest") != expected:
        return False
    return dict(covered) == {
        key: value for key, value in export.items() if key != "integrity_envelope"
    }


def build_scan_export(
    *,
    tenant_id: str,
    scan_id: str,
    result_revision: str,
    requested_target: Mapping[str, Any],
    measured_target: Mapping[str, Any],
    release: Mapping[str, Any],
    methodology_revision: str,
    corpus_revision: str,
    registry_revision: str,
    case_revisions: Mapping[str, str],
    scope: Mapping[str, Any],
    evidence_refs: Sequence[str],
    integrity_refs: Sequence[str],
    persisted_result: Mapping[str, Any],
    correction_of: str | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic, tenant-bound export of one persisted revision."""
    required_strings = {
        "tenant_id": tenant_id,
        "scan_id": scan_id,
        "result_revision": result_revision,
        "methodology_revision": methodology_revision,
        "corpus_revision": corpus_revision,
        "registry_revision": registry_revision,
    }
    missing = sorted(key for key, value in required_strings.items() if not value)
    if missing:
        raise OperationsExportError("missing required pins: " + ",".join(missing))
    if not case_revisions:
        raise OperationsExportError("case_revisions must not be empty")
    if not isinstance(scope.get("complete"), bool):
        raise OperationsExportError("scope.complete must be boolean")

    export = {
        "version": EXPORT_VERSION,
        "tenant_id": tenant_id,
        "scan_id": scan_id,
        "result_revision": result_revision,
        "target": {
            "requested": dict(requested_target),
            "measured": dict(measured_target),
        },
        "pins": {
            "release": dict(release),
            "methodology_revision": methodology_revision,
            "corpus_revision": corpus_revision,
            "registry_revision": registry_revision,
            "case_revisions": dict(sorted(case_revisions.items())),
        },
        "scope": dict(scope),
        "evidence_refs": sorted(set(evidence_refs)),
        "integrity_refs": sorted(set(integrity_refs)),
        "result_digest": canonical_digest(persisted_result),
        "correction_of": correction_of,
        "supersedes": supersedes,
    }
    export["integrity_envelope"] = {
        "version": INTEGRITY_ENVELOPE_VERSION,
        "algorithm": "sha256",
        "canonicalization": "json-sort-keys-utf8-v1",
        "covered_payload": dict(export),
        "digest": canonical_digest(export),
    }
    return export


def comparison_eligibility(
    current: Mapping[str, Any], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare the exact two exported revisions, never a release-wide flag.

    Cases are comparable only when both exports pin the same case revision.
    Missing or changed cases are returned with deterministic exclusion reasons.
    """
    for name, export in (("current", current), ("baseline", baseline)):
        if export.get("version") != EXPORT_VERSION:
            raise OperationsExportError(f"{name} is not {EXPORT_VERSION}")
        if not export.get("result_revision"):
            raise OperationsExportError(f"{name} result_revision is required")
    if current.get("tenant_id") != baseline.get("tenant_id"):
        raise OperationsExportError("cross-tenant comparison denied")

    current_cases = dict(current.get("pins", {}).get("case_revisions") or {})
    baseline_cases = dict(baseline.get("pins", {}).get("case_revisions") or {})
    comparable: list[dict[str, str]] = []
    exclusions: list[dict[str, str]] = []
    for case_id in sorted(set(current_cases) | set(baseline_cases)):
        current_revision = current_cases.get(case_id)
        baseline_revision = baseline_cases.get(case_id)
        if current_revision is None:
            exclusions.append({"case_id": case_id, "reason": "missing_current"})
        elif baseline_revision is None:
            exclusions.append({"case_id": case_id, "reason": "missing_baseline"})
        elif current_revision != baseline_revision:
            exclusions.append({"case_id": case_id, "reason": "case_revision_changed"})
        else:
            comparable.append({"case_id": case_id, "case_revision": current_revision})

    return {
        "current": {
            "scan_id": current.get("scan_id"),
            "result_revision": current["result_revision"],
        },
        "baseline": {
            "scan_id": baseline.get("scan_id"),
            "result_revision": baseline["result_revision"],
        },
        "eligible": bool(comparable),
        "comparable_cases": comparable,
        "exclusions": exclusions,
    }
