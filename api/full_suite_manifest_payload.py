"""Canonical payload construction for full-suite execution manifests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from api.full_suite_manifest_integrity import attach_manifest_signature, registry_snapshot_hash
from api.full_suite_manifest_models import (
    FULL_SUITE_MANIFEST_VERSION,
    FULL_SUITE_RUNNER_VERSION,
    EvidenceUriResolver,
    FullSuiteCase,
    FullSuiteManifestError,
    ManifestSigner,
    normalize_case,
)
from scanners.live_probe_orchestrator import ScanRequest, ScanResult


def build_full_suite_manifest(
    *,
    scan_request: ScanRequest,
    scan_result: ScanResult,
    registry_cases: Iterable[FullSuiteCase],
    signer: ManifestSigner,
    evidence_uri_resolver: EvidenceUriResolver | None = None,
    skip_reasons: Mapping[str, str] | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    manifest_uri: str | None = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    cases = tuple(normalize_case(case) for case in registry_cases)
    if not cases:
        raise FullSuiteManifestError("registry_cases must not be empty")
    skip_reasons = dict(skip_reasons or {})
    evidence_uri_resolver = evidence_uri_resolver or _default_evidence_uri
    started_at = started_at or datetime.now(timezone.utc)
    finished_at = finished_at or datetime.now(timezone.utc)
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise FullSuiteManifestError("registry_cases contains duplicate case_id")

    dim_results_by_id = {dim.dim_id: dim for dim in scan_result.dim_results}
    rows: list[dict[str, Any]] = []
    findings: list[str] = []
    counts = {
        "expected": len(cases),
        "attempted": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errored": 0,
    }

    for case in cases:
        dim_result = dim_results_by_id.get(case.case_id)
        skip_reason = skip_reasons.get(case.case_id)
        if dim_result is None:
            if skip_reason is not None:
                row = _skip_row(case, skip_reason)
                row["evidence_uri"] = _default_evidence_uri(case.case_id, scan_request, scan_result)
                rows.append(row)
                counts["skipped"] += 1
                if skip_reason not in case.approved_skip_reasons:
                    counts["errored"] += 1
                    findings.append(f"skip_reason_unapproved:{case.case_id}:{skip_reason}")
                continue
            row = _error_row(case, "required_case_not_attempted")
            row["evidence_uri"] = _default_evidence_uri(case.case_id, scan_request, scan_result)
            rows.append(row)
            counts["errored"] += 1
            findings.append(f"required_case_not_attempted:{case.case_id}")
            continue

        counts["attempted"] += 1
        evidence_uri = evidence_uri_resolver(case.case_id, scan_request, scan_result)
        row = {
            "case_id": case.case_id,
            "rubric_point_id": case.rubric_point_id,
            "required": case.required,
            "outcome": "passed",
            "raw": dim_result.raw,
            "spread": dim_result.spread,
            "replicate_count": len(dim_result.replicates),
            "evidence_uri": evidence_uri or "",
        }
        if not evidence_uri:
            row["outcome"] = "errored"
            counts["errored"] += 1
            findings.append(f"missing_evidence_uri:{case.case_id}")
        else:
            counts["passed"] += 1
        rows.append(row)

    for case_id in sorted(set(dim_results_by_id) - set(case_by_id)):
        counts["attempted"] += 1
        counts["errored"] += 1
        findings.append(f"runner_count_mismatch:unexpected_case:{case_id}")
        rows.append({"case_id": case_id, "rubric_point_id": case_id, "required": False,
                     "outcome": "errored", "error": "unexpected_case_not_in_registry",
                     "evidence_uri": ""})

    required_without_clean_terminal = [
        row["case_id"] for row in rows
        if row.get("required") is True and row.get("outcome") not in {"passed", "skipped"}
    ]
    if required_without_clean_terminal:
        findings.append("runner_count_mismatch:required_cases_not_clean:" + ",".join(required_without_clean_terminal))

    manifest: dict[str, Any] = {
        "manifest_version": FULL_SUITE_MANIFEST_VERSION,
        "runner_version": FULL_SUITE_RUNNER_VERSION,
        "scan_id": scan_request.scan_id,
        "target": {"tenant_id": scan_request.tenant_id, "agent_id": scan_request.agent_id,
                   "agent_endpoint_host": _host_only(scan_request.agent_endpoint)},
        "scanner_version": scan_result.scanner_version,
        "corpus_version": scan_result.corpus_version,
        "registry_snapshot_hash": registry_snapshot_hash(cases),
        "seed": scan_request.seed,
        "model_versions": {},
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "counts": counts,
        "incomplete": bool(findings),
        "incomplete_reasons": findings,
        "cases": rows,
    }
    if manifest_uri:
        manifest["manifest_uri"] = manifest_uri
    return attach_manifest_signature(manifest, signer), tuple(findings)


def _default_evidence_uri(case_id: str, scan_request: ScanRequest, scan_result: ScanResult) -> str:
    del scan_result
    return f"scanner://evidence/{scan_request.scan_id}/{case_id}.json"


def _skip_row(case: FullSuiteCase, skip_reason: str) -> dict[str, Any]:
    return {"case_id": case.case_id, "rubric_point_id": case.rubric_point_id,
            "required": case.required, "outcome": "skipped", "skip_reason": skip_reason,
            "evidence_uri": ""}


def _error_row(case: FullSuiteCase, error: str) -> dict[str, Any]:
    return {"case_id": case.case_id, "rubric_point_id": case.rubric_point_id,
            "required": case.required, "outcome": "errored", "error": error,
            "evidence_uri": ""}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _host_only(endpoint: str) -> str:
    return endpoint.removeprefix("https://").removeprefix("http://").split("/", 1)[0]
