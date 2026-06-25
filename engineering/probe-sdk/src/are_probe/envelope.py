"""Build the v1.0 contract envelope.

Pulls the contract output JSON from bin/are-scan and adds the SDK-side
fields (probe_sdk metadata, evidence_refs from probe verdicts, optional
signature). The contract Output schema is the authoritative shape; this
module asserts the canonical fields are present on the master scan
output and then layers on the probe-sdk additive block.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from . import CONTRACT_VERSION, PROBE_SDK_RELEASE, PROBE_SDK_VERSION

# Required by contracts/are-scanner-api-v1.md@v1.0 Output section.
_REQUIRED_OUTPUT_KEYS = (
    "are_total", "subscores", "band", "evidence_refs",
    "scanner_version", "corpus_version", "model_versions",
    "seed", "run_timestamp",
)
_REQUIRED_SUBSCORE_KEYS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")


class EnvelopeError(ValueError):
    pass


def _band_letter(band) -> str:
    """Accept the contract `band` field in either the letter form
    ("A" | "B" | "C" | "D") or the master scanner's richer object form
    {"letter": "B", "display": "B+", "modifier": "plus", ...}. The
    envelope normalizes to the letter for contract-shape assertion and
    preserves the full object as `band_detail` so the Underwriter still
    sees the modifier signal.
    """
    if isinstance(band, str):
        return band
    if isinstance(band, dict) and isinstance(band.get("letter"), str):
        return band["letter"]
    raise EnvelopeError(f"contract output band not in (A,B,C,D) or letter-bearing object: {band!r}")


def assert_v1_output_shape(contract_output: dict) -> None:
    for k in _REQUIRED_OUTPUT_KEYS:
        if k not in contract_output:
            raise EnvelopeError(f"contract output missing field {k!r}")
    sub = contract_output.get("subscores") or {}
    for k in _REQUIRED_SUBSCORE_KEYS:
        if k not in sub:
            raise EnvelopeError(f"contract output subscores missing {k!r}")
    are_total = contract_output["are_total"]
    if not isinstance(are_total, (int, float)) or not (0 <= are_total <= 100):
        raise EnvelopeError(f"contract output are_total out of [0,100]: {are_total!r}")
    letter = _band_letter(contract_output["band"])
    if letter not in ("A", "B", "C", "D"):
        raise EnvelopeError(f"contract output band.letter not in (A,B,C,D): {letter!r}")


def _evidence_refs_from_results(
    eval_results: dict, family_results: list, corpus_pack
) -> list[dict]:
    refs: list[dict] = []
    # Probe-measured evidence first.
    for fr in family_results:
        for v in fr.verdicts:
            refs.append({
                "rubric_point_id": fr.rubric_point_id,
                "test_id": v.probe_id,
                "artifact_uri": (
                    f"are-probe://probe/{fr.rubric_point_id}/"
                    f"{v.probe_id}/{v.measured_disposition}"
                ),
                "verdict": "pass" if v.pass_ else "fail",
                "weight": round(1.0 / max(len(fr.verdicts), 1), 6),
            })
    # Per-dim evidence (placeholder / dp_attested / measured aggregate).
    for dim_id, dim_result in sorted(eval_results.items()):
        refs.append({
            "rubric_point_id": dim_id,
            "test_id": f"{dim_id}.{dim_result.source}",
            "artifact_uri": dim_result.evidence_uri,
            "verdict": "pass" if dim_result.raw >= 2.0 else "partial",
            "weight": round(1.0 / len(eval_results), 6),
        })
    return refs


def build_envelope(
    *,
    contract_output: dict,
    agent_id: str,
    config,
    corpus_pack,
    eval_results: dict,
    family_results: list,
    freeze_timestamp: bool,
) -> dict:
    assert_v1_output_shape(contract_output)

    if freeze_timestamp:
        run_ts = "1970-01-01T00:00:00Z"
    else:
        run_ts = contract_output.get("run_timestamp") or \
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    band_raw = contract_output["band"]
    band_letter = _band_letter(band_raw)
    envelope: dict[str, Any] = {
        "are_total": contract_output["are_total"],
        "subscores": contract_output["subscores"],
        "band": band_letter,
        "band_detail": band_raw if isinstance(band_raw, dict) else None,
        "confidence": contract_output.get("confidence", 1.0),
        "evidence_refs": _evidence_refs_from_results(
            eval_results, family_results, corpus_pack
        ),
        "uncovered_dimensions": [
            dim_id for dim_id, d in sorted(eval_results.items())
            if d.source != "measured"
        ],
        "scanner_version": contract_output["scanner_version"],
        "corpus_version": (
            contract_output["corpus_version"]
            if "corpus_version" in contract_output
            else config.corpus_version
        ),
        "model_versions": contract_output.get("model_versions")
                          or {"primary": str(config.model_versions.get("primary", "unknown")),
                              "judges": list(config.model_versions.get("judges") or [])},
        "seed": (
            contract_output["seed"]
            if "seed" in contract_output
            else config.seed
        ),
        "run_timestamp": run_ts,
        "agent_id": agent_id,
        # Additive SDK-side metadata (does not modify contract shape; lives
        # alongside it so the auditor can distinguish a probe-SDK envelope
        # from an in-BackBond scan envelope by inspection).
        "probe_sdk": {
            "version": PROBE_SDK_VERSION,
            "release": PROBE_SDK_RELEASE,
            "contract_version": CONTRACT_VERSION,
            "corpus_pack": {
                "pack_id": corpus_pack.pack_id,
                "sha256": corpus_pack.sha256,
                "upstream_source": {
                    "repo": corpus_pack.upstream_source_repo,
                    "path": corpus_pack.upstream_source_path,
                    "commit_sha": corpus_pack.upstream_source_commit_sha,
                },
                "corpus_version": corpus_pack.corpus_version,
                "probe_set_version": corpus_pack.probe_set_version,
            },
            "probe_families_run": [
                {"family": fr.rubric_point_id, "pass_rate": fr.pass_rate, "raw": fr.raw}
                for fr in family_results
            ],
        },
    }
    return envelope
