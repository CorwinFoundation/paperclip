"""BEAAA-5867 - Phase 2.0 post-bind sinks + observed-vs-predicted appendix.

Rule-execution, export-only plumbing:
  - claims queue sink for S2+ exposure events
  - per-customer rescore feed, manual-only
  - portfolio calibration feed, export-only
  - observed_vs_predicted appendix block on the underwriting rollup

No are-scanner-api-v1@v1.0 Output mutation. All methods derive from persisted
signal_envelopes and the already-persisted BackBond Score row.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_POST_BIND_INGEST_SRC = Path(__file__).resolve().parents[1] / "post-bind-ingest" / "src"
if str(_POST_BIND_INGEST_SRC) not in sys.path:
    sys.path.insert(0, str(_POST_BIND_INGEST_SRC))

from post_bind_ingest.sinks import (
    CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS,
    CLAIMS_THRESHOLD_POLICY_REF,
    DETECTOR_BUNDLE_VERSION,
    LOG_SCHEMA_PATCH_VERSION,
    LOG_SCHEMA_VERSION,
    OVP_CALIBRATION_DATE,
    OVP_FALLBACK_WINDOW_DAYS,
    OVP_IN_SCOPE_PERILS,
    OVP_METHODOLOGY_VERSION,
    OVP_PRIMARY_WINDOW_DAYS,
    OVP_VERSION,
    POST_BIND_SINKS_VERSION,
    build_claims_queue_item as _sink_build_claims_queue_item,
    build_per_customer_rescore_feed as _sink_build_per_customer_rescore_feed,
    build_portfolio_calibration_feed as _sink_build_portfolio_calibration_feed,
    canonical_events_from_signal_envelopes,
)
from portal import (
    CORPUS_VERSION,
    PortalError,
    PortalService,
    RUBRIC_VERSION,
    SCANNER_VERSION,
    canonical_json,
    html,
    now_iso,
    sha256_hex,
)
from telemetry_v0_1 import EVENT_RULE_CORPUS_VERSION, TELEMETRY_API_VERSION


def _sink_pin_block() -> dict[str, Any]:
    return {
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "telemetry_api_version": TELEMETRY_API_VERSION,
        "event_rule_corpus_version": EVENT_RULE_CORPUS_VERSION,
    }


def _init_post_bind_sink_tables(self: PortalService) -> None:
    self.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS claims_queue_items (
          id TEXT PRIMARY KEY,
          dp_id TEXT NOT NULL,
          signal_envelope_id TEXT NOT NULL UNIQUE,
          peril_code TEXT NOT NULL,
          severity_tier TEXT NOT NULL,
          produced_at TEXT NOT NULL,
          item_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS claims_queue_items_dp_time
          ON claims_queue_items (dp_id, produced_at);
        """
    )
    self.db.commit()


def _process_post_bind_signal_envelopes(self: PortalService, signals: list[dict[str, Any]]) -> None:
    for event in canonical_events_from_signal_envelopes(signals):
        item = _sink_build_claims_queue_item(event, pin_block=_sink_pin_block())
        if item["severity_tier"] not in {"S2", "S3", "S4"}:
            continue
        self.db.execute(
            "INSERT OR IGNORE INTO claims_queue_items "
            "(id, dp_id, signal_envelope_id, peril_code, severity_tier, produced_at, item_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item["claims_queue_item_id"],
                item["dp_id"],
                item["signal_envelope_id"],
                item["peril_code"],
                item["severity_tier"],
                item["produced_at"],
                json.dumps(item, sort_keys=True),
            ),
        )
        self.audit(
            "portal",
            "claims_queue_item_emitted",
            "claims_queue_item",
            item["claims_queue_item_id"],
            {
                "payload_hash": sha256_hex(canonical_json(item)),
                "dp_id": item["dp_id"],
                "peril_code": item["peril_code"],
                "severity_tier": item["severity_tier"],
                "threshold_policy_governed": item["threshold_policy_governed"],
                "telemetry_manual_review_only": item["telemetry_manual_review_only"],
            },
        )
    self.db.commit()


