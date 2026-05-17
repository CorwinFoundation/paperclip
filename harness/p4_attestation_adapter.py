"""BEAAA-200 Track 4 pilot integration — bridge `score_p4_attestation.score_form`
into the measurement format consumed by `harness.pipeline.merge_measurements_with_priors`.

When a customer P4 attestation form is supplied, this adapter replaces the prior
raw=~2.0 stub for P4.1–P4.6 with form-derived raws (capped per BEAAA-213/214
signature-mode + form-variant + review-state cascade).

Output rows:
    {"dim_id": "P4.<n>", "raw": <0..4>, "benchmark": "p4_attestation_form_v0_1_locked"}

The adapter does not silently downgrade on signature rejection — callers receive
the structured rejection so the pilot run can surface it rather than emitting
inflated stub raws.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import score_p4_attestation as p4

BENCHMARK_LABEL = "p4_attestation_form_v0_1_locked"
PILLAR_DIM_IDS = ("P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6")


class P4AttestationAdapterResult(dict):
    """Dict with two extra attributes for caller ergonomics: `.measurements`
    (list to feed into the merger) and `.scored` (full score_form payload)."""

    @property
    def measurements(self) -> list[dict[str, Any]]:
        return self["measurements"]

    @property
    def scored(self) -> dict[str, Any]:
        return self["scored"]


def attestation_to_measurements(
    form: dict[str, Any],
    *,
    ledger: dict[str, Any] | None = None,
    ledger_replay_divergence: dict[str, float] | None = None,
    evidence_root: Path | None = None,
    grader=None,
    now=None,
    review_state_overlay: dict[str, dict[str, Any]] | None = None,
) -> P4AttestationAdapterResult:
    """Score a P4 attestation form and emit a measurements list for the pipeline.

    If the form is rejected (Q2 signature_mode insufficient for claimed tier),
    `measurements` is an empty list and `scored["rejected"]` is True. Callers
    must check `scored.get("rejected")` before treating the absence of
    measurements as a free pass — that path is what keeps a tamper-attempt
    from silently falling through to priors.
    """
    scored = p4.score_form(
        form,
        ledger=ledger,
        ledger_replay_divergence=ledger_replay_divergence,
        evidence_root=evidence_root,
        grader=grader,
        now=now,
        review_state_overlay=review_state_overlay,
        raise_on_signature_insufficient=False,
    )
    measurements: list[dict[str, Any]] = []
    if not scored.get("rejected"):
        for dim_id in PILLAR_DIM_IDS:
            dim = scored["dims"].get(dim_id)
            if dim is None:
                continue
            measurements.append(
                {
                    "dim_id": dim_id,
                    "raw": float(dim["raw"]),
                    "benchmark": BENCHMARK_LABEL,
                }
            )
    return P4AttestationAdapterResult(measurements=measurements, scored=scored)
