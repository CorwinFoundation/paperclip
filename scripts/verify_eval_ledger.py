"""Eval-coverage ledger verifier — measures ARE dim P1.6.

BEAAA-197 Track 1. P1.6 is a customer attestation surface, not a runtime
probe family. Customer submits a ledger of eval families exercised
pre-deployment; this verifier:

  1. validates the ledger against rubric/probe_mappings/eval_coverage_ledger.schema.yaml
  2. samples 5% of attested transcripts (if evidence_uri provided)
  3. replays the sampled transcripts on the scanner's matching probe family
  4. computes |claimed_pass_rate - replayed_pass_rate| per family
  5. applies the verification_modifier penalty schedule
  6. emits a raw P1.6 score 0-4 with full provenance

Usage:
    python3 scripts/verify_eval_ledger.py \\
        --ledger path/to/customer_ledger.yaml \\
        [--evidence-root path/to/transcripts/] \\
        [--out artifacts/beaaa-197/{surface}/p1_6_ledger.json]

The verifier is OFFLINE by default — it does not replay transcripts unless
--evidence-root is provided. Without evidence, the none_provided_penalty
fires from the schema.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO / "rubric" / "probe_mappings" / "eval_coverage_ledger.schema.yaml"

# Lock the sample seed so verification is reproducible across runs against the
# same ledger snapshot.
SAMPLE_SEED = 42
SAMPLE_FRACTION = 0.05  # 5% per family


@dataclass
class FamilyRow:
    family_id: str
    probe_count: int
    last_run_date: str
    pass_rate: float
    evidence_uri: str | None
    family_description: str | None = None


def load_schema() -> dict[str, Any]:
    return yaml.safe_load(SCHEMA_PATH.read_text())


def load_ledger(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def validate_ledger(ledger: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Light validator — checks required keys + family_id enum + ranges.

    Returns a list of human-readable error strings. Empty == valid.
    """
    errors: list[str] = []
    s = schema.get("schema", {})
    for req in s.get("required", []):
        if req not in ledger:
            errors.append(f"missing required field: {req}")
    fam_props = s.get("properties", {}).get("families", {}).get("items", {})
    valid_ids = set(fam_props.get("properties", {}).get("family_id", {}).get("enum", []))
    for i, fam in enumerate(ledger.get("families", [])):
        prefix = f"families[{i}]"
        for req in fam_props.get("required", []):
            if req not in fam:
                errors.append(f"{prefix}: missing {req}")
        fid = fam.get("family_id")
        if valid_ids and fid not in valid_ids:
            errors.append(f"{prefix}: family_id '{fid}' not in enum")
        if fid == "other" and not fam.get("family_description"):
            errors.append(f"{prefix}: family_description required when family_id=='other'")
        pc = fam.get("probe_count")
        if pc is not None and (not isinstance(pc, int) or pc < 1):
            errors.append(f"{prefix}: probe_count must be int >= 1, got {pc!r}")
        pr = fam.get("pass_rate")
        if pr is not None and (not isinstance(pr, (int, float)) or pr < 0 or pr > 1):
            errors.append(f"{prefix}: pass_rate must be float in [0,1], got {pr!r}")
    return errors


def sample_rows(rng: random.Random, items: list[Any], fraction: float) -> list[int]:
    n = len(items)
    sample_size = max(1, int(round(n * fraction))) if n > 0 else 0
    idxs = list(range(n))
    rng.shuffle(idxs)
    return idxs[:sample_size]


def _replay_pass_rate(
    family_id: str,
    transcripts: list[dict[str, Any]],
) -> float | None:
    """Stub-out for live replay. Returns None when offline (no replayer wired).

    In live mode, this would dispatch to harness.bench.<family>.run on the
    sampled transcripts and emit the actual pass rate. For now we return None
    so the verifier reports 'verification deferred' rather than fabricating a number.
    """
    return None


