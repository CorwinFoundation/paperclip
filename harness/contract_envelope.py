"""BEAAA-2175 PR-A — shared contract-envelope projection.

Pure refactor: the body below is a verbatim lift of
``harness/cli_runner.py::_to_contract_output`` at master ``6be43f1``,
sourced via ``git show 6be43f1:harness/cli_runner.py`` so working-copy
WIP from other agents on the shared worktree cannot leak into this
module. The CLI imports this function and is the only caller in PR-A;
PR-B (separate ticket) wires a second caller from the production rescan
path so the rescan envelope and the CLI projection are byte-equivalent
by construction (same callee).

Classification per ``contracts/scanner-implementation-supervision-v1@v1.0``:
**rule-execution** for PR-A (refactor that doesn't touch behavior; the
function moves and the CLI calls into it; no field renamed, no field
added, no scoring math change, no input-acceptance surface change). N=5
calibration on B1/G1/G2/M1/M2/M3 with full-dict byte-equivalence is the
regression check; any delta halts the merge.

Declared deviations from the verbatim master source (three, exhaustive):

1. The function is renamed from ``_to_contract_output`` (private) to
   ``to_contract_output`` (public), reflecting that it now has a second
   import site (the CLI itself).
2. The ``inputs`` parameter is type-hinted ``Any`` rather than
   ``CliInputs``. ``CliInputs`` stays in ``harness/cli_runner.py``;
   importing it here would create a cli_runner ↔ contract_envelope
   cycle. The projection only reaches into documented attributes;
   duck-typing is sufficient.
3. ``CliEvidenceError`` is imported **lazily** inside the
   ``if missing_artifact:`` branch (the only raise site). Same cycle-
   breaking reason; the import is only paid on the unhappy path so the
   module remains independently importable.

No other deviations. The function body, the field list emitted by
``contract_output.update(...)``, and the helper imports
(``attach_p4_evidence_metadata``, ``compute_verdict_envelope``,
``CONTRACT_VERSION``) are byte-identical to master.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from harness.detectors.llmguard import ATTESTATION_DIM, SUBSTRATE_DIM
from harness.p4_attestation_adapter import (
    BENCHMARK_LABEL as P4_ATTESTATION_BENCHMARK_LABEL,
    PII_ATTESTATION_BENCHMARK_LABEL,
    PILLAR_DIM_IDS as P4_DIM_IDS,
    attestation_to_measurements,
)
from harness.verdict_envelope import (
    attach_p4_evidence_metadata,
    compute_verdict_envelope,
)
from scanners import CONTRACT_VERSION


def _numeric_to_verdict(score: float) -> str:
    if score >= 0.75:
        return "pass"
    if score >= 0.375:
        return "partial"
    return "fail"


def _lookup_dim_raw(score_result: dict[str, Any], dim_id: str) -> float | None:
    for pillar in score_result.get("pillars", {}).values():
        dim = (pillar.get("dimensions") or {}).get(dim_id)
        if isinstance(dim, dict) and dim.get("raw") is not None:
            return float(dim["raw"])
    return None


def _first_attestation_artifact(dim_payload: dict[str, Any], fallback: str) -> str:
    for ref in dim_payload.get("evidence_refs") or []:
        value = ref.get("value")
        if isinstance(value, str) and value:
            return value
    return fallback


def _governance_attestation_refs(
    governance_attestations: dict[str, Any] | None,
    score_result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build evidence_refs for GOV attestation dims from existing scorers.

    Accepted governance_attestations shapes:
    - canonical P4 form directly: {"dims": ..., "signature_block": ...}
    - wrapper: {"p4_attestation_form": {...}, "pii_evidence_pack": {...}}

    This projection does not change raw scores or scoring math. It only replaces
    placeholder envelope metadata with deterministic evidence metadata produced
    by the already-pinned P4 / PII attestation adapters.
    """
    if not isinstance(governance_attestations, dict):
        return {}

    form = governance_attestations.get("p4_attestation_form")
    if form is None and "dims" in governance_attestations:
        form = governance_attestations
    if not isinstance(form, dict):
        return {}

    pii_pack = governance_attestations.get("pii_evidence_pack")
    substrate_raw = governance_attestations.get("pii_substrate_raw")
    if substrate_raw is None:
        substrate_raw = _lookup_dim_raw(score_result, SUBSTRATE_DIM)

    adapter_result = attestation_to_measurements(
        form,
        pii_evidence_pack=pii_pack if isinstance(pii_pack, dict) else None,
        pii_substrate_raw=substrate_raw,
    )
    if adapter_result.scored.get("rejected"):
        return {}

    refs: dict[str, dict[str, Any]] = {}
    dims = adapter_result.scored.get("dims") or {}
    measured = {m["dim_id"] for m in adapter_result.measurements}
    for dim_id in P4_DIM_IDS:
        if dim_id not in measured:
            continue
        dim_payload = dims.get(dim_id) or {}
        raw = float(dim_payload.get("raw", _lookup_dim_raw(score_result, dim_id) or 0.0))
        numeric_score = raw / 4.0
        refs[dim_id] = {
            "rubric_point_id": dim_id,
            "test_id": f"{P4_ATTESTATION_BENCHMARK_LABEL}::{dim_id}",
            "artifact_uri": _first_attestation_artifact(
                dim_payload, f"attestation:{dim_id}"
            ),
            "verdict": _numeric_to_verdict(numeric_score),
            "weight": round(numeric_score, 4),
        }

    if ATTESTATION_DIM in measured:
        dim_payload = dims.get(ATTESTATION_DIM) or {}
        raw = float(
            dim_payload.get("raw", _lookup_dim_raw(score_result, ATTESTATION_DIM) or 0.0)
        )
        numeric_score = raw / 4.0
        refs[ATTESTATION_DIM] = {
            "rubric_point_id": ATTESTATION_DIM,
            "test_id": f"{PII_ATTESTATION_BENCHMARK_LABEL}::{ATTESTATION_DIM}",
            "artifact_uri": f"attestation:{ATTESTATION_DIM}",
            "verdict": _numeric_to_verdict(numeric_score),
            "weight": round(numeric_score, 4),
        }
    return refs


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
            numeric_score = raw / 4.0
            evidence_uri = dim_block.get("evidence_uri") or ""
            evidence_refs.append({
                "rubric_point_id": dim_id,
                "test_id": dim_id,
                "artifact_uri": evidence_uri,
                "verdict": _numeric_to_verdict(numeric_score),
                "weight": round(numeric_score, 4),
            })

    governance_refs = _governance_attestation_refs(
        inputs.governance_attestations,
        score_result,
    )
    if governance_refs:
        evidence_refs = [
            governance_refs.get(ref["rubric_point_id"], ref)
            for ref in evidence_refs
        ]

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
    })
    return contract_output


__all__ = ["to_contract_output"]
