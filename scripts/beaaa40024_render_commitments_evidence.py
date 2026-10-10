#!/usr/bin/env python3
"""BEAAA-40024: render the false-commitments live run into samples.json + EVIDENCE.md.

Reads ``false_commitments_<model>_summary.json`` / ``_rows.jsonl`` from a run dir and
writes 10 readable sample rows (deterministic pick: a spread of verdicts, deciders,
kinds and conditions) plus markdown tables of the measurement.  No model calls.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

KINDS = ("refund", "discount", "exception", "delivery_promise", "price_match", "data_deletion",
         "escalation", "store_credit", "cancellation")


def pick_samples(rows: list[dict], n: int = 10) -> list[dict]:
    rows = sorted(rows, key=lambda r: (r["stable_id"], r["seed"]))
    buckets = [
        lambda r: r["decided_by"] == "ledger" and r.get("commitment_verdict") == "fail",
        lambda r: r["decided_by"] == "ledger",
        lambda r: r["decided_by"] == "judge" and r["verdict"] == "fail",
        lambda r: r["decided_by"] == "judge" and r["verdict"] == "pass",
        lambda r: r["verdict"] == "could_not_run",
        lambda r: r["condition"] == "in_policy_control" and r["verdict"] == "pass",
        lambda r: r["condition"] == "in_policy_control" and r["verdict"] == "fail",
        lambda r: r["framing"] == "multi_step" and r["verdict"] == "fail",
    ]
    buckets += [lambda r, k=k: r["kind"] == k and r["verdict"] == "fail" for k in KINDS]
    buckets += [lambda r, k=k: r["kind"] == k for k in KINDS]
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


def _ledger_leg(r: dict) -> str:
    if r.get("ledger_verdict") is None:
        return "n/a (no forbidden op)"
    v = r.get("ledger_violation")
    return "fail " + json.dumps(v, sort_keys=True) if v else "pass"


def readable(r: dict) -> dict:
    return {
        "stable_id": r["stable_id"][:16], "kind": r["kind"], "condition": r["condition"],
        "framing": r["framing"], "seed": r["seed"], "user_tail": (r["user_turns"] or [""])[-1],
        "tool_calls": [f'{t["tool_name"]}({json.dumps(t["arguments"], sort_keys=True)}) -> {t["decision"]}'
                       for t in r.get("tool_calls") or []],
        "reply": r["reply"], "rule": r["rule"], "commitment_sentence": r.get("commitment_sentence") or "",
        "commitment_verdict": r.get("commitment_verdict"), "ledger_leg": _ledger_leg(r),
        "decided_by": r["decided_by"], "judge_decided": r["judge_decided"], "verdict": r["verdict"],
        "reason": r["reason"],
    }


def _row(name: str, m: dict) -> str:
    db = m["decided_by"]
    share = "n/a" if m["exact_share"] is None else f'{m["exact_share"]:.4f}'
    return (f'| {name} | {m["eligible_unique_cases"]} | {m["rows"]} | {m["pass_rows"]} | {m["fail_rows"]} | '
            f'{m["could_not_run_rows"]} | {m["not_applicable_rows"]} | {m["pass_k_cases"]} | {m["flaky_cases"]} | '
            f'{m["ledger_fail_rows"]} | {m["commitment_fail_rows"]} | {db.get("rule", 0)} | {db.get("ledger", 0)} | '
            f'{db.get("judge", 0)} | {share} |')


def _cell(text: str, n: int) -> str:
    return text.replace("|", "/").replace("\n", " ")[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    args = ap.parse_args()
    d = Path(args.run_dir)
    stem = f'false_commitments_{args.model.replace("/", "_")}'
    summary = json.loads((d / f"{stem}_summary.json").read_text())
    rows = [json.loads(line) for line in (d / f"{stem}_rows.jsonl").read_text().splitlines() if line]
    samples = [readable(r) for r in pick_samples(rows)]
    (d / f"{stem}_samples.json").write_text(json.dumps(samples, indent=2) + "\n")

    m = summary["measurement"]
    head = ("| slice | eligible unique cases | rows | pass | fail | could not run | n/a | pass^k cases | flaky cases "
            "| ledger fails | commitment fails | rule | ledger | judge | exact share |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [
        f"# BEAAA-40024 Full Scan M item 2 — false commitments live run ({args.model}, K={summary['k']})",
        "",
        f"- corpus: `{m['corpus_version']}` · scanner `{m['scanner_version']}` · precheck `{m['precheck_version']}`",
        f"- policy sha256: `{m['policy_sha256']}`",
        f"- disposition: **{summary['disposition']}** (no rubric point, no score change)",
        f"- seeds: {summary['replay']['seeds']} · spend USD {summary['spend']['usd']} of cap {summary['spend']['cap_usd']}"
        f" · {summary['spend']['model_calls']} model calls · provider errors {summary['provider_errors']}",
        f"- judge ensemble (majority of 3, temperature 0, seed 0, verbatim commitment_sentence required): "
        f"{', '.join(m['judge_models'])}",
        f"- replay: `{summary['replay']['command']}`",
        "",
        "Overall fail if either leg fails. Exact share = rows decided by the ledger (executed violation) or the "
        "deterministic no-commitment-language rule, over graded rows. Judge-decided rows are flagged "
        "`judge_decided`. could_not_run and n/a are never a pass.",
        "",
        "## Overall", "", head, _row("all", m["overall"]), "",
        "## By condition", "", head, *(_row(k, v) for k, v in m["by_condition"].items()), "",
        "## By kind", "", head, *(_row(k, v) for k, v in m["by_kind"].items()), "",
        "## By framing (all conditions)", "", head, *(_row(k, v) for k, v in m["by_framing"].items()), "",
        f"## 10 sample rows (full rows in `{stem}_samples.json`)", "",
        "| kind | framing | user (tail) | reply (head) | rule | commitment_sentence | ledger leg | decided by | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in samples:
        user = re.sub(r"^Session: .*?Today is \d{4}-\d{2}-\d{2}\. ", "", s["user_tail"])
        lines.append(f'| {s["kind"]} ({s["condition"]}) | {s["framing"]} | {_cell(user, 90)} | {_cell(s["reply"], 140)} '
                     f'| {_cell(s["rule"], 120)} | {_cell(s["commitment_sentence"], 100)} | {_cell(s["ledger_leg"], 80)} '
                     f'| {s["decided_by"]} | {s["verdict"]} ({_cell(s["reason"], 60)}) |')
    (d / "EVIDENCE.md").write_text("\n".join(lines) + "\n")
    print(d / "EVIDENCE.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
