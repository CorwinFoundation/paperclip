"""Build the eval_results.json input directory consumed by bin/are-scan.

R1 strategy: SDK probe families contribute measured raws for the dims they
cover; remaining dims are filled from a DP-attested defaults file (or
shipped placeholders) so bin/are-scan can run end-to-end. Both sources are
recorded in `evidence_uri` so the auditor can distinguish measured from
attested at envelope ingest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# v1.2.0 active rubric: 25 dim ids — 21 legacy P<n>.<m> + 4 fully-qualified
# additions (TOOL_MISUSE pair, PII pair) from BEAAA-1682 + BEAAA-781.
# R2 may include further active dim ids; the lockfile pins the master
# scanner_version so the active set is reproducible.
RUBRIC_V1_2_0_DIM_IDS = (
    "P1.1", "P1.2", "P1.3", "P1.4", "P1.5", "P1.6",
    "P2.1", "P2.2", "P2.3", "P2.4", "P2.5", "P2.6",
    "P3.1", "P3.2", "P3.3",
    "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
    "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
    "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
    "P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6",
    "GOVERNANCE.PII.HANDLING_ATTESTATION",
)

# Default DP-attested raws for R1 shelf-build. Every dim gets raw=2.5
# (mid-pack placeholder) and an evidence_uri pointing back at this SDK so
# the auditor sees, "these came from R1 placeholder, not measurement".
# DPs override per-dim by supplying dp_attestations.json.
_R1_PLACEHOLDER_RAW = 2.5
_R1_PLACEHOLDER_URI = (
    "are-probe://placeholder/r1-shelf-build/"
    "see-engineering-probe-sdk-readme"
)


@dataclass(frozen=True)
class DimResult:
    dim_id: str
    raw: float
    evidence_uri: str
    source: str  # "measured" | "dp_attested" | "r1_placeholder"


def build_eval_results(
    *,
    measured: dict[str, tuple[float, str]],
    dp_attestations: dict | None,
) -> dict[str, DimResult]:
    """Return DimResult per active dim id."""
    out: dict[str, DimResult] = {}
    for dim_id in RUBRIC_V1_2_0_DIM_IDS:
        if dim_id in measured:
            raw, uri = measured[dim_id]
            out[dim_id] = DimResult(dim_id, raw, uri, "measured")
            continue
        if dp_attestations and dim_id in dp_attestations:
            entry = dp_attestations[dim_id]
            raw = float(entry["raw"])
            if not (0.0 <= raw <= 4.0):
                raise ValueError(f"dp_attestations[{dim_id}].raw={raw} out of [0,4]")
            uri = str(entry.get("evidence_uri")
                      or f"are-probe://dp-attested/{dim_id}")
            out[dim_id] = DimResult(dim_id, raw, uri, "dp_attested")
            continue
        out[dim_id] = DimResult(
            dim_id, _R1_PLACEHOLDER_RAW, _R1_PLACEHOLDER_URI, "r1_placeholder"
        )
    return out


def write_eval_results_dir(
    *,
    target_dir: Path,
    eval_results: dict[str, DimResult],
    model_versions: dict,
    purpose: str = "pre_bind",
) -> Path:
    """Lay out the input dir bin/are-scan expects.

    Files written:
      eval_results.json
      model_versions.json
      scanner_invocation_context.json
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    eval_path = target_dir / "eval_results.json"
    payload = {
        dim_id: {"raw": d.raw, "evidence_uri": d.evidence_uri}
        for dim_id, d in eval_results.items()
    }
    eval_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    (target_dir / "model_versions.json").write_text(
        json.dumps({
            "primary": str(model_versions.get("primary", "unknown")),
            "judges": list(model_versions.get("judges") or []),
        }, sort_keys=True, indent=2)
    )
    (target_dir / "scanner_invocation_context.json").write_text(
        json.dumps({
            "purpose": purpose,
            "requested_pillars": ["OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"],
        }, sort_keys=True, indent=2)
    )
    return eval_path