def _ovp_now(self: PortalService) -> datetime:
    return self._parse_iso(now_iso()) if hasattr(self, "_parse_iso") else datetime.now(UTC)


def _canonical_post_bind_events(
    self: PortalService,
    dp_id: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT envelope_json FROM signal_envelopes"
    args: list[Any] = []
    if dp_id:
        query += " WHERE dp_id = ?"
        args.append(dp_id)
    query += " ORDER BY produced_at ASC, id ASC"
    signals = [json.loads(row["envelope_json"]) for row in self.db.execute(query, args)]
    return list(canonical_events_from_signal_envelopes(signals))


def list_claims_queue(self: PortalService, dp_id: str | None = None) -> dict[str, Any]:
    query = "SELECT item_json FROM claims_queue_items"
    args: list[Any] = []
    if dp_id:
        query += " WHERE dp_id = ?"
        args.append(dp_id)
    query += " ORDER BY produced_at ASC, id ASC"
    items = [json.loads(row["item_json"]) for row in self.db.execute(query, args)]
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


def get_per_customer_rescore_feed(self: PortalService, dp_id: str) -> dict[str, Any]:
    end_dt = self._ovp_now()
    return _sink_build_per_customer_rescore_feed(
        self._canonical_post_bind_events(dp_id),
        dp_id,
        end_dt=end_dt,
        pin_block={
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
        },
    )


def get_portfolio_calibration_feed(self: PortalService) -> dict[str, Any]:
    end_dt = self._ovp_now()
    return _sink_build_portfolio_calibration_feed(
        self._canonical_post_bind_events(),
        end_dt=end_dt,
        pin_block={
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "rubric_version": RUBRIC_VERSION,
        },
    )


def build_observed_vs_predicted(self: PortalService, scan_id: str, rollup: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    pre = {
        "covered_perils": json.loads(json.dumps(rollup["covered_perils"])),
        "non_covered_regulatory_signal": json.loads(json.dumps(rollup["non_covered_regulatory_signal"])),
        "uncovered_perils": json.loads(json.dumps(rollup["uncovered_perils"])),
        "untagged_evidence_count": rollup["untagged_evidence_count"],
        "are_total": score.get("are_total"),
        "subscores": json.loads(json.dumps(score.get("subscores"))),
        "band": score.get("band"),
        "confidence": score.get("confidence"),
    }
    scan = self.db.execute("SELECT dp_id FROM scans WHERE id = ?", (scan_id,)).fetchone()
    dp_id = scan["dp_id"] if scan else None
    rescore = self.get_per_customer_rescore_feed(dp_id) if dp_id else {"perils": [], "shadow_total_delta_points": 0}
    by_code = {p["peril_code"]: p for p in rescore.get("perils", [])}
    perils = []
    for code in OVP_IN_SCOPE_PERILS:
        item = dict(by_code[code])
        item.pop("signal_envelope_ids", None)
        perils.append(item)
    guard = (
        pre["covered_perils"] == rollup["covered_perils"]
        and pre["non_covered_regulatory_signal"] == rollup["non_covered_regulatory_signal"]
        and pre["uncovered_perils"] == rollup["uncovered_perils"]
        and pre["untagged_evidence_count"] == rollup["untagged_evidence_count"]
        and pre["are_total"] == score.get("are_total")
        and pre["subscores"] == score.get("subscores")
        and pre["band"] == score.get("band")
        and pre["confidence"] == score.get("confidence")
    )
    if not guard:
        raise PortalError(500, "observed_vs_predicted_invariant_violated", "Observed-vs-predicted append mutated rollup/score fields.")
    return {
        "ovp_version": OVP_VERSION,
        "methodology_ticket_ref": "BEAAA-5818",
        "methodology_version": OVP_METHODOLOGY_VERSION,
        "block_disposition": "export_only_no_mutation",
        "taxonomy_pin": {
            "perils_in_scope": list(OVP_IN_SCOPE_PERILS),
            "notes": "P4=hallucination; P5=prompt injection/adversarial input. Pinned to BEAAA-5760/5761/5815.",
        },
        "window": {
            "mode": rescore.get("window", {}).get("mode", "rolling_30d"),
            "fallback_mode": f"rolling_{OVP_FALLBACK_WINDOW_DAYS}d",
            "start_at": rescore.get("window", {}).get("start_at"),
            "end_at": rescore.get("window", {}).get("end_at"),
            "fallback_window_used": rescore.get("window", {}).get("fallback_window_used", False),
        },
        "input_pin": {
            "bind_score_run_timestamp": score.get("run_timestamp"),
            "bind_scanner_version": score.get("scanner_version"),
            "bind_corpus_version": score.get("corpus_version"),
            "log_schema_version": LOG_SCHEMA_VERSION,
            "detector_bundle_version": DETECTOR_BUNDLE_VERSION,
            "calibration_date": OVP_CALIBRATION_DATE,
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


def render_observed_vs_predicted_block(self: PortalService, ovp: dict[str, Any]) -> str:
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
        f"<p class=\"muted\">Methodology {html.escape(ovp['methodology_ticket_ref'])} v{html.escape(ovp['methodology_version'])}; "
        "manual underwriting review only; no BackBond Score or pricing-book mutation.</p>"
        "<table><tr><th>Peril</th><th>Name</th><th>Eligible actions</th><th>Observed events</th>"
        "<th>Predicted / 10k</th><th>Observed / 10k</th><th>Credibility weighted / 10k</th>"
        "<th>Divergence</th><th>Credibility</th><th>Recommendation</th><th>Shadow delta</th></tr>"
        f"{rows}</table>"
        f"<p class=\"muted\">Shadow total delta: {ovp['shadow_total_delta_points']} | "
        f"Guardrails: {html.escape(json.dumps(ovp['guardrails'], sort_keys=True))}</p>"
    )


def handle_get(service: PortalService, parts: list[str], query: dict[str, list[str]] | None = None):
    if len(parts) == 4 and parts[:3] == ["api", "v1", "post-bind"] and parts[3] == "claims-queue":
        q = query or {}
        dp_id = (q.get("dp_id") or [None])[0]
        return 200, json.dumps(service.list_claims_queue(dp_id), sort_keys=True).encode("utf-8"), "application/json"
    if len(parts) == 5 and parts[:3] == ["api", "v1", "post-bind"] and parts[3] == "customers":
        return 200, json.dumps(service.get_per_customer_rescore_feed(parts[4]), sort_keys=True).encode("utf-8"), "application/json"
    if len(parts) == 4 and parts[:3] == ["api", "v1", "post-bind"] and parts[3] == "portfolio-calibration-feed":
        return 200, json.dumps(service.get_portfolio_calibration_feed(), sort_keys=True).encode("utf-8"), "application/json"
    return None


_BOUND_NAMES = (
    "_init_post_bind_sink_tables",
    "_process_post_bind_signal_envelopes",
    "_ovp_now",
    "_canonical_post_bind_events",
    "list_claims_queue",
    "get_per_customer_rescore_feed",
    "get_portfolio_calibration_feed",
    "build_observed_vs_predicted",
    "render_observed_vs_predicted_block",
)


def attach(cls: type[PortalService]) -> type[PortalService]:
    for name in _BOUND_NAMES:
        setattr(cls, name, globals()[name])

    original_init = cls.__init__

    def __init__(self, config):  # type: ignore[override]
        original_init(self, config)
        if not getattr(self, "_post_bind_sink_tables_ready", False):
            self._init_post_bind_sink_tables()
            self._post_bind_sink_tables_ready = True

    cls.__init__ = __init__

    original_scan_emit = cls._scan_and_emit_signals

    def _scan_and_emit_signals(self, record):  # type: ignore[override]
        emitted = original_scan_emit(self, record)
        self._process_post_bind_signal_envelopes(emitted)
        return emitted

    cls._scan_and_emit_signals = _scan_and_emit_signals
    return cls
