"""POC: run a real benchmark (TruthfulQA-MC1 sample) against a model, then
combine with priors → BMI for that agent.

Default mode is `--runner=mock` — deterministic, offline, demonstrates the
end-to-end loop. Real runs:

    ANTHROPIC_API_KEY=... python3 scripts/poc_real_score.py \\
        --runner=anthropic --model=claude-sonnet-4-6 --prior=anthropic_claude

    OPENAI_API_KEY=... python3 scripts/poc_real_score.py \\
        --runner=openai --model=gpt-4o --prior=openai_chatgpt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader; gitignored .env at repo root only.
    Last value wins for a given key — matches dotenv semantics + tolerates
    stale entries left at the top of the file by paste mistakes."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ[key] = value


_load_dotenv(REPO / ".env")

from harness.bench import prompt_injection, truthfulqa  # noqa: E402
from harness.pipeline import load_priors, merge_measurements_with_priors  # noqa: E402
from harness.runner import (  # noqa: E402
    AnthropicRunner,
    GeminiRunner,
    MockRunner,
    OpenAIRunner,
    RunnerError,
)
from pricing.quote import ManualReferral, Rates, quote  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

PRIORS_DIR = REPO / "scripts" / "agent_priors"
TQA_FIXTURE = REPO / "harness" / "fixtures" / "truthfulqa_mc1_sample.jsonl"
PI_FIXTURE = REPO / "harness" / "fixtures" / "prompt_injection_poc.jsonl"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"

BENCHES = {
    "truthfulqa": (TQA_FIXTURE, truthfulqa.load_items, truthfulqa.run, "P1.1"),
    "prompt_injection": (PI_FIXTURE, prompt_injection.load_items, prompt_injection.run, "P3.1"),
}


def _build_runner(name: str, model: str, prior_id: str, *, throttle_s: float | None):
    if name == "mock":
        # Mock returns "A" — every TQA-MC1 sample has answer "A", so this
        # demonstrates a perfect-accuracy run end-to-end.
        return MockRunner(name=f"perfect-{prior_id}", responder=lambda _p: "A")
    if name == "mock-guess":
        # Always answers "B" — demonstrates a low-accuracy run.
        return MockRunner(name=f"guess-{prior_id}", responder=lambda _p: "B")
    if name == "anthropic":
        return AnthropicRunner(model=model)
    if name == "openai":
        return OpenAIRunner(model=model)
    if name == "gemini":
        kwargs = {} if throttle_s is None else {"min_interval_s": throttle_s}
        return GeminiRunner(model=model, **kwargs)
    raise RunnerError(f"unknown runner {name!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", default="mock",
                        choices=["mock", "mock-guess", "anthropic", "openai", "gemini"])
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Model id (used by anthropic/openai/gemini runners).")
    parser.add_argument("--prior", default="anthropic_claude",
                        help="Prior fixture id (file stem in scripts/agent_priors/).")
    parser.add_argument("--customer-revenue", type=int, default=100_000_000)
    parser.add_argument("--throttle-s", type=float, default=None,
                        help="Override per-call min interval (gemini only).")
    parser.add_argument("--progress-jsonl", default=None,
                        help="If set, append per-call results to this file as we go.")
    parser.add_argument("--benches", default="truthfulqa",
                        help="Comma-separated benchmark names. Options: " + ", ".join(BENCHES))
    args = parser.parse_args()

    prior_path = PRIORS_DIR / f"{args.prior}.json"
    if not prior_path.exists():
        print(f"prior fixture not found: {prior_path}", file=sys.stderr)
        return 2
    display_name, prior_scores = load_priors(prior_path)

    runner = _build_runner(args.runner, args.model, args.prior, throttle_s=args.throttle_s)

    progress_path = Path(args.progress_jsonl) if args.progress_jsonl else None
    if progress_path:
        progress_path.write_text("")  # truncate

    bench_names = [b.strip() for b in args.benches.split(",") if b.strip()]
    for b in bench_names:
        if b not in BENCHES:
            print(f"unknown bench {b!r}; options: {list(BENCHES)}", file=sys.stderr)
            return 2

    measurements = []
    for bench_name in bench_names:
        fixture_path, loader, run_fn, _dim = BENCHES[bench_name]
        items = loader(fixture_path)

        def _on_progress(idx: int, total: int, item_result: dict, _bench=bench_name) -> None:
            # Stream to stderr so stdout stays a single clean JSON document.
            outcome = item_result.get("correct", item_result.get("resisted"))
            mark = "✓" if outcome else "✗"
            extra = (
                f"chose={item_result['chosen']} expected={item_result['expected']}"
                if "chosen" in item_result
                else f"category={item_result['category']}"
            )
            print(
                f"  [{_bench}][{idx:>2}/{total}] {item_result['id']} {mark} {extra}",
                file=sys.stderr,
                flush=True,
            )
            if progress_path:
                with progress_path.open("a") as f:
                    f.write(json.dumps({"bench": _bench, "idx": idx, "total": total, **item_result}) + "\n")

        print(f"Running {bench_name} ({len(items)} items) against {runner.model_id()}...",
              file=sys.stderr, flush=True)
        measurement = run_fn(runner, items, on_progress=_on_progress)
        measurements.append(measurement)

    raw_scores, provenance = merge_measurements_with_priors(measurements, prior_scores)

    rubric = Rubric.load(RUBRIC_PATH)
    eval_outputs = {
        dim_id: {"raw": raw, "evidence_uri": f"https://poc.beaai.local/{dim_id}"}
        for dim_id, raw in raw_scores.items()
    }
    result = score(
        rubric, eval_outputs,
        snapshot_hash="sha256:poc-not-a-real-snapshot",
        forensic_bundle_uri="https://poc.beaai.local/no-bundle",
        scored_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
    )

    rates = Rates.load(RATES_PATH)
    customer = {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": args.customer_revenue,
        "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
    }
    try:
        q = quote(result, customer, rates=rates)
        premium_str = (
            f"${q['annual_premium_usd']:,}"
            if q["annual_premium_usd"] == q["total_with_endorsements_usd"]
            else f"${q['annual_premium_usd']:,} (+${q['total_with_endorsements_usd'] - q['annual_premium_usd']:,} endorsements = ${q['total_with_endorsements_usd']:,})"
        )
    except ManualReferral as exc:
        premium_str = f"manual referral — {exc.reason}"

    n_measured = sum(1 for v in provenance.values() if v.startswith("measured:"))
    n_priors = sum(1 for v in provenance.values() if v == "prior")

    out = {
        "agent": display_name,
        "runner": runner.model_id(),
        "bmi": result["bmi"],
        "band": result["band"]["display"],
        "posture": result["band"]["posture"],
        "premium": premium_str,
        "measurements": [
            {
                "benchmark": m["benchmark"],
                "dim_id": m["dim_id"],
                "n_items": m["n_items"],
                "score_summary": {
                    k: m[k] for k in ("n_correct", "accuracy", "n_resisted", "resistance_rate")
                    if k in m
                },
                "measured_raw": m["raw"],
                "prior_raw_for_same_dim": prior_scores.get(m["dim_id"]),
            }
            for m in measurements
        ],
        "provenance_summary": {
            "n_measured_dims": n_measured,
            "n_prior_dims": n_priors,
            "measured_dims": [d for d, p in provenance.items() if p.startswith("measured:")],
        },
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
