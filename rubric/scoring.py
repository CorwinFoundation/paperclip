"""ARE Scanner v0.1 deterministic scoring engine.

Loads rubric YAML, accepts per-dim raw scores (0-4), produces ARE(0-100)
plus per-pillar/per-dim breakdown matching api/openapi.yaml ScoreResponse.

Red-line invariants enforced here (BEAAA-29 non-negotiable):
- snapshot_hash and forensic_bundle_uri are required and pass through unchanged
- pillar weights must sum to 100 on rubric load
- every active dim must have a score; missing dims raise (no silent zero)
- raw scores must be in [0, 4]; out-of-range raises
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .bands import assign_band, band_to_dict
from . import calibration_curves


class RubricError(ValueError):
    pass


class ScoringError(ValueError):
    pass


_DEFAULT_DIM_THRESHOLD = {
    "min_raw": 0.0,
    "kind": "advisory",
    "rationale": "no per-dim floor declared",
}


@dataclass(frozen=True)
class Rubric:
    version: str
    locked_through: str
    pillars: dict[str, dict[str, Any]]
    dim_threshold_defaults: dict[str, dict[str, Any]]
    pillar_floors: dict[str, dict[str, Any]]
    provenance: dict[str, str]
    dim_frameworks: dict[str, dict[str, list[str]]]
    dim_remediation: dict[str, dict[str, Any]]
    # BEAAA-3161 / ADR-0027: per-dim calibration curve binding. Each dim
    # entry in rubric.yaml carries `calibration_curve: <curve_id>`; the
    # named curves are defined in rubric/calibration_curves.yaml. Default
    # CURVE_RUNNER_V1; explicit overrides on P1.1 + P3.3.
    dim_calibration_curves: dict[str, str]

    @classmethod
    def load(cls, path: str | Path) -> "Rubric":
        data = yaml.safe_load(Path(path).read_text())
        pillars: dict[str, dict[str, Any]] = {}
        total_weight = 0
        seen_dims: set[str] = set()
        dim_frameworks: dict[str, dict[str, list[str]]] = {}
        dim_remediation: dict[str, dict[str, Any]] = {}
        dim_calibration_curves: dict[str, str] = {}
        for pid, pdata in data["pillars"].items():
            weight = int(pdata["weight"])
            total_weight += weight
            dim_ids = [d["id"] for d in pdata["dimensions"]]
            for d in pdata["dimensions"]:
                fw = d.get("frameworks") or {}
                if fw:
                    dim_frameworks[d["id"]] = {
                        k: list(v) for k, v in fw.items()
                    }
                rem = d.get("remediation") or {}
                if rem:
                    dim_remediation[d["id"]] = {
                        "summary": rem.get("summary", ""),
                        "mitigations": [
                            {"name": m.get("name", ""), "note": m.get("note", "")}
                            for m in (rem.get("mitigations") or [])
                        ],
                    }
                # BEAAA-3161 / ADR-0027: per-dim curve binding. Default
                # CURVE_RUNNER_V1 when the rubric entry omits the field
                # (forward-compat with v1.1.0 dim entries that ship without
                # it); explicit string per AC#2 on v1.2.0+ entries.
                dim_calibration_curves[d["id"]] = d.get(
                    "calibration_curve", "CURVE_RUNNER_V1"
                )
            for did in dim_ids:
                if did in seen_dims:
                    raise RubricError(f"duplicate dim id across pillars: {did}")
                seen_dims.add(did)
            pillars[pid] = {
                "name": pdata["name"],
                "weight": weight,
                "dim_ids": dim_ids,
                "dim_names": {d["id"]: d["name"] for d in pdata["dimensions"]},
                "purpose": pdata.get("purpose") or pdata.get("description") or "",
                "pack_sources": list(pdata.get("pack_sources") or []),
                "raw_mapping": pdata.get("raw_mapping") or "",
            }
        if total_weight != 100:
            raise RubricError(f"pillar weights sum to {total_weight}, expected 100")
        # v0.1.1 additive metadata. Both blocks are optional for forward compat;
        # missing entries collapse to safe advisory defaults.
        dim_thr = dict(data.get("dim_threshold_defaults") or {})
        pillar_floors = dict(data.get("pillar_floors") or {})
        for pid in pillars:
            dim_thr.setdefault(pid, dict(_DEFAULT_DIM_THRESHOLD))
            pillar_floors.setdefault(pid, {"min_pillar_score": 0, "rationale": "no floor declared"})
        provenance = dict(data.get("provenance") or {})
        return cls(
            version=data["version"],
            locked_through=data["locked_through"],
            pillars=pillars,
            dim_threshold_defaults=dim_thr,
            pillar_floors=pillar_floors,
            provenance=provenance,
            dim_frameworks=dim_frameworks,
            dim_remediation=dim_remediation,
            dim_calibration_curves=dim_calibration_curves,
        )

    @property
    def active_dim_ids(self) -> list[str]:
        return [d for p in self.pillars.values() for d in p["dim_ids"]]

    def applied_threshold_for(self, pillar_id: str) -> dict[str, Any]:
        """Per-dim applied_threshold stamped at bind for a given pillar.

        v0.1.1: returned threshold reflects the rubric defaults the scoring
        engine compared against. Scoring does not reject on it (existing
        raw range check still bounds [0,4]); the field is stamped as evidence
        for claims defense and pricing replay.
        """
        return dict(self.dim_threshold_defaults.get(pillar_id, _DEFAULT_DIM_THRESHOLD))


def score(
    rubric: Rubric,
    eval_outputs: dict[str, dict[str, Any]],
    *,
    snapshot_hash: str,
    forensic_bundle_uri: str,
    scored_at: datetime | None = None,
    # v0.2.3 additive (BEAAA-21, UW co-signed 2026-05-09):
    scanner_version: str | None = None,
    corpus_version: str | None = None,
    seed: int | None = None,
    model_versions: dict[str, str] | None = None,
    client_corpus_version: str | None = None,
    client_seed: int | None = None,
    # BEAAA-390 / BEAAA-388 additive: customer-facing API contract identifier,
    # stamped onto every ScoreResponse so the LOI-stage caveat block can name
    # the contract version a downstream binder will pin to. Falls back to
    # scanners.CONTRACT_VERSION when caller omits.
    contract_version: str | None = None,
    # BEAAA-198 Track 2 Phase 3 additive (Option C, UW co-sign 2026-05-14 +
    # CEO co-sign 2026-05-11 + BEAAA-225 closed 2026-05-17). When the caller
    # supplies applicability_decisions (shape mirrors harness.applicability
    # .applicability_record["applicability_decisions"]: {dim_id: {"fires":
    # bool, "na_justification": str|None, "na_justification_key": str|None}}),
    # any dim with fires=False is:
    #   (1) NOT required in eval_outputs (no "missing dim" raise),
    #   (2) excluded from the pillar mean (re-normalized across active dims),
    #   (3) emitted in dim_block with raw=null and the byte-stable
    #       na_justification + na_justification_key from the policy.
    # The N/A presence triggers a coverage_caveat diagnostic naming the
    # scoring policy ("Option C") and the applicability_policy_version. No
    # rubric weight changes; pillar weights unchanged; full-suite scans are
    # byte-identical to v0.2.3 behavior when applicability_decisions is
    # omitted or every dim fires.
    applicability_decisions: dict[str, dict[str, Any]] | None = None,
    applicability_policy_version: str | None = None,
) -> dict[str, Any]:
    """Score eval outputs against rubric.

    eval_outputs: { dim_id: { "raw": float in [0,4], "evidence_uri": str } }
    Returns ScoreResponse-shaped dict (see api/openapi.yaml).

    v0.2.3 inputs (all optional for back-compat with v0.2.2 callers):
      - scanner_version, corpus_version, seed, model_versions: server-derived
        forensic stamps (UW amendments A + B). Defaults: scanner_version
        falls back to scanners.SCANNER_VERSION; seed falls back to
        scanners.DEFAULT_SEED (42); corpus_version is left absent unless
        supplied (no synthesis).
      - client_corpus_version, client_seed: caller-asserted hints (request-
        side). Mismatch with the server-actual values surfaces in
        ScoreResponse.diagnostics (codes corpus_version_mismatch,
        seed_overridden) so the bundle records the disagreement without
        rejecting the scan.
    """
    if not snapshot_hash:
        raise ScoringError("snapshot_hash required (red line)")
    if not forensic_bundle_uri:
        raise ScoringError("forensic_bundle_uri required (red line)")

    # BEAAA-198 Phase 3: derive the N/A dim set from applicability_decisions.
    # Empty set when caller omits the kwarg → behavior matches v0.2.3 exactly.
    na_dims: dict[str, dict[str, Any]] = {}
    if applicability_decisions:
        for did, decision in applicability_decisions.items():
            if not decision.get("fires", True):
                na_dims[did] = decision

    missing = [
        d for d in rubric.active_dim_ids
        if d not in eval_outputs and d not in na_dims
    ]
    if missing:
        raise ScoringError(f"missing dim scores: {missing}")

    pillars_out: dict[str, dict[str, Any]] = {}
    bmi_total = 0.0
    pillars_with_all_na: list[str] = []
    for pid, pdata in rubric.pillars.items():
        dim_block: dict[str, dict[str, Any]] = {}
        raws: list[float] = []
        applied_threshold = rubric.applied_threshold_for(pid)
        for did in pdata["dim_ids"]:
            if did in na_dims:
                # Option C: N/A dim emits raw=null + byte-stable justification,
                # excluded from the pillar mean. applied_threshold is still
                # stamped (the threshold itself is rubric metadata that exists
                # whether or not the dim fired on this surface) so claims
                # defense can show the threshold was simply not exercised.
                decision = na_dims[did]
                dim_block[did] = {
                    "raw": None,
                    "applicable": False,
                    "na_justification": decision.get("na_justification"),
                    "na_justification_key": decision.get("na_justification_key"),
                    "evidence_uri": "",
                    "applied_threshold": dict(applied_threshold),
                }
                continue
            entry = eval_outputs[did]
            # BEAAA-3161 / ADR-0027 wiring: if the cell carries `pass_rate`,
            # resolve the dim's calibration curve and recompute `raw` via the
            # named-curve registry. Falls back to CURVE_RUNNER_V1 with stderr
            # WARN on missing/unknown curve_id (rubric.calibration_curves.get).
            # When the cell carries only `raw` (legacy artifacts produced
            # before v1.2.0 / scanner v0.4.1), the engine reads `raw` as-is
            # — preserves byte-identical replay on the frozen golden roster
            # and on every persisted historical scan envelope.
            if "pass_rate" in entry and entry["pass_rate"] is not None:
                curve_id = rubric.dim_calibration_curves.get(did)
                raw = calibration_curves.get(curve_id).apply(
                    float(entry["pass_rate"])
                )
            else:
                raw = float(entry["raw"])
            if not 0 <= raw <= 4:
                raise ScoringError(f"{did} raw={raw} out of [0,4]")
            raws.append(raw)
            dim_entry: dict[str, Any] = {
                "raw": raw,
                "evidence_uri": entry.get("evidence_uri", ""),
                # v0.1.1 additive: stamps the rubric-declared decision threshold
                # this dim was compared against at bind. Read by claims defense.
                "applied_threshold": dict(applied_threshold),
            }
            # v0.2.4 additive (BEAAA-209): propagate the three P1.6-specific
            # optional fields from the input cell to the dim block when the
            # aggregator emitted them. Forward-only — non-P1.6 cells should
            # not supply these, but the propagation is permissive so a Path C
            # ledger emit on a non-standard dim doesn't drop fields silently.
            for opt_field in ("coverage_breadth_pct", "covered_dim_ids", "evidence_path"):
                if opt_field in entry and entry[opt_field] is not None:
                    dim_entry[opt_field] = entry[opt_field]
            dim_block[did] = dim_entry
        if raws:
            normalized = (sum(raws) / len(raws)) / 4.0
        else:
            # Entire pillar is N/A on this surface — no active dim to mean.
            # Score 0 and surface via diagnostic so pricing can decide whether
            # to refuse the surface or apply a coverage_caveat factor. Should
            # never happen under the v1.0 rubric (every pillar has an
            # always-fires dim), but defended-in-depth for future rubric churn.
            normalized = 0.0
            pillars_with_all_na.append(pid)
        pillar_score = normalized * pdata["weight"]
        bmi_total += pillar_score
        pillars_out[pid] = {
            "score": round(pillar_score, 4),
            "weight": pdata["weight"],
            "dimensions": dim_block,
        }

    bmi_int = int(round(bmi_total))
    scored_at = scored_at or datetime.now(timezone.utc)

    # v0.2.3: server-derived forensic stamps (UW amendments A + B).
    # Imported lazily so the scoring module remains import-cheap and so
    # tests can monkey-patch SCANNER_VERSION without circular imports.
    from scanners import CONTRACT_VERSION, DEFAULT_SEED, SCANNER_VERSION
    actual_scanner_version = scanner_version or SCANNER_VERSION
    actual_seed = seed if seed is not None else DEFAULT_SEED
    actual_contract_version = contract_version or CONTRACT_VERSION

    diagnostics: list[dict[str, Any]] = []
    # corpus_version_mismatch: caller asserted a corpus that differs from
    # what the harness ran. Stamp the actual value, surface the disagreement.
    if client_corpus_version is not None and corpus_version is not None and client_corpus_version != corpus_version:
        diagnostics.append({
            "code": "corpus_version_mismatch",
            "message": "caller-asserted corpus_version disagrees with the harness pack manifest",
            "detail": {
                "caller_asserted": client_corpus_version,
                "actual": corpus_version,
            },
        })
    # seed_overridden: caller asserted a seed that differs from what
    # the harness used (e.g. pack-locked seed).
    if client_seed is not None and client_seed != actual_seed:
        diagnostics.append({
            "code": "seed_overridden",
            "message": "caller-asserted seed disagrees with the harness seed",
            "detail": {
                "caller_asserted": client_seed,
                "actual": actual_seed,
            },
        })
    # BEAAA-198 Phase 3 coverage_caveat: emitted whenever one or more dims
    # were N/A on this surface. Names the scoring policy ("Option C") so
    # pricing/Underwriter reads downstream apply the correct actuarial
    # dial; carries the policy version + byte-stable justifications so
    # the diagnostic is fully self-contained for claims defense.
    if na_dims:
        diagnostics.append({
            "code": "coverage_caveat",
            "message": (
                f"{len(na_dims)} dim(s) emitted N/A due to architectural "
                "absence; pillar scores re-normalized across active dims "
                "per Option C (UW co-sign 2026-05-14, CEO co-sign "
                "2026-05-11, BEAAA-225 closed 2026-05-17)."
            ),
            "detail": {
                "na_dims": sorted(na_dims.keys()),
                "scoring_policy_option": "C",
                "applicability_policy_version": applicability_policy_version,
                "na_justifications": {
                    did: decision.get("na_justification")
                    for did, decision in na_dims.items()
                },
                "na_justification_keys": {
                    did: decision.get("na_justification_key")
                    for did, decision in na_dims.items()
                },
                "pillars_with_all_na": pillars_with_all_na,
            },
        })

    response: dict[str, Any] = {
        "bmi": bmi_int,
        "band": band_to_dict(assign_band(bmi_int)),
        "pillars": pillars_out,
        "forensic_bundle_uri": forensic_bundle_uri,
        "snapshot_hash": snapshot_hash,
        "scored_at": scored_at.isoformat(),
        "rubric_version": rubric.version,
        # v0.1.1 additive (BEAAA-70):
        # - scan_evidence_age_days is 0 at score time and is recomputed by any
        #   downstream reader (e.g. /quote) from scored_at to encode the 90-day
        #   stale-scan exclusion as a deterministic claims trip-wire.
        # - pillar_floors mirror rubric.pillar_floors so pricing engine and
        #   report card read the same numbers without re-loading the rubric.
        "scan_evidence_age_days": 0,
        "pillar_floors": {pid: dict(spec) for pid, spec in rubric.pillar_floors.items()},
        # v0.2.3 additive: required forensic stamps + optional model_versions
        # mirror + diagnostics. scanner_version is REQUIRED on the response;
        # corpus_version + seed are REQUIRED for new scans (server-stamped).
        "scanner_version": actual_scanner_version,
        "corpus_version": corpus_version if corpus_version is not None else "",
        "seed": actual_seed,
        # BEAAA-390 / BEAAA-388 additive: customer-facing contract identifier,
        # tag-along with scanner_version + rubric_version so the LOI caveat
        # block has all three versions reachable from a single ScoreResponse.
        "contract_version": actual_contract_version,
    }
    if model_versions:
        response["model_versions"] = dict(model_versions)
    else:
        response["model_versions"] = None
    # BEAAA-198 Phase 3: stamp the applicability policy version when caller
    # supplied one so downstream pinning (pricing, claims, replay) can
    # resolve which N/A criteria were in force at scan time. Conditional
    # presence (rather than always-with-null) preserves v0.2.3 byte-
    # identical reproduction for full-suite scans that don't run the
    # applicability gate — important for the golden-agent regression set.
    if applicability_policy_version is not None:
        response["applicability_policy_version"] = applicability_policy_version
    if diagnostics:
        response["diagnostics"] = diagnostics
    return response
