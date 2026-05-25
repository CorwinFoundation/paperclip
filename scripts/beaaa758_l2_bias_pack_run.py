"""BEAAA-758 — bias-pack v0.2 L2 live-judge runner (rebaseline P2.1 + P2.4 on goldens).

v0.1 (BEAAA-749) wired `harness/pipeline.py::run_bias_pack` as an additive,
opt-in BIAS_FAIRNESS pillar call and proved L1-replay zero-delta on the
released path. v0.2 closes the loop: actually fire `run_bias_pack` against
each live Path-A golden fixture, persist the rich BBQ + DoNotAnswer bench
rows, re-emit P2.1 + P2.4 measurements into per-fixture `eval_outputs.json`,
re-score, and re-baseline `artifacts/beaaa-387/manifest.json` under
`bias_pack_version: bias-pack-v0.1`.

Co-sign posture (per `contracts/scanner-implementation-supervision-v1.md@v1.0`):
  * Rule-execution UPGRADE to rule-change is the working assumption for
    v0.2 because the L2 rebaseline WILL move per-fixture ARE on at least
    some fixtures by design.
  * CTO co-sign required (3 open Qs in BEAAA-758 description).
  * UW (HoUW) upfront co-sign required.
  * CEO/board approval required for projected ~$180–$240 judge spend
    against the BEAAA-152 $100 hard cap.

This script's `--execute` path is fail-closed: it requires `--cto-cosign-ref`,
`--uw-cosign-ref`, and `--budget-approval-ref` flags pointing at the
acceptance evidence before any live judge call is made. `--dry-run` (default)
prints a per-fixture cost estimate without any model traffic.

Carrier Rule: no carrier names in prompts (inherited from `_bbq` + `_dna`).
Supreme Law: judge pinned (`claude-sonnet-4-6`, temperature 0, seed 42,
ensemble_n=3 — see `rubric/probe_mappings/{bbq,donotanswer}.yaml`).

Usage:
    # Default — print scope + cost estimate, no model calls.
    python3 scripts/beaaa758_l2_bias_pack_run.py --fixture all

    # Real run (post-approval) — all three refs must resolve.
    python3 scripts/beaaa758_l2_bias_pack_run.py \\
        --fixture all --execute --seed 0 \\
        --cto-cosign-ref BEAAA-758:<comment-id> \\
        --uw-cosign-ref BEAAA-758:<interaction-id> \\
        --budget-approval-ref <approval-uuid>

CTO co-sign (cmt 8441869f, 2026-05-25) requirements baked in:
  * `--execute` is strictly gated on all 3 refs (fail-closed).
  * `--seed` flag selects which seed of the N=5 candidate-side run
    this invocation owns (seeds 0..4 per CTO requirement 2).
  * Sibling archive `eval_outputs.bias-pack-v0.1-prior.json` MUST be
    written BEFORE the new `eval_outputs.json` overwrite for any
    fixture (single-write order matters per CTO requirement 4;
    aborted runs leave the fixture in a known consistent state).
  * Calibration delta renders to
    `tools/calibration-deltas/2026-05-26-beaaa758-bias-pack-v0.2-rebaseline.{md,json}`
    using the standing-table format (per CTO requirement 1).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Reuse the live-fixture machinery from path_a_baselines so the same
# adapter/runner/system_prompt logic stays in one place (wrap don't fork).
from scripts.path_a_baselines import (  # noqa: E402
    FIXTURE_CONFIG,
    FIXTURES,
    BASELINES,
    _build_runner,
    _system_prompt,
)
from harness.pipeline import run_bias_pack  # noqa: E402
from harness.runner import AnthropicRunner  # noqa: E402

# Per `rubric/probe_mappings/{bbq,donotanswer}.yaml@v0.1` — the v0.1 pin the
# CTO co-signed for BEAAA-749. Do NOT change without a rule-change co-sign.
JUDGE_MODEL_PIN = "claude-sonnet-4-6"
ENSEMBLE_N = 3

# Live fixtures that can carry the v0.2 L2 rebaseline today. B1 + M1 are
# scanner 0.2.3 deterministic replay (no live API); M2 is Path B (different
# runner); B2 is pending HoUW BEAAA-419 anchor decision. v0.2 ships rebaseline
# against the live Path-A v2.x fixtures only. The v1 / Path-B / pending
# fixtures are CTO design Q-3 (heterogeneity policy + path-B coverage).
L2_FIXTURES_PATH_A_V2 = ["G1", "G2", "G3", "M3", "B3"]
DEFERRED_FIXTURES_NOTE = {
    "B1": "deterministic_replay@scanner-0.2.3 — no live API; covered by CTO Q-3",
    "M1": "deterministic_replay@scanner-0.2.3 — no live API; covered by CTO Q-3",
    "M2": "path_b@scanner-0.2.3 — different runner shape; covered by CTO Q-3",
    "B2": "pending HoUW anchor decision BEAAA-419 — explicit exclusion in BEAAA-758",
}


def _estimate_cost(n_fixtures: int) -> dict[str, Any]:
    """Per-fixture: BBQ n=200 subject + DNA n=100 subject + DNA n=100 judge,
    each with ensemble_n=3. Rough $/call assumes pinned Sonnet-class pricing;
    actuals vary by fixture's subject vendor (G2 = gpt-4o, G3 = gemini, etc.)."""
    items_per_fixture = (200 + 100 + 100) * ENSEMBLE_N
    total_calls = items_per_fixture * n_fixtures
    # Bound estimate — issue body's $240 for 8 fixtures = ~$30/fixture upper.
    low = round(n_fixtures * 18.0, 2)
    high = round(n_fixtures * 30.0, 2)
    return {
        "fixtures": n_fixtures,
        "items_per_fixture": items_per_fixture,
        "total_calls": total_calls,
        "usd_low": low,
        "usd_high": high,
        "beaaa_152_cap_remaining_usd": 100.0,  # CEO/board confirm
    }


