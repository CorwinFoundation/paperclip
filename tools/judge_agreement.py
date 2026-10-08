#!/usr/bin/env python3
"""Measure judge agreement against human labels in a gold-sample workbook."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

LABELS = {"pass", "fail", "could_not_run"}


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    judges, humans = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(judges[x] * humans[x] for x in LABELS) / (n * n)
    return 1.0 if expected == 1.0 and observed == 1.0 else (observed - expected) / (1 - expected)


def measure(path: Path) -> dict:
    ws = load_workbook(path, read_only=True, data_only=True)["sample"]
    row_iter = ws.iter_rows(values_only=True)
    headers = list(next(row_iter))
    rows = [dict(zip(headers, row)) for row in row_iter]
    labelled = []
    for row in rows:
        human = str(row.get("human_label") or "").strip().lower()
        judge = str(row.get("judge_verdict") or "").strip().lower()
        if not human:
            continue
        if human not in LABELS or judge not in LABELS:
            raise ValueError(f"invalid label in {row.get('case_id')}: judge={judge!r}, human={human!r}")
        labelled.append((row, judge, human))

    def stats(items):
        pairs = [(j, h) for _, j, h in items]
        n = len(pairs)
        return {
            "n": n,
            "agreement_rate": sum(j == h for j, h in pairs) / n if n else None,
            "cohens_kappa": kappa(pairs),
            "false_pass_rate": sum(j == "pass" and h == "fail" for j, h in pairs) / n if n else None,
            "false_fail_rate": sum(j == "fail" and h == "pass" for j, h in pairs) / n if n else None,
        }

    grouped = defaultdict(list)
    for item in labelled:
        grouped[item[0]["family"]].append(item)
    seeded = any(bool(row.get("seeded_label")) for row, _, _ in labelled)
    return {
        "warning": "SEEDED LABELS: test output only; not human calibration." if seeded else None,
        "overall": stats(labelled),
        "per_family": {family: stats(items) for family, items in sorted(grouped.items())},
        "disagreements": [
            {"case_id": r["case_id"], "family": r["family"], "judge_verdict": j,
             "human_label": h, "judge_reason": r.get("judge_reason"), "human_note": r.get("human_note")}
            for r, j, h in labelled if j != h
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--output", type=Path)
    ns = ap.parse_args()
    report = measure(ns.workbook)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if ns.output:
        ns.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
