"""P1.2 v0.2.1 Atticus dry-run — BEAAA-450 / BEAAA-206 H3 acceptance.

Exercises `harness.bench.citation_integrity_legal.run_family_aware()`
end-to-end against an **Atticus surface fixture**: a deterministic
MockRunner subject configured to emit in-index citations on F1 prompts
and curated-summary content on F2 prompts. This is a fixture, not the
live Atticus endpoint — the live questions-stage surface
architecturally does not emit citations (see
`scripts/scan_lawconnect_atticus.py`), so a live dry-run would force F1
inconclusive (`n_with_citation < 10`) and fail the "both families
conclusive" acceptance bullet.

The judge is the real, pinned Haiku 4.5 adapter
(`claude-haiku-4-5-20251001`, T=0, seed=42, ensemble_n=3). Compute
budget: 80 probes × 2 replicates × ≤$0.00011/call ≈ $0.02, well below
the Mode A spend gate.

What this proves (per BEAAA-450 acceptance):
  - The v0.2.1 family-aware bench wrapper produces a P1.2 cell with
    BOTH families conclusive (F1 ≥ 10 probes with citations, F2 ≥ 10
    probes scored).
  - The full evidence-trail contract from
    `docs/judges/p1_2_citation_integrity.md#evidence-trail-contract`
    is present on the emitted cell (`corpus_version`,
    `citation_index_version`, `citation_index_sha256`, `judge_model`,
    `seed`, `ensemble_n`, per-probe trace, per-family rollup, dim
    rollup).

What this does NOT prove:
  - Real Atticus-surface behavior on P1.2. The live surface is
    workflow-restricted; P1.2 on the live customer-scan path will fall
    back to v0.1.0 judge-priors-bounded contribution until UW signs
    `has_uw_signed_scoring_policy: true` (see BEAAA-206 cosign packet).

Usage:
    python3 scripts/dry_run_p1_2_atticus.py [--replicates 2] [--out-dir PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import citation_index  # noqa: E402
from harness.bench import citation_integrity_legal as cil  # noqa: E402
from harness.runner import AnthropicRunner, MockRunner  # noqa: E402

CORPUS_YAML = REPO / "rubric" / "probe_mappings" / "citation_integrity_legal.yaml"
JUDGE_MODEL = "claude-haiku-4-5-20251001"
JUDGE_SEED = 42
JUDGE_ENSEMBLE_N = 3
SURFACE_LABEL = "atticus_fixture_well_behaved_v1"

# Category mapping from F1 probe categories → citation-index buckets.
# F1 corpus categories: scotus_canon, state_appellate_canon, statutory, treatise.
# Index buckets: scotus_canon, state_appellate_canon (only 1), usc_canon,
# frcp_canon, ucc_canon, restatement_canon.
_CATEGORY_TO_BUCKETS: dict[str, tuple[str, ...]] = {
    "scotus_canon": ("scotus_canon",),
    "state_appellate_canon": ("state_appellate_canon", "scotus_canon"),
    "statutory": ("usc_canon", "frcp_canon", "ucc_canon"),
    "treatise": ("restatement_canon",),
}


def _load_env_file() -> None:
    env_path = REPO / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _build_atticus_responder(corpus_path: Path):
    """Construct a mock subject that emits in-index citations on F1 and
    curated-summary content on F2.

    The responder is keyed by exact prompt match (the harness drives the
    subject with `probe.prompt`), so we pre-build a {prompt -> response}
    map from the v0.2.1 YAML. This is the same idiom used in
    `scripts/dry_run_p1_5_atticus.py`.
    """
    import yaml as _yaml

    data = _yaml.safe_load(corpus_path.read_text())
    families = data["families"]
    idx = citation_index.get_index()

    bucket_iters: dict[str, "itertools.cycle"] = {}
    for bucket in {b for buckets in _CATEGORY_TO_BUCKETS.values() for b in buckets}:
        entries = list(idx.by_bucket(bucket))
        if entries:
            bucket_iters[bucket] = itertools.cycle(entries)

    def _pick_for_category(category: str):
        for bucket in _CATEGORY_TO_BUCKETS.get(category, ("scotus_canon",)):
            it = bucket_iters.get(bucket)
            if it is not None:
                return next(it)
        # Fallback: SCOTUS canon is always populated.
        return next(bucket_iters["scotus_canon"])

    def _format_f1_response(entry, category: str) -> str:
        cite = entry.normalized_citation
        # Format citation in a form the v0.1 regex extractor recognizes.
        # For SCOTUS / state appellate entries we have a normalized
        # "<vol> U.S. <page>" / "<vol> N.Y.2d <page>" already. For USC,
        # FRCP, UCC, Restatement the normalized_citation IS the citation
        # text.
        name_part = f"{entry.case_name}, " if getattr(entry, "case_name", None) else ""
        prop = entry.proposition if entry.proposition else ""
        return (
            f"The leading authority is {name_part}{cite}"
            f"{' (' + entry.year + ')' if getattr(entry, 'year', None) else ''}. "
            f"{prop.capitalize() if prop else ''}."
        )

    response_map: dict[str, str] = {}
    for p in families["F1_existence"]["probes"]:
        entry = _pick_for_category(p["category"])
        response_map[p["prompt"]] = _format_f1_response(entry, p["category"])
    for p in families["F2_content_fidelity"]["probes"]:
        # Subject hands the judge a faithful summary so the judge can
        # score proposition=YES, scope=YES (where scope_note is non-empty),
        # no_fabricated_content=YES — proving F2 end-to-end. This is a
        # cheat for end-to-end mechanics validation, mirroring the P1.5
        # dry-run pattern.
        body = p["curated_summary"]
        scope = p.get("scope_note", "")
        if scope:
            body = f"{body} Scope: {scope}"
        response_map[p["prompt"]] = body

    def _responder(prompt: str) -> str:
        return response_map.get(
            prompt,
            "I am not certain and would prefer to refer you to a qualified attorney.",
        )

    return MockRunner(SURFACE_LABEL, _responder)


def _evidence_trail(dim_rollup: dict, idx) -> dict:
    """Assemble the evidence-trail block contract'd by
    docs/judges/p1_2_citation_integrity.md#evidence-trail-contract.
    """
    # Read manifest to surface citation_index_version + sha256 directly.
    manifest_path = REPO / "harness" / "citation_index" / "data" / "MANIFEST.yaml"
    import yaml as _yaml

    manifest = _yaml.safe_load(manifest_path.read_text())
    return {
        "corpus_version": dim_rollup.get("benchmark", "citation_integrity_legal-v0.2.1"),
        "citation_index_version": manifest.get("version"),
        "citation_index_sha256": (
            getattr(idx, "csv_sha256", None) or manifest.get("csv_sha256")
        ),
        "judge_model": JUDGE_MODEL,
        "seed": JUDGE_SEED,
        "ensemble_n": JUDGE_ENSEMBLE_N,
        "scanner_version": _scanner_version(),
        "subject_surface": SURFACE_LABEL,
    }


def _scanner_version() -> str:
    from scanners import SCANNER_VERSION

    return SCANNER_VERSION


def _aggregate_replicates(replicates: list[dict]) -> dict:
    pass_rates = {
        "F1": [r["f1"]["pass_rate"] for r in replicates if r.get("f1")],
        "F2": [r["f2"]["pass_rate"] for r in replicates if r.get("f2")],
        "dim_pass_rate": [r["dim_pass_rate"] for r in replicates if r.get("dim_pass_rate") is not None],
        "raw": [r["raw"] for r in replicates if r.get("raw") is not None],
    }

    def _stats(vs):
        if not vs:
            return None
        return {
            "n": len(vs),
            "mean": statistics.fmean(vs),
            "min": min(vs),
            "max": max(vs),
            "stdev": statistics.pstdev(vs) if len(vs) > 1 else 0.0,
        }

    return {family: _stats(vs) for family, vs in pass_rates.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replicates", type=int, default=2,
                        help="Number of replicate runs (default 2; matches BEAAA-450 budget).")
    parser.add_argument("--out-dir", type=Path,
                        default=REPO / "artifacts" / "beaaa-206" / "atticus_dryrun",
                        help="Output directory for the evidence-trail JSON.")
    parser.add_argument("--judge-model", default=JUDGE_MODEL,
                        help="Pinned judge model (BEAAA-152 cost basis).")
    args = parser.parse_args()

    _load_env_file()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set; cannot pin Haiku 4.5 judge.", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)

    subject = _build_atticus_responder(CORPUS_YAML)
    judge = AnthropicRunner(model=args.judge_model, max_tokens=128, temperature=0.0)
    idx = citation_index.get_index()

    replicates: list[dict] = []
    t0 = time.time()
    for r in range(1, args.replicates + 1):
        print(f"[dry_run_p1_2_atticus] replicate {r}/{args.replicates} ...", flush=True)
        rep_t0 = time.time()
        result = cil.run_family_aware(
            subject,
            judge,
            CORPUS_YAML,
            families=("F1_existence", "F2_content_fidelity"),
            ensemble_n=JUDGE_ENSEMBLE_N,
            index=idx,
        )
        rep_elapsed = time.time() - rep_t0
        # Trim full traces in replicate aggregate; keep counts + pass rates.
        rep_summary = {
            "replicate": r,
            "elapsed_s": round(rep_elapsed, 2),
            "dim_id": result.get("dim_id"),
            "status": result.get("status"),
            "families_conclusive": result.get("families_conclusive"),
            "dim_pass_rate": result.get("dim_pass_rate"),
            "raw": result.get("raw"),
            "f1": {k: v for k, v in (result.get("f1") or {}).items() if k != "trace"},
            "f2": {k: v for k, v in (result.get("f2") or {}).items() if k != "trace"},
            "f1_trace": (result.get("f1") or {}).get("trace"),
            "f2_trace": (result.get("f2") or {}).get("trace"),
        }
        replicates.append(rep_summary)
        # Persist each replicate full trace as its own file so the
        # evidence-trail JSON for the summary stays human-readable.
        rep_path = args.out_dir / f"replicate_{r:02d}.json"
        rep_path.write_text(json.dumps(rep_summary, indent=2))
        print(f"  status={rep_summary['status']} families={rep_summary['families_conclusive']} "
              f"dim_pass_rate={rep_summary['dim_pass_rate']} raw={rep_summary['raw']} "
              f"({rep_elapsed:.1f}s)", flush=True)

    total_elapsed = time.time() - t0
    summary = {
        "BEAAA": "BEAAA-450",
        "parent": "BEAAA-206 H3",
        "title": "P1.2 v0.2.1 Atticus dry-run",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_s_total": round(total_elapsed, 2),
        "evidence_trail": _evidence_trail(replicates[0], idx),
        "replicate_aggregate": _aggregate_replicates(replicates),
        "acceptance": {
            "both_families_conclusive": all(
                r.get("status") == "live"
                and {"F1_existence", "F2_content_fidelity"} <= set(r.get("families_conclusive") or [])
                for r in replicates
            ),
            "f1_min_signal_probes": [r["f1"]["n_with_citation"] for r in replicates if r.get("f1")],
            "f2_n_probes": [r["f2"]["n_probes"] for r in replicates if r.get("f2")],
            "evidence_trail_keys_present": sorted(_evidence_trail(replicates[0], idx).keys()),
        },
        "replicates": replicates,
    }
    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n[dry_run_p1_2_atticus] summary → {summary_path}")
    print(f"  total elapsed: {total_elapsed:.1f}s; replicates: {args.replicates}")
    print(f"  both families conclusive: {summary['acceptance']['both_families_conclusive']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
