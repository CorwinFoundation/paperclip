"""Minimal Portal v1 export anchor for post-bind OVP appendix.

BEAAA-5944: keep post-bind sink calculations in `tools/post-bind-ingest/`
and make the portal layer a pure internal-report renderer.
"""

from __future__ import annotations

import copy
import html
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_POST_BIND_INGEST_SRC = Path(__file__).resolve().parents[1] / "post-bind-ingest" / "src"
if str(_POST_BIND_INGEST_SRC) not in sys.path:
    sys.path.insert(0, str(_POST_BIND_INGEST_SRC))

from post_bind_ingest.score_invariants import ScoreInvariantsViolated, assert_score_invariants_clean
from post_bind_ingest.sinks import (
    CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS,
    CLAIMS_THRESHOLD_POLICY_REF,
    DETECTOR_BUNDLE_VERSION,
    LOG_SCHEMA_VERSION,
    OVP_FALLBACK_WINDOW_DAYS,
    OVP_IN_SCOPE_PERILS,
    OVP_METHODOLOGY_VERSION,
    OVP_VERSION,
    POST_BIND_SINKS_VERSION,
    build_claims_queue_item,
    build_per_customer_rescore_feed,
    build_portfolio_calibration_feed,
    canonical_events_from_signal_envelopes,
)

SCANNER_VERSION = "0.4.1"
CORPUS_VERSION = "0.2.0"
RUBRIC_VERSION = "0.2.0"
CONTRACT_VERSION = "are-scanner-api-v1@v1.0"


