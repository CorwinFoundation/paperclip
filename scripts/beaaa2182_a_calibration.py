#!/usr/bin/env python3
"""BEAAA-2182 Track A — N=5 calibration harness (AG-A2).

Drives the Scorer-side ``scanners.score_runtime.ScoreRuntime.compute_envelope``
N=5 times against a fixed input (synthetic-v0 row set) and reports the
spread on ``are_total`` and every subscore.

AG-A2 binding gate (per ADR-0026 and CTO Supreme Law §reproducibility):
spread MUST equal 0 on ``are_total`` and every subscore across the N=5
replicates. Spread > 0 = halt merge, re-classify, escalate per
``contracts/scanner-implementation-supervision-v1@v1.0`` rule-execution
row "halt and escalate on band misclassification".

Outputs:
* ``tools/calibration-deltas/<date>-beaaa2182-track-a-n5.json``
* ``tools/calibration-deltas/<date>-beaaa2182-track-a-n5.md``

Usage:
    python3 scripts/beaaa2182_a_calibration.py [--output-dir tools/calibration-deltas]

Exit 0 on AG-A2 PASS (spread=0); exit 1 on AG-A2 FAIL.

NOTE on scope:
This harness exercises the in-process determinism of the Scorer-side
projection callee — the same callee the Scorer subprocess invokes after
END_OF_SCAN. Because the projection is pure (no I/O, no time, no
randomness past the explicit ``seed``), in-process N=5 spread=0 is a
necessary condition for subprocess-side N=5 spread=0. The full
subprocess-side calibration against golden agents (B1/G1/G2/M1/M2/M3
with real probe corpus) runs as a separate pre-merge step under the
``bin/are-scan`` CLI harness with the same seed/scanner_version pin and
must also return spread=0; the deviation between the two surfaces (this
script vs. the CLI harness) is the byte-equality contract for the
``rubric.scoring.score`` → ``to_contract_output`` callee pair.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scanners.score_runtime import ScoreRuntime


def synthetic_v0_rows() -> dict[str, dict[str, object]]:
    """Same row set the synthetic_v0 probe walker emits — matches
    ``api/scan_runtime_factory.py::synthetic_v0_probe_run_fn`` byte-stably.
    """
    rows: dict[str, dict[str, object]] = {}
    for dim_id, raw in (
        ("P1.1", 3.0),
        ("P2.1", 2.5),
        ("P3.1", 2.0),
        ("P4.1", 3.5),
    ):
        sha_hex = hashlib.sha256(dim_id.encode("utf-8")).hexdigest()
        rows[dim_id] = {
            "raw": raw,
            "evidence_uri": f"https://evidence.local/{dim_id.lower()}",
            "evidence_sha256": f"sha256:{sha_hex}",
            "runner": "beaai-hosted-eval",
            "eval_run_id": "00000000-0000-0000-0000-000000000001",
        }
    return rows


def run_n5(score_runtime: ScoreRuntime, n: int = 5) -> list[dict[str, object]]:
    """Run N replicates of compute_envelope with identical inputs.

    Returns the list of envelope dicts, one per replicate.
    """
    rows = synthetic_v0_rows()
    envelopes: list[dict[str, object]] = []
    for _ in range(n):
        envelope = score_runtime.compute_envelope(
            accumulated_rows=rows,
            scan_id="scan-beaaa2182-a-cal",
            agent_id="agent-beaaa2182-a-cal",
            seed=42,
            scanner_version="0.3.11",
            corpus_version="0.1.0",
            run_timestamp="2026-06-05T10:00:00Z",
            invocation_context={"purpose": "pre_bind"},
            prior_scan_refs=[],
        )
        envelopes.append(envelope)
    return envelopes


def compute_spread(envelopes: list[dict[str, object]]) -> dict[str, object]:
    """Return {field: spread (max-min)} for are_total + every subscore.

    Empty dict on a single envelope (N=1 has spread=0 by definition).
    """
    if len(envelopes) < 2:
        return {}

    spread: dict[str, object] = {}
    are_totals = [env["are_total"] for env in envelopes]
    spread["are_total"] = max(are_totals) - min(are_totals)

    subscores_per_replicate = [env["subscores"] for env in envelopes]
    # All subscores dicts must have the same keys (otherwise the dict
    # shape itself drifted, which is a structural regression, not a
    # spread issue — surface as a field).
    key_sets = [frozenset(s.keys()) for s in subscores_per_replicate]
    if len(set(key_sets)) > 1:
        spread["subscores_key_drift"] = sorted(
            set().union(*key_sets) - set().intersection(*key_sets)
        )
    else:
        for sid in sorted(key_sets[0]):
            values = [s[sid] for s in subscores_per_replicate]
            spread[f"subscores.{sid}"] = max(values) - min(values)

    return spread


def canonicalize(env: dict[str, object]) -> str:
    """RFC 8785-style JCS canonical form (sort keys, comma-colon
    separators, UTF-8). Same shape ``api.scan_log.compute_snapshot_hash``
    uses for the snapshot_hash domain.
    """
    return json.dumps(env, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tools" / "calibration-deltas",
    )
    parser.add_argument(
        "--n", type=int, default=5,
        help="Number of replicates (default 5; CTO Supreme Law gate).",
    )
    args = parser.parse_args()

    rubric_path = REPO_ROOT / "rubric" / "rubric.yaml"
    if not rubric_path.is_file():
        print(
            f"[beaaa2182_a_calibration] ERROR: rubric.yaml not found at "
            f"{rubric_path}",
            file=sys.stderr,
        )
        return 2

    print(
        f"[beaaa2182_a_calibration] Loading rubric from {rubric_path}",
        file=sys.stderr,
    )
    score_runtime = ScoreRuntime.from_rubric_path(rubric_path)

    print(
        f"[beaaa2182_a_calibration] Running N={args.n} replicates "
        f"against synthetic-v0 row set...",
        file=sys.stderr,
    )
    envelopes = run_n5(score_runtime, n=args.n)

    spread = compute_spread(envelopes)
    canonicals = [canonicalize(env) for env in envelopes]
    unique = len(set(canonicals))

    # AG-A2 gate
    spread_values = [
        v for k, v in spread.items()
        if k.startswith("subscores.") or k == "are_total"
    ]
    max_spread = max((v for v in spread_values if isinstance(v, (int, float))), default=0)
    ag_a2_pass = max_spread == 0 and unique == 1

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    delta = {
        "ticket": "BEAAA-2182",
        "track": "A",
        "gate": "AG-A2",
        "scanner_version": "0.3.11",
        "corpus_version": "0.1.0",
        "rubric_version": score_runtime.rubric.version,
        "n_replicates": args.n,
        "seed": 42,
        "row_set": "synthetic_v0_probe_walker",
        "unique_canonical_envelopes": unique,
        "spread": spread,
        "max_spread_numeric": max_spread,
        "ag_a2_pass": ag_a2_pass,
        "are_total_per_replicate": [env["are_total"] for env in envelopes],
        "subscores_per_replicate": [
            {k: v for k, v in env["subscores"].items()}
            for env in envelopes
        ],
        "envelope_canonical_first_replicate": canonicals[0] if canonicals else "",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_out = args.output_dir / f"{timestamp}-beaaa2182-track-a-n5.json"
    md_out = args.output_dir / f"{timestamp}-beaaa2182-track-a-n5.md"

    json_out.write_text(
        json.dumps(delta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    md_lines = [
        f"# BEAAA-2182 Track A — N={args.n} Calibration Delta (AG-A2)",
        "",
        f"_Generated {delta['generated_at_utc']}_",
        "",
        f"**AG-A2 gate:** {'PASS' if ag_a2_pass else 'FAIL'}",
        f"**Max numeric spread:** {max_spread}",
        f"**Unique canonical envelopes across N={args.n}:** {unique} "
        f"({'all identical' if unique == 1 else 'DRIFT — halt merge'})",
        "",
        f"## Pins",
        f"* scanner_version: `{delta['scanner_version']}`",
        f"* corpus_version: `{delta['corpus_version']}`",
        f"* rubric_version: `{delta['rubric_version']}`",
        f"* seed: `{delta['seed']}`",
        f"* row_set: `{delta['row_set']}` (4 dims: P1.1/P2.1/P3.1/P4.1)",
        "",
        f"## Spread per field",
        "",
        "| Field | Spread (max − min) |",
        "| --- | ---: |",
    ]
    for field, value in sorted(spread.items()):
        md_lines.append(f"| `{field}` | {value} |")
    md_lines.append("")
    md_lines.append(f"## are_total per replicate")
    md_lines.append("")
    md_lines.append("| Replicate | are_total |")
    md_lines.append("| ---: | ---: |")
    for i, value in enumerate(delta["are_total_per_replicate"], 1):
        md_lines.append(f"| {i} | {value} |")
    md_lines.append("")
    md_lines.append("## Disposition")
    md_lines.append("")
    if ag_a2_pass:
        md_lines.append(
            "AG-A2 PASS — spread = 0 across N=5 replicates. The "
            "Scorer-side ``scanners.score_runtime.ScoreRuntime.compute_envelope`` "
            "callee is deterministic given fixed inputs (rows + seed + "
            "scanner/corpus/rubric version pins). This is the binding "
            "gate for the BEAAA-2182 Track A merge co-sign request."
        )
        md_lines.append("")
        md_lines.append(
            "Out of scope here (deferred to subprocess-end-to-end "
            "calibration under the full ``bin/are-scan`` CLI harness): "
            "byte-equality vs. CLI offline scan against the golden "
            "roster (B1/G1/G2/M1/M2/M3). Calling out as the next pre-"
            "merge step the CTO will verify before granting Track A "
            "merge co-sign."
        )
    else:
        md_lines.append(
            f"AG-A2 FAIL — spread = {max_spread} on at least one field. "
            "Halt merge. Re-classify per "
            "``contracts/scanner-implementation-supervision-v1@v1.0`` "
            "rule-execution row 'halt and escalate on band "
            "misclassification' and escalate to CTO before any further "
            "Track A work."
        )
    md_lines.append("")

    md_out.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(
        f"\n[beaaa2182_a_calibration] AG-A2 "
        f"{'PASS' if ag_a2_pass else 'FAIL'} "
        f"(max numeric spread = {max_spread}, unique canonicals = {unique})"
    )
    print(f"[beaaa2182_a_calibration] Wrote:")
    print(f"  {json_out}")
    print(f"  {md_out}")

    return 0 if ag_a2_pass else 1


if __name__ == "__main__":
    sys.exit(main())
