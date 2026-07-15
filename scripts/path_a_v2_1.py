"""Path A v2.1 — OpenRouter base-model category-proxy scan slate (BEAAA-68).

Runs the ARE Scanner against the 4 v2.1 category-proxy targets, all via
OpenRouter for label-discipline consistency:

  - meta-llama/llama-3.3-70b-instruct       (open-weights category)
  - mistralai/mistral-large-2411            (European frontier category)
  - openai/gpt-4o                           (closed frontier category — base via OpenRouter)
  - anthropic/claude-3.5-sonnet             (closed frontier category — base via OpenRouter)

Provenance label on every row in every surface:
  `category-proxy via OpenRouter — base-model capability calibration, not deployment scan`

Usage:
    python3 scripts/path_a_v2_1.py
    python3 scripts/path_a_v2_1.py --runs-dir /tmp/path_a_v2_1
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


from harness.bench import indirect_injection, prompt_injection, secret_exfil, truthfulqa  # noqa: E402
from harness.pipeline import load_priors, merge_measurements_with_priors  # noqa: E402
from harness.runner import OpenRouterRunner, RunnerError  # noqa: E402
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
    (REPO / "harness" / "fixtures" / "indirect_injection_poc.jsonl",
     indirect_injection.load_items, indirect_injection.run, "indirect-injection-poc", "P3.2"),
    (REPO / "harness" / "fixtures" / "secret_exfil_poc.jsonl",
     secret_exfil.load_items, secret_exfil.run, "secret-exfil-poc", "P3.3"),
]

# v2.1 Path A slate — all via OpenRouter, all category-proxy labeled.
TARGETS = [
    ("meta-llama/llama-3.3-70b-instruct", "huggingchat",      "Llama 3.3 70B (Meta)"),
    ("mistralai/mistral-large-2411",      "mistral_le_chat",  "Mistral Large 2411"),
    ("openai/gpt-4o",                     "openai_chatgpt",   "GPT-4o (base via OpenRouter)"),
    # Note: anthropic/claude-3.5-sonnet was retired on OpenRouter (only
    # claude-3.5-haiku remains in the 3.5 family). Substituting Claude 3.7
    # Sonnet — closest available Sonnet-tier base in the Claude 3.x family.
    ("anthropic/claude-3.7-sonnet",       "anthropic_claude", "Claude 3.7 Sonnet (base via OpenRouter — Claude 3.5 retired)"),
]

PROVENANCE = "category-proxy via OpenRouter — base-model capability calibration, not deployment scan"


def _run_one(model: str, prior_id: str, label: str,
             rubric: Rubric, rates: Rates, customer: dict,
             scored_at: datetime, runs_dir: Path) -> dict:
    print(f"  running {label} (openrouter:{model})...", file=sys.stderr, flush=True)
    runner = OpenRouterRunner(model=model)
    measurements = []
    for fixture, loader, run_fn, _bench_name, _dim in BENCHES:
        items = loader(fixture)
        measurement = run_fn(runner, items)
        measurements.append(measurement)
    prior_path = PRIORS_DIR / f"{prior_id}.json"
    _, prior_scores = load_priors(prior_path)
    raw_scores, _ = merge_measurements_with_priors(measurements, prior_scores)
    eval_outputs = {
        d: {"raw": r, "evidence_uri": f"https://leaderboard.beaai.local/{d}"}
        for d, r in raw_scores.items()
    }
    result = score(
        rubric, eval_outputs,
        snapshot_hash="sha256:path-a-v2-1-not-a-real-snapshot",
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
        "provenance": PROVENANCE,
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
    _load_dotenv(REPO / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-revenue", type=int, default=100_000_000)
    parser.add_argument("--runs-dir", default="/tmp/path_a_v2_1")
    args = parser.parse_args()

    rubric = Rubric.load(RUBRIC_PATH)
    rates = Rates.load(RATES_PATH)
    customer = {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": args.customer_revenue,
        "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
    }
    scored_at = datetime(2026, 4, 30, tzinfo=timezone.utc)
    runs_dir = Path(args.runs_dir)

    results: list[dict] = []
    for model, prior_id, label in TARGETS:
        try:
            r = _run_one(model, prior_id, label, rubric, rates, customer, scored_at, runs_dir)
            results.append(r)
        except Exception as exc:
            print(f"  [skip] {label}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            results.append({"label": label, "runner": f"openrouter:{model}",
                            "skipped": True, "error": str(exc)[:300]})

    results.sort(key=lambda r: -r.get("bmi", -1))

    print("# Path A v2.1 — OpenRouter category-proxy leaderboard (BEAAA-68)")
    print()
    print(f"_Provenance: **{PROVENANCE}**_")
    print()
    print(f"_Customer profile: ENT_SAAS, ${args.customer_revenue:,} annual revenue, $1M/$2M limits._")
    print(f"_Measured: P1.1 (TruthfulQA-MC1-POC), P3.1 (prompt-injection-POC), P3.2 (indirect-injection-POC), P3.3 (secret-exfil-POC). All other dims = priors._")
    print()
    print("| # | Model | TQA | Direct Inj | Indirect Inj | Exfil | ARE | Band | Premium |")
    print("|--:|-------|----:|-----------:|-------------:|------:|----:|:----:|:--------|")
    for i, r in enumerate([x for x in results if not x.get("skipped")], 1):
        ms = {m["dim_id"]: m for m in r["measurements"]}
        tqa = f"{ms['P1.1']['n_correct']}/{ms['P1.1']['n_items']}" if "P1.1" in ms else "-"
        inj = f"{ms['P3.1']['n_resisted']}/{ms['P3.1']['n_items']}" if "P3.1" in ms else "-"
        iinj = f"{ms['P3.2']['n_resisted']}/{ms['P3.2']['n_items']}" if "P3.2" in ms else "-"
        exf = f"{ms['P3.3']['n_resisted']}/{ms['P3.3']['n_items']}" if "P3.3" in ms else "-"
        print(f"| {i} | **{r['label']}** | {tqa} | {inj} | {iinj} | {exf} | **{r['bmi']}** | **{r['band']}** | {r['premium']} |")
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
