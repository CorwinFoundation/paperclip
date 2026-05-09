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

    @classmethod
    def load(cls, path: str | Path) -> "Rubric":
        data = yaml.safe_load(Path(path).read_text())
        pillars: dict[str, dict[str, Any]] = {}
        total_weight = 0
        seen_dims: set[str] = set()
        dim_frameworks: dict[str, dict[str, list[str]]] = {}
        dim_remediation: dict[str, dict[str, Any]] = {}
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

    missing = [d for d in rubric.active_dim_ids if d not in eval_outputs]
    if missing:
        raise ScoringError(f"missing dim scores: {missing}")

    pillars_out: dict[str, dict[str, Any]] = {}
    bmi_total = 0.0
    for pid, pdata in rubric.pillars.items():
        dim_block: dict[str, dict[str, Any]] = {}
        raws: list[float] = []
        applied_threshold = rubric.applied_threshold_for(pid)
        for did in pdata["dim_ids"]:
            entry = eval_outputs[did]
            raw = float(entry["raw"])
            if not 0 <= raw <= 4:
                raise ScoringError(f"{did} raw={raw} out of [0,4]")
            raws.append(raw)
            dim_block[did] = {
                "raw": raw,
                "evidence_uri": entry.get("evidence_uri", ""),
                # v0.1.1 additive: stamps the rubric-declared decision threshold
                # this dim was compared against at bind. Read by claims defense.
                "applied_threshold": dict(applied_threshold),
            }
        normalized = (sum(raws) / len(raws)) / 4.0
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
    from scanners import DEFAULT_SEED, SCANNER_VERSION
    actual_scanner_version = scanner_version or SCANNER_VERSION
    actual_seed = seed if seed is not None else DEFAULT_SEED

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
    }
    if model_versions:
        response["model_versions"] = dict(model_versions)
    else:
        response["model_versions"] = None
    if diagnostics:
        response["diagnostics"] = diagnostics
    return response
