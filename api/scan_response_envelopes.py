"""Internal response-projection seam for Scanner API handlers."""
from __future__ import annotations

from typing import Any

from scanners.live_probe_orchestrator import ScanResult


def scan_result_to_dict(result: ScanResult) -> dict[str, Any]:
    return {
        "scan_id": result.scan_id,
        "status": result.status,
        "scanner_version": result.scanner_version,
        "corpus_version": result.corpus_version,
        "rows_emitted": result.rows_emitted,
        "k_replicates": result.k_replicates,
        "live_probe_variance_band": result.live_probe_variance_band,
        "spread_per_dim": dict(result.spread_per_dim),
        "pillar_spread": dict(result.pillar_spread),
        "total_spread": result.total_spread,
        "incomplete": result.incomplete,
        "incomplete_reason": result.incomplete_reason,
        "full_suite_manifest": result.full_suite_manifest,
        "anti_gaming": result.anti_gaming.to_dict(),
        "dim_results": [
            {"dim_id": d.dim_id, "raw": d.raw, "spread": d.spread, "replicates": list(d.replicates)}
            for d in result.dim_results
        ],
    }


def uw_payload(result: ScanResult) -> dict[str, Any]:
    return scan_result_to_dict(result)


def customer_payload(result: ScanResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scan_id": result.scan_id,
        "status": result.status,
        "scanner_version": result.scanner_version,
        "corpus_version": result.corpus_version,
        "are_total": "_phase1_pending",
        "band": "_phase1_pending",
        "confidence": None,
        "confidence_below_adjudication_threshold": bool(result.incomplete),
        "evidence_refs": [],
        "partial_completion": bool(result.incomplete),
        "prior_scan_refs": [],
        "purpose": None,
        "rubric_version": "_phase1_pending",
        "subscores": "_phase1_pending",
        "uncovered_dimensions": [],
        "incomplete": result.incomplete,
    }
    if result.incomplete_reason is not None:
        payload["incomplete_reason"] = result.incomplete_reason
    return payload
