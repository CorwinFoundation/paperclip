"""P2.6 Mitigation-evidence attestation reader — BEAAA-199 Phase 2.

Deterministic scorer for the customer-supplied bias-mitigation attestation
form. No LLM spend, no agent probe. Mirrors the shape and tamper-resistance
discipline of `harness/p4_attestation_adapter.py` (BEAAA-200 P4 v0.1-LOCKED)
so that the two attestation paths stay invariant — see the cross-track note
on `mitigation_evidence.yaml#uw_cosign_status.cross_track_note`.

Inputs:
  - `form`: customer attestation dict keyed by artifact class
    (`fairness_aware_finetune`, `output_filter_audit`,
    `fairness_review_process`). Each value carries `evidence_tier` (0..4)
    plus the class's `required_fields` (see mitigation_evidence.yaml).
  - YAML spec: loaded from
    `rubric/probe_mappings/mitigation_evidence.yaml`. Class weights, tier
    raw-contributions, and `required_fields` come from the UW+CEO co-signed
    spec — this module never hard-codes them.

Tamper-resistance rules (mirror P4 adapter):
  - An unclaimed class defaults to tier 0 (raw_contribution=0). Silence
    does NOT inflate the score.
  - A claim at tier >= 2 that is missing any of the class's
    `required_fields` (or supplies them empty) is DOWNGRADED to tier 1
    with an explicit `downgrade_reason`. The tier-1 cap of raw 1.0 is the
    UW-signed cross-track invariant with BEAAA-200 P4: claims without
    evidence cannot exceed raw 1.0 on either track.
  - The reader never silently zeroes a valid claim and never silently
    inflates an unsupported claim. Callers receive both the effective
    tier AND the structured downgrade reason.

Output mirrors `harness/p2_emit.py::_emit_measurement` shape so the result
plugs into `harness.pipeline.merge_measurements_with_priors`:
  {
    "dim_status": "ok",
    "dim_raw": <float in [0, 4]>,
    "benchmark": "mitigation_evidence_form_v0_1",
    "per_class": { ... },
    "form_version": "<yaml.version>",
  }

This module does NOT mutate `harness/p2_emit.py` or the goldens — wiring
into the emitter is deferred to the adapter-level `--with-p2` work where it
is co-sequenced with the N=5 calibration delta on Mon 5/19 EOD UTC.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

_REPO = Path(__file__).resolve().parent.parent
DEFAULT_SPEC_PATH = (
    _REPO / "rubric" / "probe_mappings" / "mitigation_evidence.yaml"
)
BENCHMARK_LABEL = "mitigation_evidence_form_v0_1"
DIM_ID = "P2.6"


def load_spec(path: str | Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    """Load the mitigation_evidence.yaml UW+CEO co-signed spec."""
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _missing_required(class_entry: dict[str, Any],
                      required_fields: Iterable[str]) -> list[str]:
    return [f for f in required_fields
            if f not in class_entry or _is_empty(class_entry[f])]


def score_attestation(
    form: dict[str, Any] | None,
    *,
    spec: dict[str, Any] | None = None,
    spec_path: str | Path = DEFAULT_SPEC_PATH,
) -> dict[str, Any]:
    """Score a customer mitigation-evidence attestation form.

    `form` may be None or an empty dict — both yield dim_raw=0 (tier 0
    floor across all three classes). The result's `dim_status` is always
    "ok" because P2.6 emits a measured floor rather than N/A per
    `rubric/applicability_policy.yaml#P2.6` ("always fires").
    """
    if spec is None:
        spec = load_spec(spec_path)
    form = form or {}

    classes: dict[str, dict[str, Any]] = spec["artifact_classes"]
    tiers: dict[int, dict[str, Any]] = {
        int(k): v for k, v in spec["evidence_tiers"].items()
    }

    per_class: dict[str, dict[str, Any]] = {}
    dim_raw = 0.0
    for class_name, class_spec in classes.items():
        weight = float(class_spec["weight"])
        required_fields = tuple(class_spec.get("required_fields", ()))
        entry = form.get(class_name) or {}

        claimed_tier_raw = entry.get("evidence_tier", 0)
        try:
            claimed_tier = int(claimed_tier_raw)
        except (TypeError, ValueError):
            claimed_tier = 0
        if claimed_tier not in tiers:
            # Unknown tier value -> treat as no claim. Downgrade is
            # explicit so the caller sees the malformed input.
            effective_tier = 0
            downgrade_reason = (
                f"unknown evidence_tier={claimed_tier_raw!r}; "
                "defaulting to tier 0"
            )
        elif claimed_tier >= 2:
            missing = _missing_required(entry, required_fields)
            if missing:
                effective_tier = 1
                downgrade_reason = (
                    f"claimed tier {claimed_tier} requires "
                    f"required_fields={list(required_fields)}; "
                    f"missing/empty={missing}; downgraded to tier 1 per "
                    "BEAAA-200 tamper-resistance cap"
                )
            else:
                effective_tier = claimed_tier
                downgrade_reason = None
        else:
            # Tier 0 (no claim) or tier 1 (claimed without evidence) — no
            # required_fields validation; tier-1 cap of raw=1.0 is the
            # cross-track invariant with P4.
            effective_tier = claimed_tier
            downgrade_reason = None

        raw_contribution = float(tiers[effective_tier]["raw_contribution"])
        weighted_raw = raw_contribution * weight
        dim_raw += weighted_raw

        per_class[class_name] = {
            "claimed_tier": claimed_tier,
            "effective_tier": effective_tier,
            "raw_contribution": raw_contribution,
            "weight": weight,
            "weighted_raw": weighted_raw,
            "downgrade_reason": downgrade_reason,
            "tier_name": tiers[effective_tier]["name"],
        }

    return {
        "dim_status": "ok",
        "dim_id": DIM_ID,
        "dim_raw": round(dim_raw, 6),
        "benchmark": BENCHMARK_LABEL,
        "per_class": per_class,
        "form_version": str(spec.get("version", "unknown")),
    }


def to_measurement(scored: dict[str, Any]) -> dict[str, Any]:
    """Convert a `score_attestation` result into the measurement-row shape
    consumed by `harness.pipeline.merge_measurements_with_priors`."""
    return {
        "dim_id": scored["dim_id"],
        "raw": float(scored["dim_raw"]),
        "benchmark": scored["benchmark"],
    }
