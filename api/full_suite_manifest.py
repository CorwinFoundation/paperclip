"""Full-suite execution manifest for BEAAA-13645.

Rule-execution guard: this module records runner completeness against the
active policy-required case registry. It does not change rubric weights,
scoring math, judge selection, or contract output fields.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import ScanRequest, ScanResult


FULL_SUITE_MANIFEST_VERSION = "full-suite-execution-manifest/v0.1"
FULL_SUITE_RUNNER_VERSION = "full-suite-runner/v0.1"
DEFAULT_ACTIVE_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "probe-registry"
    / "active_registry.json"
)

_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class FullSuiteManifestError(ValueError):
    pass


@dataclass(frozen=True)
class FullSuiteCase:
    case_id: str
    rubric_point_id: str
    required: bool = True
    approved_skip_reasons: tuple[str, ...] = ()

    def to_registry_row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "rubric_point_id": self.rubric_point_id,
            "required": self.required,
            "approved_skip_reasons": list(self.approved_skip_reasons),
        }


EvidenceUriResolver = Callable[[str, ScanRequest, ScanResult], str | None]
ManifestSigner = Callable[[dict[str, Any]], str]


def active_registry_from_rubric(rubric: Rubric) -> tuple[FullSuiteCase, ...]:
    """Build the current active-case registry from the loaded rubric.

    BEAAA-13645 is deliberately registry-source agnostic. Until the separate
    active registry task lands, the loaded rubric's active dimensions are the
    only authoritative code path that says what policy-required active rows
    exist.
    """
    cases: list[FullSuiteCase] = []
    for dim_id in rubric.active_dim_ids:
        cases.append(
            FullSuiteCase(
                case_id=dim_id,
                rubric_point_id=dim_id,
                required=True,
            )
        )
    return tuple(cases)


def active_registry_from_manifest(registry: Mapping[str, Any]) -> tuple[FullSuiteCase, ...]:
    """Build full-suite cases from the BEAAA-13678 active registry manifest.

    The manifest is the Workstream A source of truth. Every active rubric point
    is required here, including points currently marked
    ``fail_closed_unavailable``. That is the fail-closed underwriting behavior:
    unavailable active base coverage cannot be priced as complete.
    """
    points = registry.get("active_rubric_points")
    if not isinstance(points, list) or not points:
        raise FullSuiteManifestError("active_registry.active_rubric_points must be non-empty")
    cases: list[FullSuiteCase] = []
    seen: set[str] = set()
    for point in points:
        if not isinstance(point, Mapping):
            raise FullSuiteManifestError("active_registry.active_rubric_points rows must be objects")
        rubric_point_id = point.get("rubric_point_id")
        if not isinstance(rubric_point_id, str) or not rubric_point_id:
            raise FullSuiteManifestError("active registry point missing rubric_point_id")
        if rubric_point_id in seen:
            raise FullSuiteManifestError(f"duplicate active registry point: {rubric_point_id}")
        seen.add(rubric_point_id)
        cases.append(
            FullSuiteCase(
                case_id=rubric_point_id,
                rubric_point_id=rubric_point_id,
                required=True,
            )
        )
    return tuple(cases)


def active_registry_from_path(path: Path | str = DEFAULT_ACTIVE_REGISTRY_PATH) -> tuple[FullSuiteCase, ...]:
    """Load the in-repo active registry into full-suite case records."""
    registry_path = Path(path)
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FullSuiteManifestError(f"active registry not found: {registry_path}") from exc
    except json.JSONDecodeError as exc:
        raise FullSuiteManifestError(f"active registry is invalid JSON: {registry_path}: {exc}") from exc
    if not isinstance(registry, Mapping):
        raise FullSuiteManifestError("active registry root must be an object")
    return active_registry_from_manifest(registry)


def registry_snapshot_hash(cases: Iterable[FullSuiteCase]) -> str:
    rows = [_normalize_case(case).to_registry_row() for case in cases]
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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
    """Return ``(signed_manifest, findings)``.

    ``findings`` is empty only when every required active case has a completed
    result row or an approved explicit skip. Any finding must force an
    incomplete-scan state at the caller.
    """
    cases = tuple(_normalize_case(case) for case in registry_cases)
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
    counts = {"expected": len(cases), "attempted": 0, "passed": 0, "failed": 0, "skipped": 0, "errored": 0}

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
        outcome = "passed"
        row = {
            "case_id": case.case_id,
            "rubric_point_id": case.rubric_point_id,
            "required": case.required,
            "outcome": outcome,
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

    unknown_results = sorted(set(dim_results_by_id) - set(case_by_id))
    for case_id in unknown_results:
        counts["attempted"] += 1
        counts["errored"] += 1
        findings.append(f"runner_count_mismatch:unexpected_case:{case_id}")
        rows.append(
            {
                "case_id": case_id,
                "rubric_point_id": case_id,
                "required": False,
                "outcome": "errored",
                "error": "unexpected_case_not_in_registry",
                "evidence_uri": "",
            }
        )

    required_without_clean_terminal = [
        row["case_id"]
        for row in rows
        if row.get("required") is True and row.get("outcome") not in {"passed", "skipped"}
    ]
    if required_without_clean_terminal:
        findings.append(
            "runner_count_mismatch:required_cases_not_clean:"
            + ",".join(required_without_clean_terminal)
        )

    manifest: dict[str, Any] = {
        "manifest_version": FULL_SUITE_MANIFEST_VERSION,
        "runner_version": FULL_SUITE_RUNNER_VERSION,
        "scan_id": scan_request.scan_id,
        "target": {
            "tenant_id": scan_request.tenant_id,
            "agent_id": scan_request.agent_id,
            "agent_endpoint_host": _host_only(scan_request.agent_endpoint),
        },
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

    signed = dict(manifest)
    signed["signed_manifest"] = signer(manifest)
    return signed, tuple(findings)


def _normalize_case(case: FullSuiteCase) -> FullSuiteCase:
    if not isinstance(case.case_id, str) or not _CASE_ID_RE.match(case.case_id):
        raise FullSuiteManifestError(f"invalid case_id: {case.case_id!r}")
    if not isinstance(case.rubric_point_id, str) or not case.rubric_point_id:
        raise FullSuiteManifestError(f"invalid rubric_point_id for {case.case_id!r}")
    return case


def _default_evidence_uri(
    case_id: str,
    scan_request: ScanRequest,
    scan_result: ScanResult,
) -> str:
    return f"scanner://evidence/{scan_request.scan_id}/{case_id}.json"


def _skip_row(case: FullSuiteCase, skip_reason: str) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "rubric_point_id": case.rubric_point_id,
        "required": case.required,
        "outcome": "skipped",
        "skip_reason": skip_reason,
        "evidence_uri": "",
    }


def _error_row(case: FullSuiteCase, error: str) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "rubric_point_id": case.rubric_point_id,
        "required": case.required,
        "outcome": "errored",
        "error": error,
        "evidence_uri": "",
    }


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _host_only(endpoint: str) -> str:
    return endpoint.removeprefix("https://").removeprefix("http://").split("/", 1)[0]
