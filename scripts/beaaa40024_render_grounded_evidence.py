#!/usr/bin/env python3
"""BEAAA-40024: render the grounded-claims live run into samples.json + EVIDENCE.md.

Reads ``grounded_claims_<model>_summary.json`` / ``_rows.jsonl`` from a run dir and writes
10 readable sample rows (deterministic pick: a spread of verdicts, conditions and deciders)
plus a markdown table of the measurement.  No model calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _pick_samples(rows: list[dict], n: int = 10) -> list[dict]:
    rows = sorted(rows, key=lambda r: (r["stable_id"], r["seed"]))
    buckets = [
        lambda r: r["judge_decided"],
        lambda r: r["verdict"] == "could_not_run",
        lambda r: r["decided_by"] == "model" and r["verdict"] == "fail",
        lambda r: r["decided_by"] == "model" and r["verdict"] == "pass",
    ]
    for cond in ("owned", "missing", "other_customer", "ambiguous"):
        buckets.append(lambda r, c=cond: r["condition"] == c and r["verdict"] == "fail")
        buckets.append(lambda r, c=cond: r["condition"] == c and r["verdict"] == "pass")
    picked: list[dict] = []
    seen: set[str] = set()
    for match in buckets:
        for r in rows:
            if r["stable_id"] not in seen and match(r):
                picked.append(r)
                seen.add(r["stable_id"])
                break
        if len(picked) == n:
            break
    return picked


def _readable(r: dict) -> dict:
    return {
        "stable_id": r["stable_id"][:16],
        "topic": r["topic"],
        "condition": r["condition"],
        "seed": r["seed"],
        "user": r["user_turns"][-1],
        "tool_calls": [f'{t["tool_name"]}({json.dumps(t["arguments"], sort_keys=True)})' for t in r["tool_calls"]],
        "reply": r["reply"],
        "truth_values": r["truth_values"],
        "claim_values": r["claim_values"],
        "decided_by": r["decided_by"],
        "judge_decided": r["judge_decided"],
        "verdict": r["verdict"],
        "reason": r["reason"],
    }


def _row(name: str, m: dict) -> str:
    db = m["decided_by"]
    return (f'| {name} | {m["eligible_unique_cases"]} | {m["rows"]} | {m["pass_rows"]} | {m["fail_rows"]} | '
            f'{m["could_not_run_rows"]} | {m["pass_k_cases"]} | {m["flaky_cases"]} | '
            f'{db.get("regex", 0)} | {db.get("model", 0)} | {db.get("judge", 0)} | {m["exact_share"]:.4f} |')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    args = ap.parse_args()
    d = Path(args.run_dir)
    stem = f'grounded_claims_{args.model.replace("/", "_")}'
    summary = json.loads((d / f"{stem}_summary.json").read_text())
    rows = [json.loads(line) for line in (d / f"{stem}_rows.jsonl").read_text().splitlines() if line]
    samples = [_readable(r) for r in _pick_samples(rows)]
    (d / f"{stem}_samples.json").write_text(json.dumps(samples, indent=2) + "\n")

    m = summary["measurement"]
    head = ("| slice | eligible unique cases | rows | pass | fail | could not run | pass^k cases | flaky cases "
            "| regex | model | judge | exact share |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [
        f"# BEAAA-40024 Full Scan M item 1 — grounded claims live run ({args.model}, K={summary['k']})",
        "",
        f"- corpus: `{m['corpus_version']}` · scanner `{m['scanner_version']}` · extractor `{m['extractor_version']}`",
        f"- disposition: **{summary['disposition']}** (no rubric point, no score change)",
        f"- seeds: {summary['replay']['seeds']} · spend USD {summary['spend']['usd']} of cap {summary['spend']['cap_usd']}"
        f" · {summary['spend']['model_calls']} model calls · provider errors {summary['provider_errors']}",
        f"- extraction fallback model: `{m['model_extraction_model']}` · prompt sha256 `{summary['pins']['extraction_prompt_sha256']}`",
        f"- judge ensemble (majority of 3, temperature 0, seed 0): {', '.join(m['judge_models'])}",
        f"- replay: `{summary['replay']['command']}`",
        "",
        "Exact share = rows decided by regex or the pinned structured extractor, over graded rows. "
        "Judge-decided rows are flagged `judge_decided`. could_not_run is never a pass.",
        "",
        "## Overall", "", head, _row("all", m["overall"]), "",
        "## By condition", "", head, *(_row(k, v) for k, v in m["by_condition"].items()), "",
        "## By topic", "", head, *(_row(k, v) for k, v in m["by_topic"].items()), "",
        f"## 10 sample rows (full rows in `{stem}_samples.json`)", "",
        "| topic | condition | seed | user (tail) | reply (head) | truth | claimed | decided by | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in samples:
        user = s["user"].split(". ", 2)[-1][:90].replace("|", "/")
        reply = s["reply"][:140].replace("|", "/").replace("\n", " ")
        lines.append(f'| {s["topic"]} | {s["condition"]} | {s["seed"]} | {user} | {reply} | {s["truth_values"]} '
                     f'| {s["claim_values"]} | {s["decided_by"]} | {s["verdict"]} ({s["reason"]}) |')
    (d / "EVIDENCE.md").write_text("\n".join(lines) + "\n")
    print(d / "EVIDENCE.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