def _check_approvals(
    *, cto_cosign_ref: str | None,
    uw_cosign_ref: str | None,
    budget_approval_ref: str | None,
) -> None:
    """Fail-closed gate. All three refs must be non-empty strings or the
    execute path refuses to make any model call."""
    missing = [
        n for n, v in (
            ("--cto-cosign-ref", cto_cosign_ref),
            ("--uw-cosign-ref", uw_cosign_ref),
            ("--budget-approval-ref", budget_approval_ref),
        ) if not v
    ]
    if missing:
        raise SystemExit(
            "BEAAA-758 v0.2 L2 execute is fail-closed.\n"
            f"  Missing approval refs: {', '.join(missing)}\n"
            "  See BEAAA-758 description §Co-sign classification and §Open\n"
            "  questions. Default --dry-run prints cost estimate without any\n"
            "  model traffic.")


def dry_run(fixture_ids: list[str]) -> dict[str, Any]:
    runnable = [f for f in fixture_ids if f in L2_FIXTURES_PATH_A_V2]
    deferred = [f for f in fixture_ids if f in DEFERRED_FIXTURES_NOTE]
    out = {
        "ticket": "BEAAA-758",
        "scanner_version": "0.3.1",
        "rubric_version_target": "1.0.0",
        "bias_pack_version": "bias-pack-v0.1",
        "judge_pin": JUDGE_MODEL_PIN,
        "ensemble_n": ENSEMBLE_N,
        "runnable_fixtures": runnable,
        "deferred_fixtures": {f: DEFERRED_FIXTURES_NOTE[f] for f in deferred},
        "cost_estimate": _estimate_cost(len(runnable)),
        "co_sign_status": {
            "cto": "OPEN — 3 Qs in BEAAA-758 description",
            "uw": "OPEN — upfront co-sign required (rebaseline moves ARE by design)",
            "budget": "OPEN — projected spend exceeds BEAAA-152 $100 cap; board approval required",
        },
    }
    return out


