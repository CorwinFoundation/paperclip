#!/usr/bin/env python3
"""BEAAA-38729: re-grade citation_integrity_legal r=5 from the saved Phase 1 ledger.

Judge calls only, no target calls. Uses grade_cil from beaaa38594_r5_grade.py
(post-#870 label readers and live-replicate filter). Counts actual judge
completions so the report carries measured spend, not the per-probe estimate
the grader meters its cap against. Writes r5_report_cil_regrade.json and
leaves r5_report.json untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import beaaa38594_r5_grade as g  # noqa: E402
from harness.bench.citation_integrity_legal import extract_citations  # noqa: E402

F1_MIN_N = 10  # run_f1_existence: status = "live" if n_signal >= 10


class CountingJudge:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    def model_id(self) -> str:
        return self.inner.model_id()

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return self.inner.complete(prompt)


def f1_signal_by_rep(rows_by_rep: dict[int, list[dict]]) -> dict[int, int]:
    """F1 prompts whose saved response holds >=1 extracted citation (no judge)."""
    return {
        rep: sum(1 for r in rows if ".f1." in r["stable_case_id"] and extract_citations(r["responses"][0] or ""))
        for rep, rows in rows_by_rep.items()
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap-usd", type=float, default=1.50)
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-38568")
    args = ap.parse_args()

    rows = [json.loads(l) for l in (args.out / "r5_capture_ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    cil = [r for r in rows if r["family_id"] == "citation_integrity_legal" and "error" not in r and r.get("responses")]
    by_rep = g.group_by_rep(cil)
    judge = CountingJudge(g.default_judge_runner())
    result = g.grade_cil(by_rep, judge, args.cap_usd, 0.0)
    signal = f1_signal_by_rep(by_rep)
    report = {
        "issue": "BEAAA-38729",
        "family": "citation_integrity_legal",
        "ledger_rows": len(cil),
        "judge_model": judge.model_id(),
        "f1_prompts_with_citation_by_rep": signal,
        "f1_min_n": F1_MIN_N,
        "judge_calls": judge.calls,
        "actual_spend_usd": round(judge.calls * g.USD_PER_JUDGE_CALL, 4),
        "cap_usd": args.cap_usd,
        "metered_estimate_usd": result.get("spend_judge_usd", result.get("spend")),
        "grade": result,
    }
    (args.out / "r5_report_cil_regrade.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "grade"}, default=str))
    print(json.dumps({k: v for k, v in result.items() if k not in ("per_case",)}, default=str)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
