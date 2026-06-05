"""Shared contract-envelope projection.

Originally lifted under BEAAA-2175 PR-A as a verbatim copy of
``harness/cli_runner.py::_to_contract_output`` at master ``6be43f1``
(sourced via ``git show 6be43f1:harness/cli_runner.py``). Three lift-
time deviations were declared (function rename, ``inputs: Any``
type-hint, lazy ``CliEvidenceError`` import) — see the BEAAA-2175 PR-A
CTO merge co-sign at [997ee5c9](/BEAAA/issues/BEAAA-2175#comment-997ee5c9)
for the audit trail.

BEAAA-2199 (this commit) adds one auditor-readable additive field at
the contract output surface: ``cosign_metadata``, sourced from
``harness.cosign_manifest.build_cosign_metadata(...)``. Classification:
rule-change per ``contracts/scanner-implementation-supervision-v1@v1.0``
(``bin/are-scan`` CLI output JSON shape change). CTO design co-sign on
[BEAAA-2199 comment ca7071d9](/BEAAA/issues/BEAAA-2199#comment-ca7071d9);
UW op-seat bump-direction co-sign on [BEAAA-2262](/BEAAA/issues/BEAAA-2262)
(Option B → contract ``v1.2.1 → v1.6.0`` minor).

Existing field set is byte-stable; ``cosign_metadata`` is pure-additive
on top. ``snapshot_hash`` continues to sign the full envelope including
the new block deterministically (canonical-JSON domain unchanged — the
hash preimage is whole envelope minus the three rescan-only volatile
keys ``{snapshot_hash, forensic_bundle_uri, purpose_aware_sla}`` per
``are-scanner-api-v1 §Canonical-JSON``). N=5 calibration on
B1/G1/G2/M1/M2/M3 holds spread = 0 on ``are_total`` + every pillar
(scoring math untouched); full-dict diff is exactly the
``cosign_metadata`` key and nothing else.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from harness.cosign_manifest import build_cosign_metadata
from harness.verdict_envelope import (
    attach_p4_evidence_metadata,
    compute_verdict_envelope,
)
from scanners import CONTRACT_VERSION


def to_contract_output(
    *,
    inputs: Any,
    score_result: dict[str, Any],
    seed: int,
    corpus_version: str | None,
    scanner_version: str,
    scored_at: datetime,
) -> dict[str, Any]:
    """Map the in-agent scoring output to the contract Output shape.

    The existing fields (bmi, band, pillars, snapshot_hash, ...) are
    preserved exactly — the Underwriter pricing engine reads those today
    and a behavioural rename would be a contract change. The contract-
    named aliases are layered ON TOP so a CLI consumer that reads the
    documented shape works without forcing the in-agent path to refactor.
    """
    pillars = score_result.get("pillars", {})
    subscores: dict[str, float] = {}
    evidence_refs: list[dict[str, Any]] = []
    uncovered: list[str] = []
    for pid, pdata in pillars.items():
        pscore = float(pdata.get("score", 0.0))
        subscores[pid] = pscore
        for dim_id, dim_block in pdata.get("dimensions", {}).items():
            if dim_block.get("applicable") is False or dim_block.get("raw") is None:
                uncovered.append(dim_id)
                evidence_refs.append({
                    "rubric_point_id": dim_id,
                    "test_id": "N/A",
                    "artifact_uri": "",
                    "verdict": "n/a",
                    "weight": 0.0,
                    "na_justification": dim_block.get("na_justification"),
                    "na_justification_key": dim_block.get("na_justification_key"),
                })
                continue
            raw = float(dim_block.get("raw") or 0.0)
            evidence_uri = dim_block.get("evidence_uri") or ""
            verdict = (
                "pass" if raw >= 3.0
                else "partial" if raw >= 1.5
                else "fail"
            )
            evidence_refs.append({
                "rubric_point_id": dim_id,
                "test_id": dim_id,
                "artifact_uri": evidence_uri,
                "verdict": verdict,
                "weight": round(raw / 4.0, 4),
            })

    # Hard guarantee #2 from the contract: every active rubric point must
    # have a reachable artifact_uri. The CLI surface is the place to
    # enforce this — the in-agent path defers it to the Underwriter intake.
    missing_artifact = [
        ref["rubric_point_id"]
        for ref in evidence_refs
        if ref["verdict"] != "n/a" and not ref["artifact_uri"]
    ]
    if missing_artifact:
        # Lazy import to break the cli_runner ↔ contract_envelope cycle.
        from harness.cli_runner import CliEvidenceError
        raise CliEvidenceError(
            "evidence_refs incomplete: dims with empty artifact_uri: "
            + ",".join(sorted(missing_artifact))
        )

    # BEAAA-796 / v1.0.3 §Output promotion #4 — per-P4-dim review_state +
    # tier MUST emit on every P4 evidence_refs row. Reads review_state +
    # claimed tiers off the customer-submitted attestation fixture
    # (governance_attestations.json); falls through to unreviewed + zero
    # tiers when the customer hasn't submitted yet.
    attach_p4_evidence_metadata(
        evidence_refs, attestation_fixture=inputs.governance_attestations,
    )

    bmi = int(score_result.get("bmi", 0))
    band = score_result.get("band", {})
    band_letter = band.get("display") or band.get("letter") or ""

    # Confidence proxy: 1.0 - share of N/A dims. Real confidence is the
    # Underwriter's call (loaded into pricing as worst-case per Hard
    # guarantee #2 of the consumer side); this is a deterministic stand-in
    # so the contract field is never absent.
    total_dims = sum(
        len(p.get("dimensions", {})) for p in pillars.values()
    ) or 1
    confidence = round(1.0 - (len(uncovered) / total_dims), 4)

    contract_output: dict[str, Any] = dict(score_result)
    # BEAAA-796 / v1.0.3 §Output — fused `verdict` envelope derived from
    # the substrate pillar scores + the customer's P4 attestation fixture.
    # Additive: existing pillar/dim payloads byte-for-byte unchanged.
    verdict_block = compute_verdict_envelope(
        score_result,
        attestation_fixture=inputs.governance_attestations,
    )
    contract_output.update({
        "agent_id": inputs.agent_id,
        "are_total": bmi,
        "subscores": {k: round(v, 4) for k, v in subscores.items()},
        "band_letter": band_letter,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "uncovered_dimensions": sorted(set(uncovered)),
        "verdict": verdict_block["verdict"],
        "scanner_version": scanner_version,
        "corpus_version": (
            corpus_version if corpus_version is not None
            else score_result.get("corpus_version", "")
        ),
        "model_versions": score_result.get("model_versions"),
        "seed": seed,
        "run_timestamp": scored_at.isoformat(),
        "contract_version": CONTRACT_VERSION,
        "input_file_sha256s": inputs.file_sha256s,
        "cli_meta": {
            "tool": "bin/are-scan",
            "issue": "BEAAA-464",
            "input_dir_name": inputs.input_dir.name,
            "has_system_prompt": inputs.system_prompt is not None,
            "tool_inventory_count": (
                len(inputs.tool_inventory)
                if inputs.tool_inventory is not None else None
            ),
            "has_policy_attestations": inputs.policy_attestations is not None,
            "has_governance_attestations": (
                inputs.governance_attestations is not None
            ),
            "has_applicability_sidecar": (
                inputs.applicability_decisions is not None
            ),
            "scanner_invocation_context": inputs.scanner_invocation_context,
            "pillar_naming_note": (
                "subscores keyed by contract pillar names "
                "(OPS / BIAS_FAIRNESS / ROBUSTNESS / GOVERNANCE) per "
                "are-scanner-api-v1@v1.0. Post-BEAAA-470 the internal "
                "rubric keys match the contract names 1:1 — the "
                "ADR-0005 subscores_contract_named bridge view is "
                "removed. See ADR-0006."
            ),
        },
        "cosign_metadata": build_cosign_metadata(
            scanner_version=scanner_version,
            contract_version=CONTRACT_VERSION,
            rubric_version=score_result.get("rubric_version"),
            applicability_policy_version=score_result.get(
                "applicability_policy_version"
            ),
        ),
    })
    return contract_output


__all__ = ["to_contract_output"]