def run_fixture(
    fixture_id: str,
    *,
    seed: int,
    cto_cosign_ref: str,
    uw_cosign_ref: str,
    budget_approval_ref: str,
) -> dict[str, Any]:
    """Live L2 bias-pack run for one Path-A v2 fixture at one candidate seed.

    Per CTO co-sign cmt 8441869f requirement 2: the candidate side is
    median(N=5 seeded runs), so call this for seed in 0..4 and aggregate
    the rich rows into a median row before persisting. Baseline (v0.1)
    side is a single deterministic replay — no N=5 there.

    Implementation will land in the follow-up heartbeat once the three
    approval refs above resolve. Skeleton is committed now so the next
    heartbeat is mechanical fill-in, not design re-derivation.
    """
    if fixture_id not in L2_FIXTURES_PATH_A_V2:
        raise SystemExit(f"BEAAA-758 v0.2 L2 does not cover {fixture_id} in this release; see CTO Q-3.")

    cfg = FIXTURE_CONFIG[fixture_id]
    agent_path = FIXTURES / fixture_id / "agent.json"
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    print(f"[{fixture_id}] adapter={cfg['adapter']} model={cfg['model']} "
          f"expected_bmi={agent.get('expected_bmi')} bias_pack_version=bias-pack-v0.1",
          file=sys.stderr, flush=True)

    system_prompt, _ = _system_prompt(cfg["system_prompt_kind"])
    subject_runner = _build_runner(cfg, system_prompt)
    judge_runner = AnthropicRunner(JUDGE_MODEL_PIN)

    measurements, details = run_bias_pack(
        subject_runner,
        judge_runner=judge_runner,
        requested_pillars={"BIAS_FAIRNESS"},
        ensemble_n=ENSEMBLE_N,
    )

    # ---- TODO post-approval (next heartbeat) ----
    # ORDER MATTERS — single-write rollback symmetry per CTO requirement 4:
    # 1. Write per-seed rich rows:
    #      artifacts/beaaa-387/{fid}/probes/bias-pack-bbq-v0.1-seed{seed}.json
    #      artifacts/beaaa-387/{fid}/probes/bias-pack-dna-v0.1-seed{seed}.json
    # 2. After all 5 seeds complete for this fixture, compute median rows.
    # 3. Write sibling archive `eval_outputs.bias-pack-v0.1-prior.json` BEFORE
    #    overwriting eval_outputs.json (CTO requirement 4 — atomic-ish rollback).
    # 4. Overwrite eval_outputs.json with median P2.1+P2.4 rows.
    # 5. Re-score the fixture and persist new baseline.json (bias-pack-aware).
    # 6. update_manifest(): per-fixture bias_pack_version + bumped corpus_version
    #    (additive: "bias-pack-v0.1+<existing>").
    # 7. Compute calibration delta vs v0.1 baseline to
    #    tools/calibration-deltas/2026-05-26-beaaa758-bias-pack-v0.2-rebaseline.{md,json}
    #    using CTO standing table (Agent|Δ total|Δ GOV|Δ ROB|Δ BIAS|Δ OPS|spread base|spread cand).
    # 8. Gate ±1 ARE total + ±0.5 per pillar; >3pt names FID + per-pillar deltas
    #    in Founder escalation packet (CTO A2 refinement).
    # 9. Re-check discrimination per ADR-0012 R3 (per-version, v2-only set —
    #    hard release-block gate per CTO A3 condition 3).
    # ----------------------------------------------
    return {
        "fixture_id": fixture_id,
        "seed": seed,
        "cto_cosign_ref": cto_cosign_ref,
        "uw_cosign_ref": uw_cosign_ref,
        "budget_approval_ref": budget_approval_ref,
        "judge_model_pinned": JUDGE_MODEL_PIN,
        "ensemble_n": ENSEMBLE_N,
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_measurements": len(measurements),
        "details_keys": sorted(details.keys()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", default="all",
                    choices=L2_FIXTURES_PATH_A_V2 + list(DEFERRED_FIXTURES_NOTE.keys()) + ["all"],
                    help="Path-A v2 fixture id, or 'all'")
    ap.add_argument("--execute", action="store_true",
                    help="Run live judge calls (requires --cto-cosign-ref, --uw-cosign-ref, --budget-approval-ref)")
    ap.add_argument("--seed", type=int, default=0, choices=[0, 1, 2, 3, 4],
                    help="Candidate-side seed (CTO requirement 2: N=5 median over seeds 0..4)")
    ap.add_argument("--cto-cosign-ref", default=os.environ.get("BEAAA_758_CTO_REF"))
    ap.add_argument("--uw-cosign-ref", default=os.environ.get("BEAAA_758_UW_REF"))
    ap.add_argument("--budget-approval-ref", default=os.environ.get("BEAAA_758_BUDGET_REF"))
    args = ap.parse_args()

    targets = L2_FIXTURES_PATH_A_V2 if args.fixture == "all" else [args.fixture]

    if not args.execute:
        print(json.dumps(dry_run(targets), indent=2, sort_keys=True))
        return

    _check_approvals(
        cto_cosign_ref=args.cto_cosign_ref,
        uw_cosign_ref=args.uw_cosign_ref,
        budget_approval_ref=args.budget_approval_ref,
    )
    results = [
        run_fixture(
            fid,
            seed=args.seed,
            cto_cosign_ref=args.cto_cosign_ref,
            uw_cosign_ref=args.uw_cosign_ref,
            budget_approval_ref=args.budget_approval_ref,
        )
        for fid in targets if fid in L2_FIXTURES_PATH_A_V2
    ]
    print(json.dumps({"ticket": "BEAAA-758", "seed": args.seed, "results": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
