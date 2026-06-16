from __future__ import annotations

from typing import Any

from .common import (
    CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS,
    CLAIMS_THRESHOLD_POLICY_REF,
    DETECTOR_BUNDLE_VERSION,
    LOG_SCHEMA_PATCH_VERSION,
    LOG_SCHEMA_VERSION,
    POST_BIND_SINKS_VERSION,
)


def build_claims_queue_item(event: dict[str, Any], *, pin_block: dict[str, Any]) -> dict[str, Any]:
    payload = event["payload"]
    peril_code = payload["peril"]
    policy_governed = peril_code in CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS
    return {
        "sink_version": POST_BIND_SINKS_VERSION,
        "claims_queue_item_id": payload["source_signal_envelope_id"],
        "signal_envelope_id": payload["source_signal_envelope_id"],
        "dp_id": payload["dp_id"],
        "peril_code": peril_code,
        "peril_name": payload["peril_name"],
        "severity_tier": payload["severity"],
        "routing": "underwriter_and_claims_architect_review",
        "manual_only": True,
        "threshold_policy_ref": CLAIMS_THRESHOLD_POLICY_REF if policy_governed else None,
        "threshold_policy_governed": policy_governed,
        "telemetry_manual_review_only": not policy_governed,
        "policy_scope_note": (
            "BEAAA-5817-governed P4/P6 handling lane; manual-only review, no automatic coverage or repricing."
            if policy_governed
            else "Telemetry-only manual review outside BEAAA-5817 v0.3 P4/P6 threshold scope."
        ),
        "produced_at": event["logged_at"],
        "evidence_refs": payload["evidence_refs"],
        "pin_block": {
            **pin_block,
            "log_schema_version": LOG_SCHEMA_VERSION,
            "log_schema_patch_version": LOG_SCHEMA_PATCH_VERSION,
            "detector_bundle_version": DETECTOR_BUNDLE_VERSION,
        },
    }
