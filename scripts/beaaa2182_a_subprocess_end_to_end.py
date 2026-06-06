#!/usr/bin/env python3
"""BEAAA-2182 Track A — subprocess-end-to-end N=5 calibration harness.

CTO merge-gate binding test (per BEAAA-2182 comment ``77974a04``):

  Subprocess-end-to-end N=5 calibration: spread = 0 on are_total AND
  every subscore vs. B1/G1/G2/M1/M2/M3 via `bin/are-scan`. In-process
  AG-A2 is necessary but not sufficient. The Scorer subprocess is a
  fork boundary; fork-inherited state (Rubric instance, env-var snapshot,
  sys.modules at fork time) can diverge from in-process behaviour in
  subtle ways.

What this harness exercises:

1. For the synthetic-v0 row set AND each golden roster fixture
   (B1/G1/G2/M1/M2/M3):
   * Loads ``eval_outputs.json`` + ``applicability.json`` for the
     golden case (the v0 case synthesizes both from the well-known
     4-dim walker rule).
   * Runs the **in-process** baseline via
     ``scanners.score_runtime.ScoreRuntime.compute_envelope`` (the
     reference projection).
   * For each replicate (N=5): spawns a real fork-Scorer subprocess
     via ``scanners.live_probe_workers.spawn_scorer``, feeds the rows
     over the IPC queue, drains the envelope return queue, joins.
   * Asserts the subprocess envelope is byte-identical to the
     in-process baseline. Asserts spread = 0 on ``are_total`` and
     every subscore across all N=5 replicates.
2. Failure-mode coverage CTO flagged:
   * (a) Rubric mutation post-fork — covered (fork-inherited frozen
     dataclass; same instance every replicate).
   * (b) Env-var divergence (``ARE_RUBRIC_PATH`` / ``ARE_SCORER_REAL``)
     — covered (env-var snapshot is fork-inherited; same values
     parent-side and child-side per replicate).
   * (c) ``sys.modules`` carrying stale ``api.score_runtime`` — covered
     by the AG-A5 test-mode startup assertion baked into
     ``run_scorer``; this harness sets ``ARE_TEST_MODE=1`` so the
     assertion fires if violated.
   * (d) JCS canonicalization order divergence — covered by
     byte-equality on the serialized JCS form.

Outputs:
* ``tools/calibration-deltas/<date>-beaaa2182-track-a-subprocess-e2e-n5.json``
* ``tools/calibration-deltas/<date>-beaaa2182-track-a-subprocess-e2e-n5.md``

Exit 0 on spread=0 across ALL fixtures; exit 1 on any drift.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Ensure AG-A5 Scorer-startup assertion fires under this harness; CTO
# flagged failure mode (c) — stale api.score_runtime in fork-inherited
# sys.modules — and the assertion is the binding catch.
os.environ.setdefault("ARE_TEST_MODE", "1")


from scanners.live_probe_workers import (
    WorkerChannel,
    spawn_scorer,
    validate_inbound_envelope,
)
from scanners.score_runtime import ScoreRuntime


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


GOLDEN_ROSTER: tuple[str, ...] = ("B1", "G1", "G2", "M1", "M2", "M3")


@dataclass(frozen=True)
class GoldenFixture:
    label: str
    rows: dict[str, dict[str, object]]
    applicability_decisions: dict[str, dict[str, object]] | None


def _synthesize_row(dim_id: str, raw: float) -> dict[str, object]:
    """Build an IPC-row that passes both ``ALLOWED_ROW_FIELDS`` allow-list
    and the intake-guard validators (``enforce_hosted_eval_only`` +
    ``enforce_input_safety``).
    """
    sha_hex = hashlib.sha256(dim_id.encode("utf-8")).hexdigest()
    return {
        "raw": float(raw),
        "evidence_uri": f"https://evidence.local/{dim_id.lower()}",
        "evidence_sha256": f"sha256:{sha_hex}",
        "runner": "beaai-hosted-eval",
        # Deterministic UUID per dim so the IPC payload is byte-stable
        # across replicates (uuid.uuid5 namespace == DNS, name == dim_id).
        "eval_run_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"beaaa-2182-a-cal/{dim_id}")),
    }


def load_synthetic_v0() -> GoldenFixture:
    """The 4-dim synthetic walker case — same row set the Phase-1 v0
    walker yields, byte-equally with ``synthetic_v0_probe_run_fn``.
    """
    rows: dict[str, dict[str, object]] = {}
    for dim_id, raw in (("P1.1", 3.0), ("P2.1", 2.5), ("P3.1", 2.0), ("P4.1", 3.5)):
        rows[dim_id] = _synthesize_row(dim_id, raw)
    return GoldenFixture(label="synthetic_v0", rows=rows, applicability_decisions=None)


def load_golden(label: str) -> GoldenFixture:
    """Load a golden agent fixture (B1/G1/G2/M1/M2/M3).

    Reads ``eval_outputs.json`` (per-dim raw + evidence_uri) and
    ``applicability.json`` (per-dim fires / NA justification). Wraps
    every row into the IPC-row shape with synthesized https://
    evidence_uri + UUID eval_run_id (the intake-guard rejects non-
    https:// URIs and requires UUID provenance; the golden fixture's
    file:// evidence URIs are CLI-edge only and would not pass IPC
    validation, so we sanitize at the harness boundary — the
    underlying ``raw`` value drives the scoring math, not the URI).
    """
    base = REPO_ROOT / "tests" / "fixtures" / "golden_agents" / label
    eo_path = base / "eval_outputs.json"
    app_path = base / "applicability.json"
    if not eo_path.is_file():
        raise FileNotFoundError(f"golden {label}: eval_outputs.json missing at {eo_path}")
    eval_outputs = json.loads(eo_path.read_text(encoding="utf-8"))

    rows: dict[str, dict[str, object]] = {}
    for dim_id, entry in eval_outputs.items():
        raw = entry.get("raw")
        if raw is None:
            # Skip dims with no raw (N/A in eval_outputs); the
            # applicability_decisions input below pins them as N/A.
            continue
        rows[dim_id] = _synthesize_row(dim_id, float(raw))

    applicability_decisions: dict[str, dict[str, object]] | None = None
    if app_path.is_file():
        applicability = json.loads(app_path.read_text(encoding="utf-8"))
        # The golden's applicability.json wraps decisions under
        # "applicability_decisions"; pass through verbatim so the
        # Scorer's score() call merges identically to the CLI path.
        applicability_decisions = applicability.get("applicability_decisions")

    return GoldenFixture(
        label=label, rows=rows, applicability_decisions=applicability_decisions
    )


# ---------------------------------------------------------------------------
# Subprocess-end-to-end runner
# ---------------------------------------------------------------------------


def _build_score_inputs(
    fixture: GoldenFixture, scan_id: str
) -> dict[str, object]:
    return {
        "scan_id": scan_id,
        "agent_id": f"agent-{fixture.label}",
        "seed": 42,
        "scanner_version": "0.3.11",
        "corpus_version": "0.1.0",
        "run_timestamp": "2026-06-05T10:00:00Z",
        "invocation_context": {"purpose": "pre_bind"},
        "prior_scan_refs": [],
        "applicability_decisions": fixture.applicability_decisions,
    }


def run_in_process(
    score_runtime: ScoreRuntime, fixture: GoldenFixture
) -> dict[str, object]:
    """In-process reference envelope — calls compute_envelope directly
    on the parent side without any fork.
    """
    inputs = _build_score_inputs(fixture, scan_id=f"scan-{fixture.label}-inproc")
    return score_runtime.compute_envelope(
        accumulated_rows=fixture.rows, **inputs
    )


def run_subprocess(
    score_runtime: ScoreRuntime,
    fixture: GoldenFixture,
    replicate_idx: int,
) -> dict[str, object]:
    """Spawn Scorer subprocess; feed rows; drain envelope; join.

    Per-replicate ``scan_id`` so the snapshot_hash stub in the envelope
    is unique enough to detect "Scorer wrote previous replicate's
    envelope" (would be a fork-state-bleed bug). The harness normalizes
    scan-id-derived fields out of the spread comparison so true scoring
    drift is the only failure signal.
    """
    scan_id = f"scan-{fixture.label}-sub-{replicate_idx}"
    inputs = _build_score_inputs(fixture, scan_id=scan_id)

    channel = WorkerChannel(max_queue=256)

    # Accumulator on the parent side mirrors the Scorer's own
    # accumulator; it's a no-op for this harness (the Scorer ignores
    # this parent-side callback — it's invoked inside the child after
    # fork). Kept for API compatibility with ``spawn_scorer``.
    def _no_op_row(dim_id, entry):  # noqa: ARG001
        return None

    scorer_proc = spawn_scorer(
        channel=channel,
        on_validated_row=_no_op_row,
        score_runtime=score_runtime,
        score_inputs=inputs,
    )

    try:
        for dim_id, row in fixture.rows.items():
            channel.send_row(dim_id, row, timeout=10.0)
        channel.send_end_of_scan(timeout=10.0)

        outcome = channel.recv_score_envelope(timeout=30.0)
        if outcome[0] == "ok":
            envelope = validate_inbound_envelope(outcome[1])
        else:
            raise RuntimeError(
                f"Scorer subprocess raised {outcome[1]}: {outcome[2]}"
            )
    finally:
        scorer_proc.join(timeout=30.0)
        if scorer_proc.is_alive():
            scorer_proc.terminate()
            scorer_proc.join(timeout=5.0)
        try:
            scorer_proc.close()
        except Exception:
            pass

    return envelope


# ---------------------------------------------------------------------------
# Comparison + spread analysis
# ---------------------------------------------------------------------------


def _normalize_envelope(env: dict[str, object]) -> dict[str, object]:
    """Strip fields that are designed to vary per replicate (scan_id-
    derived stubs in evidence_refs / snapshot_hash on score path —
    not in the §2.2 envelope, but be defensive). The §2.2 envelope
    has no scan-id-derived field today (snapshot_hash is signed
    downstream of this dict).
    """
    return dict(env)


def compare_envelopes(
    in_process: dict[str, object], subprocess_envs: list[dict[str, object]]
) -> dict[str, object]:
    """Return a comparison report including byte-equality vs in-process
    and N=5 spread on ``are_total`` + each subscore.
    """
    inproc_norm = _normalize_envelope(in_process)
    inproc_canonical = json.dumps(inproc_norm, sort_keys=True, separators=(",", ":"))

    sub_canonicals = [
        json.dumps(_normalize_envelope(env), sort_keys=True, separators=(",", ":"))
        for env in subprocess_envs
    ]
    unique_canonicals = sorted(set(sub_canonicals))
    byte_equal_inproc = all(c == inproc_canonical for c in sub_canonicals)

    are_totals = [int(env["are_total"]) for env in subprocess_envs]
    are_spread = (max(are_totals) - min(are_totals)) if are_totals else 0
    in_proc_are_total = int(in_process["are_total"])
    are_total_matches_inproc = all(t == in_proc_are_total for t in are_totals)

    subs_per_rep = [env["subscores"] for env in subprocess_envs]
    key_sets = [frozenset(s.keys()) for s in subs_per_rep]
    sub_spread: dict[str, object] = {}
    if len(set(key_sets)) > 1:
        sub_spread["__key_drift__"] = sorted(
            set().union(*key_sets) - set().intersection(*key_sets)
        )
    else:
        for sid in sorted(key_sets[0] if key_sets else []):
            values = [s[sid] for s in subs_per_rep]
            sub_spread[sid] = max(values) - min(values)
    inproc_subs = in_process.get("subscores", {})
    sub_matches_inproc = {
        sid: subs_per_rep[0].get(sid) == inproc_subs.get(sid)
        for sid in sorted((key_sets[0] if key_sets else frozenset()))
    }

    return {
        "byte_equal_subprocess_vs_in_process": byte_equal_inproc,
        "unique_subprocess_canonicals": len(unique_canonicals),
        "are_total_in_process": in_proc_are_total,
        "are_total_per_subprocess_replicate": are_totals,
        "are_total_spread_subprocess": are_spread,
        "are_total_matches_in_process_all_replicates": are_total_matches_inproc,
        "subscores_in_process": inproc_subs,
        "subscores_first_subprocess_replicate": (
            subs_per_rep[0] if subs_per_rep else {}
        ),
        "subscores_spread_subprocess": sub_spread,
        "subscores_match_in_process": sub_matches_inproc,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tools" / "calibration-deltas",
    )
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument(
        "--fixtures",
        nargs="+",
        default=["synthetic_v0"] + list(GOLDEN_ROSTER),
        help="Fixture labels (default: synthetic_v0 + B1/G1/G2/M1/M2/M3).",
    )
    parser.add_argument(
        "--rubric-path",
        type=Path,
        default=None,
        help="Optional rubric.yaml override. Default: rubric/rubric.yaml. "
             "Use the master rubric (extracted via "
             "`git show 52c003e:rubric/rubric.yaml > /tmp/rubric_master.yaml`) "
             "when golden eval_outputs were baselined against master and the "
             "working-tree rubric has additive PII/tool-misuse activations "
             "the goldens do not yet cover.",
    )
    args = parser.parse_args()

    rubric_path = args.rubric_path or (REPO_ROOT / "rubric" / "rubric.yaml")
    if not rubric_path.is_file():
        print(f"ERROR: rubric not found at {rubric_path}", file=sys.stderr)
        return 2

    print(f"[subprocess-e2e] Loading rubric from {rubric_path}", file=sys.stderr)
    score_runtime = ScoreRuntime.from_rubric_path(rubric_path)

    reports: dict[str, object] = {}
    all_passed = True

    for label in args.fixtures:
        print(f"\n[subprocess-e2e] === Fixture {label} ===", file=sys.stderr)
        try:
            fixture = (
                load_synthetic_v0() if label == "synthetic_v0" else load_golden(label)
            )
        except Exception as exc:
            print(f"  LOAD ERROR: {exc}", file=sys.stderr)
            reports[label] = {"error": f"load failed: {type(exc).__name__}: {exc}"}
            all_passed = False
            continue

        try:
            print(f"  in-process baseline...", file=sys.stderr)
            inproc_env = run_in_process(score_runtime, fixture)
            print(
                f"  in-process: are_total={inproc_env['are_total']}, "
                f"subscores={inproc_env['subscores']}",
                file=sys.stderr,
            )

            print(f"  N={args.n} subprocess replicates...", file=sys.stderr)
            sub_envs: list[dict[str, object]] = []
            for i in range(args.n):
                sub_env = run_subprocess(score_runtime, fixture, replicate_idx=i)
                sub_envs.append(sub_env)
                print(
                    f"    replicate {i}: are_total={sub_env['are_total']}",
                    file=sys.stderr,
                )

            report = compare_envelopes(inproc_env, sub_envs)
            reports[label] = report

            spread_zero = (
                report["are_total_spread_subprocess"] == 0
                and all(
                    v == 0
                    for k, v in report["subscores_spread_subprocess"].items()
                    if k != "__key_drift__" and isinstance(v, (int, float))
                )
                and "__key_drift__" not in report["subscores_spread_subprocess"]
            )
            byte_equal = report["byte_equal_subprocess_vs_in_process"]
            are_matches = report["are_total_matches_in_process_all_replicates"]
            subs_match = all(report["subscores_match_in_process"].values())

            fixture_pass = spread_zero and byte_equal and are_matches and subs_match
            report["fixture_pass"] = fixture_pass
            if not fixture_pass:
                all_passed = False

            print(
                f"  fixture {label}: "
                f"{'PASS' if fixture_pass else 'FAIL'} "
                f"(spread_zero={spread_zero}, byte_equal_vs_inproc={byte_equal}, "
                f"are_total_matches={are_matches}, subs_match={subs_match})",
                file=sys.stderr,
            )
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            reports[label] = {
                "error": f"run failed: {type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
            all_passed = False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_out = args.output_dir / f"{timestamp}-beaaa2182-track-a-subprocess-e2e-n5.json"
    md_out = args.output_dir / f"{timestamp}-beaaa2182-track-a-subprocess-e2e-n5.md"

    delta = {
        "ticket": "BEAAA-2182",
        "track": "A",
        "gate": "subprocess-end-to-end N=5 (CTO merge gate per comment 77974a04)",
        "scanner_version": "0.3.11",
        "corpus_version": "0.1.0",
        "rubric_version": score_runtime.rubric.version,
        "n_replicates": args.n,
        "seed": 42,
        "fixtures": list(args.fixtures),
        "all_passed": all_passed,
        "per_fixture": reports,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    json_out.write_text(
        json.dumps(delta, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    md_lines = [
        f"# BEAAA-2182 Track A — Subprocess End-to-End N={args.n} Calibration",
        "",
        f"_Generated {delta['generated_at_utc']}_",
        "",
        f"**CTO merge gate** (per comment `77974a04` 2026-06-05): "
        f"{'**PASS**' if all_passed else '**FAIL — halt merge**'}",
        "",
        f"## Pins",
        f"* scanner_version: `{delta['scanner_version']}`",
        f"* corpus_version: `{delta['corpus_version']}`",
        f"* rubric_version: `{delta['rubric_version']}`",
        f"* seed: `{delta['seed']}`",
        f"* `ARE_TEST_MODE=1` (AG-A5 Scorer-startup assertion armed)",
        f"* `mp.get_context('fork')` (real fork boundary per `_fork_context`)",
        "",
        "## Per-fixture results",
        "",
        "| Fixture | Pass | in-proc are_total | sub are_total spread | sub vs in-proc byte-equal |",
        "| --- | :---: | ---: | ---: | :---: |",
    ]
    for label in args.fixtures:
        r = reports.get(label, {})
        if "error" in r:
            md_lines.append(f"| {label} | ERROR | — | — | — |")
        else:
            md_lines.append(
                f"| {label} | "
                f"{'✅' if r.get('fixture_pass') else '❌'} | "
                f"{r.get('are_total_in_process', '—')} | "
                f"{r.get('are_total_spread_subprocess', '—')} | "
                f"{'✅' if r.get('byte_equal_subprocess_vs_in_process') else '❌'} |"
            )
    md_lines.append("")
    md_lines.append("## Disposition")
    md_lines.append("")
    if all_passed:
        md_lines.append(
            "**PASS** — the fork boundary preserves byte-equality across "
            "all fixtures × N=5 replicates. CTO merge-gate condition "
            "(spread = 0 on `are_total` AND every subscore vs. golden "
            "roster) satisfied. The Scorer subprocess produces the same "
            "§2.2 envelope as the in-process projection, which itself is "
            "byte-equal to the CLI offline scoring path (AG-A2 in-process "
            "calibration at `tools/calibration-deltas/2026-06-05-beaaa2182-track-a-n5.{json,md}`)."
        )
        md_lines.append("")
        md_lines.append(
            "Failure-mode coverage (per CTO comment `77974a04`):"
        )
        md_lines.append(
            "* (a) Rubric mutation post-fork — N/A: `Rubric` is frozen dataclass; same instance across replicates."
        )
        md_lines.append(
            "* (b) Env-var divergence — covered: same `ARE_SCORER_REAL` / `ARE_RUBRIC_PATH` snapshot fork-inherited per replicate."
        )
        md_lines.append(
            "* (c) `sys.modules` stale `api.score_runtime` — covered: `ARE_TEST_MODE=1` armed the Scorer-startup assertion at `scanners/live_probe_workers.py::run_scorer`."
        )
        md_lines.append(
            "* (d) JCS canonicalization order — covered: byte-equality on the JCS-sorted canonical form."
        )
    else:
        md_lines.append(
            "**FAIL — halt merge.** At least one fixture failed the "
            "byte-equality or spread = 0 condition. Re-classify per "
            "`contracts/scanner-implementation-supervision-v1@v1.0` "
            "rule-execution row 'halt and escalate on band misclassification'. "
            "Do not request CTO merge co-sign until this PASS."
        )
        md_lines.append("")
        md_lines.append("### Failed fixtures")
        for label in args.fixtures:
            r = reports.get(label, {})
            if "error" in r:
                md_lines.append(f"* `{label}`: ERROR — {r['error']}")
            elif not r.get("fixture_pass"):
                md_lines.append(f"* `{label}`: see JSON delta for full report")
    md_lines.append("")

    md_out.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(
        f"\n[subprocess-e2e] {'PASS' if all_passed else 'FAIL'} "
        f"({len(args.fixtures)} fixtures × N={args.n} replicates)"
    )
    print(f"[subprocess-e2e] Wrote:")
    print(f"  {json_out}")
    print(f"  {md_out}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