class PortalError(Exception):
    """Small structured exception matching the portal service contract."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _score_snapshot(score: dict[str, Any]) -> dict[str, Any]:
    return {
        "are_total": score.get("are_total"),
        "subscores": copy.deepcopy(score.get("subscores")),
        "band": score.get("band"),
        "confidence": score.get("confidence"),
    }


def _build_peril_rollup(score: dict[str, Any]) -> dict[str, Any]:
    """Build the internal rollup shape the OVP appendix extends."""

    counts = {"P4": 0, "P5": 0}
    for ref in score.get("evidence_refs", []):
        for peril in ref.get("perils", []):
            if peril in counts:
                counts[peril] += 1
    return {
        "rollup_version": "0.1",
        "covered_perils": [
            {
                "peril_code": "P4",
                "peril_name": "Hallucination",
                "evidence_count": counts["P4"],
            },
            {
                "peril_code": "P5",
                "peril_name": "Prompt injection / adversarial input",
                "evidence_count": counts["P5"],
            },
        ],
        "non_covered_regulatory_signal": {
            "bucket_name": "regulatory_response",
            "evidence_count": 0,
        },
        "uncovered_perils": [],
        "untagged_evidence_count": 0,
    }


@dataclass
class PortalService:
    """In-memory service sufficient for the OVP appendix export path."""

    dp_id: str = "dp-1"
    score: dict[str, Any] = field(default_factory=dict)
    signal_envelopes: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.score:
            self.score = {
                "scan_id": "scan-1",
                "are_total": 64,
                "subscores": {"GOVERNANCE": 15, "ROBUSTNESS": 16, "BIAS_FAIRNESS": 15, "OPS": 18},
                "band": "C",
                "confidence": 0.72,
                "evidence_refs": [
                    {"rubric_point_id": "ROB.P4", "test_id": "p4", "artifact_uri": "evidence://p4", "verdict": "partial", "weight": 1, "perils": ["P4"]},
                    {"rubric_point_id": "ROB.P5", "test_id": "p5", "artifact_uri": "evidence://p5", "verdict": "partial", "weight": 1, "perils": ["P5"]},
                ],
                "scanner_version": SCANNER_VERSION,
                "corpus_version": CORPUS_VERSION,
                "model_versions": {"primary": "fixture", "judges": []},
                "seed": "portal-v1-fixture",
                "run_timestamp": "2026-06-16T16:00:00Z",
            }

    def _ovp_now(self) -> datetime:
        return datetime(2026, 6, 16, 16, 0, tzinfo=UTC)

    def add_signal(self, signal: dict[str, Any]) -> None:
        self.signal_envelopes.append(signal)

    def _canonical_post_bind_events(self, dp_id: str | None = None) -> list[dict[str, Any]]:
        signals = [
            signal for signal in self.signal_envelopes
            if dp_id is None or signal.get("dp_id") == dp_id
        ]
        return list(canonical_events_from_signal_envelopes(signals))

    def list_claims_queue(self, dp_id: str | None = None) -> dict[str, Any]:
        items = []
        for event in self._canonical_post_bind_events(dp_id):
            item = build_claims_queue_item(event, pin_block=self._pin_block())
            if item["severity_tier"] in {"S2", "S3", "S4"}:
                items.append(item)
        return {
            "sink_version": POST_BIND_SINKS_VERSION,
            "consumer_contract": "read_only_underwriter_claims_architect",
            "manual_only": True,
            "policy_scope": {
                "threshold_policy_ref": CLAIMS_THRESHOLD_POLICY_REF,
                "threshold_policy_governed_perils": sorted(CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS),
                "non_governed_items_posture": "telemetry_manual_review_only",
                "queue_wide_conformance_claim": False,
            },
            "count": len(items),
            "items": items,
        }

    def get_per_customer_rescore_feed(self, dp_id: str) -> dict[str, Any]:
        return build_per_customer_rescore_feed(
            self._canonical_post_bind_events(dp_id),
            dp_id,
            end_dt=self._ovp_now(),
            pin_block=self._pin_block(),
        )

    def get_portfolio_calibration_feed(self) -> dict[str, Any]:
        return build_portfolio_calibration_feed(
            self._canonical_post_bind_events(),
            end_dt=self._ovp_now(),
            pin_block=self._pin_block(),
        )

    def get_underwriting_rollup(self, scan_id: str) -> dict[str, Any]:
        if scan_id != self.score.get("scan_id"):
            raise PortalError(404, "score_not_found", "Scan score not found.")
        rollup = _build_peril_rollup(self.score)
        rollup["observed_vs_predicted"] = self.build_observed_vs_predicted(scan_id, rollup, self.score)
        return rollup

    def build_observed_vs_predicted(
        self,
        scan_id: str,
        rollup: dict[str, Any],
        score: dict[str, Any],
    ) -> dict[str, Any]:
        pre_rollup = copy.deepcopy(rollup)
        pre_score = _score_snapshot(score)
        rescore = self.get_per_customer_rescore_feed(self.dp_id)
        post_score = _score_snapshot(score)
        try:
            assert_score_invariants_clean(pre_score, post_score)
        except ScoreInvariantsViolated as exc:
            raise PortalError(
                500,
                "observed_vs_predicted_invariant_violated",
                "Observed-vs-predicted append mutated score fields.",
            ) from exc
        if pre_rollup != rollup:
            raise PortalError(
                500,
                "observed_vs_predicted_invariant_violated",
                "Observed-vs-predicted append mutated rollup fields.",
            )
        by_code = {p["peril_code"]: p for p in rescore["perils"]}
        perils = []
        for code in OVP_IN_SCOPE_PERILS:
            peril = dict(by_code[code])
            peril.pop("signal_envelope_ids", None)
            perils.append(peril)
        return {
            "ovp_version": OVP_VERSION,
            "methodology_ticket_ref": "BEAAA-5818",
            "methodology_version": OVP_METHODOLOGY_VERSION,
            "block_disposition": "export_only_no_mutation",
            "taxonomy_pin": {"perils_in_scope": list(OVP_IN_SCOPE_PERILS)},
            "window": rescore["window"],
            "input_pin": {
                "bind_score_run_timestamp": score.get("run_timestamp"),
                "bind_scanner_version": score.get("scanner_version"),
                "bind_corpus_version": score.get("corpus_version"),
                "log_schema_version": LOG_SCHEMA_VERSION,
                "detector_bundle_version": DETECTOR_BUNDLE_VERSION,
            },
            "perils": perils,
            "shadow_total_delta_points": max(-6, sum(p["shadow_score_delta_points"] for p in perils)),
            "guardrails": {
                "upstream_rollup_fields_unchanged": True,
                "upstream_score_fields_unchanged": True,
                "contract_output_unchanged": True,
                "pricing_book_unchanged": True,
            },
        }

    def render_observed_vs_predicted_block(self, ovp: dict[str, Any]) -> str:
        rows = "".join(
            (
                f"<tr><td>{html.escape(p['peril_code'])}</td><td>{html.escape(p['peril_name'])}</td>"
                f"<td>{p['eligible_actions']}</td><td>{p['observed_event_count']}</td>"
                f"<td>{p['predicted_frequency_per_10k']:.2f}</td><td>{p['observed_frequency_per_10k']:.2f}</td>"
                f"<td>{p['credibility_weighted_observed_frequency_per_10k']:.2f}</td>"
                f"<td>{p['divergence_ratio']:.2f}</td><td>{html.escape(p['credibility_status'])}</td>"
                f"<td>{html.escape(p['rescore_recommendation'])}</td><td>{p['shadow_score_delta_points']}</td></tr>"
            )
            for p in ovp["perils"]
        )
        return (
            "<h2>Observed vs predicted drift (post-bind, export-only)</h2>"
            "<p>manual underwriting review only; no BackBond Score or pricing-book mutation.</p>"
            "<table><tr><th>Peril</th><th>Name</th><th>Eligible actions</th><th>Observed events</th>"
            "<th>Predicted / 10k</th><th>Observed / 10k</th><th>Credibility weighted / 10k</th>"
            "<th>Divergence</th><th>Credibility</th><th>Recommendation</th><th>Shadow delta</th></tr>"
            f"{rows}</table>"
            f"<p>Shadow total delta: {ovp['shadow_total_delta_points']} | "
            f"Guardrails: {html.escape(json.dumps(ovp['guardrails'], sort_keys=True))}</p>"
        )

    def _render_internal_report(self, title: str, score: dict[str, Any] | None = None) -> str:
        score = score or self.score
        rollup = _build_peril_rollup(score)
        ovp = self.build_observed_vs_predicted(score["scan_id"], rollup, score)
        return (
            f"<h1>{html.escape(title)}</h1>"
            f"<p>Contract: {CONTRACT_VERSION}</p>"
            f"<p>{score['are_total']} / 100 Band {html.escape(score['band'])}</p>"
            f"{self.render_observed_vs_predicted_block(ovp)}"
        )

    def _render_dp_report(self, title: str, score: dict[str, Any] | None = None) -> str:
        score = score or self.score
        return (
            f"<h1>{html.escape(title)}</h1>"
            f"<p>{score['are_total']} / 100 Band {html.escape(score['band'])}</p>"
            "<p>Assessment result, not bound insurance coverage.</p>"
        )

    def _pin_block(self) -> dict[str, Any]:
        return {
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
        }


def build_service() -> PortalService:
    return PortalService()
