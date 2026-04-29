"""Measured BMI leaderboard — run the harness against multiple frontier models
and emit a single ranked Markdown table.

Each target is a (runner, model, prior_id, label) tuple. The harness is the
same loop as scripts/poc_real_score.py — score(), 12-band assignment,
quote() against a representative customer profile.

Usage:
    python3 scripts/measured_leaderboard.py
    python3 scripts/measured_leaderboard.py --customer-revenue 1000000000
    python3 scripts/measured_leaderboard.py --runs-dir /tmp/leaderboard_runs
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
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip("'").strip('"')


_load_dotenv(REPO / ".env")

from harness.bench import prompt_injection, secret_exfil, truthfulqa  # noqa: E402
from harness.pipeline import load_priors, merge_measurements_with_priors  # noqa: E402
from harness.runner import (  # noqa: E402
    AnthropicRunner,
    GeminiRunner,
    OpenAIRunner,
    RunnerError,
)
from pricing.quote import ManualReferral, Rates, quote  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

PRIORS_DIR = REPO / "scripts" / "agent_priors"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"

BENCHES = [
    (REPO / "harness" / "fixtures" / "truthfulqa_mc1_sample.jsonl",
     truthfulqa.load_items, truthfulqa.run, "truthfulqa-mc1-poc", "P1.1"),
    (REPO / "harness" / "fixtures" / "prompt_injection_poc.jsonl",
     prompt_injection.load_items, prompt_injection.run, "prompt-injection-poc", "P3.1"),
    (REPO / "harness" / "fixtures" / "secret_exfil_poc.jsonl",
     secret_exfil.load_items, secret_exfil.run, "secret-exfil-poc", "P3.3"),
]

# 9 frontier targets across the 3 SDKs we integrate today.
TARGETS = [
    ("anthropic", "claude-opus-4-7",            "anthropic_claude", "Claude Opus 4.7"),
    ("anthropic", "claude-sonnet-4-6",          "anthropic_claude", "Claude Sonnet 4.6"),
    ("anthropic", "claude-haiku-4-5-20251001",  "anthropic_claude", "Claude Haiku 4.5"),
    ("openai",    "gpt-4o",                     "openai_chatgpt",   "GPT-4o"),
    ("openai",    "gpt-4o-mini",                "openai_chatgpt",   "GPT-4o Mini"),
    ("openai",    "gpt-4-turbo",                "openai_chatgpt",   "GPT-4 Turbo"),
    ("gemini",    "gemini-2.5-pro",             "google_gemini",    "Gemini 2.5 Pro"),
    ("gemini",    "gemini-2.5-flash",           "google_gemini",    "Gemini 2.5 Flash"),
    ("gemini",    "gemini-2.5-flash-lite",      "google_gemini",    "Gemini 2.5 Flash-Lite"),
]


def _build_runner(name: str, model: str):
    if name == "anthropic":
        return AnthropicRunner(model=model)
    if name == "openai":
        return OpenAIRunner(model=model)
    if name == "gemini":
        return GeminiRunner(model=model, min_interval_s=0.0)
    raise RunnerError(f"unknown runner {name!r}")


def _run_one(runner_name: str, model: str, prior_id: str, label: str,
             rubric: Rubric, rates: Rates, customer: dict, scored_at: datetime,
             runs_dir: Path) -> dict:
    print(f"  running {label} ({runner_name}:{model})...", file=sys.stderr, flush=True)
    runner = _build_runner(runner_name, model)
    measurements = []
    for fixture, loader, run_fn, _bench_name, _dim in BENCHES:
        items = loader(fixture)
        measurement = run_fn(runner, items)
        measurements.append(measurement)
    prior_path = PRIORS_DIR / f"{prior_id}.json"
    _, prior_scores = load_priors(prior_path)
    raw_scores, provenance = merge_measurements_with_priors(measurements, prior_scores)
    eval_outputs = {
        d: {"raw": r, "evidence_uri": f"https://leaderboard.beaai.local/{d}"}
        for d, r in raw_scores.items()
    }
    result = score(
        rubric, eval_outputs,
        snapshot_hash="sha256:leaderboard-not-a-real-snapshot",
        forensic_bundle_uri="https://leaderboard.beaai.local/no-bundle",
        scored_at=scored_at,
    )
    try:
        q = quote(result, customer, rates=rates)
        annual = q["annual_premium_usd"]
        total = q["total_with_endorsements_usd"]
        premium_str = f"${annual:,}" if annual == total else f"${annual:,} (+${total - annual:,} = ${total:,})"
        status = "indicative"
    except ManualReferral as exc:
        premium_str = f"manual referral — {exc.reason[:60]}"
        status = "manual_referral"
    record = {
        "label": label,
        "runner": runner.model_id(),
        "prior_id": prior_id,
        "bmi": result["bmi"],
        "band": result["band"]["display"],
        "posture": result["band"]["posture"],
        "premium": premium_str,
        "status": status,
        "measurements": [
            {"dim_id": m["dim_id"], "raw": m["raw"], **{k: m[k] for k in m if k.startswith("n_") or k.endswith("_rate")}}
            for m in measurements
        ],
    }
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{runner.model_id().replace(':', '_').replace('/', '_')}.json").write_text(
        json.dumps(record, indent=2)
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-revenue", type=int, default=100_000_000)
    parser.add_argument("--runs-dir", default="/tmp/measured_leaderboard")
    args = parser.parse_args()

    rubric = Rubric.load(RUBRIC_PATH)
    rates = Rates.load(RATES_PATH)
    customer = {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": args.customer_revenue,
        "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
    }
    scored_at = datetime(2026, 4, 29, tzinfo=timezone.utc)
    runs_dir = Path(args.runs_dir)

    results: list[dict] = []
    for runner_name, model, prior_id, label in TARGETS:
        try:
            r = _run_one(runner_name, model, prior_id, label,
                         rubric, rates, customer, scored_at, runs_dir)
            results.append(r)
        except Exception as exc:
            print(f"  [skip] {label}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            results.append({"label": label, "runner": f"{runner_name}:{model}",
                            "skipped": True, "error": str(exc)[:200]})

    results.sort(key=lambda r: -r.get("bmi", -1))

    print("# Measured BMI leaderboard — frontier models")
    print()
    print(f"_Customer profile: ENT_SAAS, ${args.customer_revenue:,} annual revenue, $1M/$2M limits._")
    print(f"_Measured: P1.1 (TruthfulQA-MC1-POC), P3.1 (prompt-injection-POC), P3.3 (secret-exfil-POC). All other dims = priors._")
    print()
    print("| # | Model | TQA | Inject | Exfil | BMI | Band | Premium |")
    print("|--:|-------|----:|------:|-----:|----:|:----:|:--------|")
    for i, r in enumerate([x for x in results if not x.get("skipped")], 1):
        ms = {m["dim_id"]: m for m in r["measurements"]}
        tqa = f"{ms['P1.1']['n_correct']}/{ms['P1.1']['n_items']}" if "P1.1" in ms else "-"
        inj = f"{ms['P3.1']['n_resisted']}/{ms['P3.1']['n_items']}" if "P3.1" in ms else "-"
        exf = f"{ms['P3.3']['n_resisted']}/{ms['P3.3']['n_items']}" if "P3.3" in ms else "-"
        print(f"| {i} | **{r['label']}** | {tqa} | {inj} | {exf} | **{r['bmi']}** | **{r['band']}** | {r['premium']} |")
    print()
    skipped = [x for x in results if x.get("skipped")]
    if skipped:
        print("## Skipped")
        for s in skipped:
            print(f"- {s['label']} (`{s['runner']}`): {s['error']}")
        print()
    print(f"_Run artifacts: {runs_dir}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