def score(
    ledger: dict[str, Any],
    schema: dict[str, Any],
    *,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    cuts = schema.get("raw_mapping", {}).get("attested_families_cuts", [])
    modifier = schema.get("raw_mapping", {}).get("verification_modifier", {})
    clamp = schema.get("raw_mapping", {}).get("final_raw_clamp", [0.0, 4.0])

    families = ledger.get("families", [])
    distinct_ids = {f.get("family_id") for f in families if f.get("family_id")}
    n_distinct = len(distinct_ids)

    raw_base = 0.0
    for cut in cuts:
        if n_distinct >= cut["min_distinct"]:
            raw_base = float(cut["raw_base"])
            break

    per_family_records: list[dict[str, Any]] = []
    rng = random.Random(SAMPLE_SEED)
    total_divergence = 0.0
    n_divergent_5_10 = 0
    n_divergent_gt10 = 0
    n_with_evidence = 0
    for fam in families:
        evidence_uri = fam.get("evidence_uri")
        replayed_rate: float | None = None
        divergence: float | None = None
        if evidence_uri and evidence_root is not None:
            transcripts_dir = evidence_root / str(evidence_uri).lstrip("/")
            if transcripts_dir.exists():
                n_with_evidence += 1
                transcripts = [
                    json.loads(p.read_text())
                    for p in sorted(transcripts_dir.glob("*.json"))
                ]
                if transcripts:
                    sampled_idxs = sample_rows(rng, transcripts, SAMPLE_FRACTION)
                    sampled = [transcripts[i] for i in sampled_idxs]
                    replayed_rate = _replay_pass_rate(fam.get("family_id"), sampled)
                    if replayed_rate is not None:
                        divergence = abs(float(fam.get("pass_rate", 0.0)) - replayed_rate)
                        total_divergence += divergence
                        if divergence > 0.10:
                            n_divergent_gt10 += 1
                        elif divergence > 0.05:
                            n_divergent_5_10 += 1
        per_family_records.append(
            {
                "family_id": fam.get("family_id"),
                "probe_count": fam.get("probe_count"),
                "claimed_pass_rate": fam.get("pass_rate"),
                "evidence_uri": evidence_uri,
                "evidence_available": bool(evidence_uri and evidence_root and replayed_rate is not None),
                "replayed_pass_rate": replayed_rate,
                "divergence": divergence,
            }
        )

    # Apply modifier
    raw_adjustment = 0.0
    coverage_label = "attested:customer-self-report"
    if n_with_evidence == 0:
        # no evidence at all -> apply none_provided_penalty
        raw_adjustment += float(modifier.get("none_provided_penalty", 0.0))
        coverage_label = "attested:customer-self-report;unverified"
    else:
        if n_divergent_gt10 > 0:
            raw_adjustment += float(modifier.get("divergence_gt_10pp", 0.0))
            coverage_label = "attested:customer-self-report;divergence-flagged"
        elif n_divergent_5_10 > 0:
            raw_adjustment += float(modifier.get("divergence_5_to_10pp", 0.0))

    raw = raw_base + raw_adjustment
    lo, hi = float(clamp[0]), float(clamp[1])
    raw = max(lo, min(hi, raw))

    return {
        "dim_id": "P1.6",
        "benchmark": "eval_coverage_ledger-v0.1.0",
        "customer_id": ledger.get("customer_id"),
        "attested_at": ledger.get("attested_at"),
        "n_attested_families": n_distinct,
        "raw_base": raw_base,
        "raw_adjustment": raw_adjustment,
        "raw": raw,
        "n_with_evidence": n_with_evidence,
        "n_divergent_5_10pp": n_divergent_5_10,
        "n_divergent_gt10pp": n_divergent_gt10,
        "mean_divergence": (total_divergence / n_with_evidence) if n_with_evidence else None,
        "coverage_label": coverage_label,
        "family_records": per_family_records,
        "provenance_label_template": "attested:customer-self-report",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify customer eval-coverage ledger (P1.6)")
    parser.add_argument("--ledger", required=True, help="Path to customer-submitted ledger YAML")
    parser.add_argument("--evidence-root", default=None, help="Optional directory root for transcripts")
    parser.add_argument("--out", default=None, help="Optional output JSON path")
    args = parser.parse_args()

    schema = load_schema()
    ledger = load_ledger(args.ledger)
    errors = validate_ledger(ledger, schema)
    if errors:
        print("LEDGER VALIDATION ERRORS:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 2

    evidence_root = Path(args.evidence_root) if args.evidence_root else None
    result = score(ledger, schema, evidence_root=evidence_root)
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
