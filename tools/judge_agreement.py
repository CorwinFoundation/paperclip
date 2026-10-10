#!/usr/bin/env python3
"""Agreement reporting for legacy human workbooks and dual-judge evidence."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

if __package__ in (None, ""):
    # Allow `python tools/judge_agreement.py` as well as `python -m tools.judge_agreement`.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.dual_judge_grader import agreement_metrics, render_metrics

LABELS = {"pass", "fail", "could_not_run"}


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[x] * right[x] for x in LABELS) / (n * n)
    return 1.0 if expected == 1.0 and observed == 1.0 else (observed - expected) / (1 - expected)


def _column_specs(headers: list[str], reviewer_columns: list[str] | None) -> list[tuple[str, str, str]]:
    if reviewer_columns:
        specs = []
        for spec in reviewer_columns:
            try:
                reviewer, pass_id, column = spec.split(":", 2)
            except ValueError as exc:
                raise ValueError(f"invalid --reviewer {spec!r}; expected REVIEWER_ID:PASS_ID:COLUMN") from exc
            if column not in headers or not reviewer or not pass_id:
                raise ValueError(f"invalid --reviewer {spec!r}: column/tag missing")
            specs.append((reviewer, pass_id, column))
        return specs
    specs = []
    for column in headers:
        if column.startswith("reviewer__"):
            parts = column.split("__")
            if len(parts) != 3 or not parts[1] or not parts[2]:
                raise ValueError(f"invalid reviewer column {column!r}; expected reviewer__ID__PASS")
            specs.append((parts[1], parts[2], column))
    if not specs and "human_label" in headers:
        specs.append(("human", "pass1", "human_label"))
    return specs


def _agreement(pairs: list[tuple[str, str]]) -> dict:
    return {
        "n": len(pairs),
        "agreement_rate": sum(a == b for a, b in pairs) / len(pairs) if pairs else None,
        "cohens_kappa": kappa(pairs),
    }


def measure(path: Path, reviewer_columns: list[str] | None = None) -> dict:
    ws = load_workbook(path, read_only=True, data_only=True)["sample"]
    row_iter = ws.iter_rows(values_only=True)
    headers = [str(value) for value in next(row_iter)]
    rows = [dict(zip(headers, row)) for row in row_iter]
    specs = _column_specs(headers, reviewer_columns)
    if not specs:
        raise ValueError("no reviewer label columns found")
    labelled_rows, contested = [], []
    for row in rows:
        labels = {}
        for reviewer, pass_id, column in specs:
            value = str(row.get(column) or "").strip().lower()
            if value and value not in LABELS:
                raise ValueError(f"invalid label in {row.get('case_id')} column {column}: {value!r}")
            if value:
                labels[(reviewer, pass_id)] = value
        if not labels:
            continue
        adjudicated = str(row.get("adjudication_label") or "").strip().lower()
        reason = str(row.get("adjudication_reason") or "").strip()
        if adjudicated and (adjudicated not in LABELS or not reason):
            raise ValueError(f"adjudication for {row.get('case_id')} requires a valid label and reason")
        distinct = set(labels.values())
        reference = adjudicated or (next(iter(distinct)) if len(distinct) == 1 else None)
        if reference is None:
            contested.append({"case_id": row.get("case_id"), "family": row.get("family"), "reviewer_labels": {f"{r}:{p}": v for (r, p), v in labels.items()}})
        labelled_rows.append((row, labels, reference))
    within, between = {}, {}
    by_reviewer = defaultdict(list)
    for reviewer, pass_id, column in specs:
        by_reviewer[reviewer].append((pass_id, column))
    for reviewer, passes in sorted(by_reviewer.items()):
        for (pass_a, _), (pass_b, _) in combinations(passes, 2):
            pairs = [(labels[(reviewer, pass_a)], labels[(reviewer, pass_b)]) for _, labels, _ in labelled_rows if (reviewer, pass_a) in labels and (reviewer, pass_b) in labels]
            within[f"{reviewer}:{pass_a}_vs_{pass_b}"] = _agreement(pairs)
    for reviewer_a, reviewer_b in combinations(sorted(by_reviewer), 2):
        pairs = []
        for _, labels, _ in labelled_rows:
            a = [v for (r, _), v in labels.items() if r == reviewer_a]
            b = [v for (r, _), v in labels.items() if r == reviewer_b]
            if a and b and len(set(a)) == len(set(b)) == 1:
                pairs.append((a[0], b[0]))
        between[f"{reviewer_a}_vs_{reviewer_b}"] = _agreement(pairs)
    reference_rows = [(row, str(row.get("judge_verdict") or "").strip().lower(), reference) for row, _, reference in labelled_rows if reference is not None]
    for row, judge, reference in reference_rows:
        if judge not in LABELS:
            raise ValueError(f"invalid judge label in {row.get('case_id')}: {judge!r}")
    def stats(items):
        pairs = [(judge, reference) for _, judge, reference in items]
        result = _agreement(pairs)
        n = len(pairs)
        result.update({"false_pass_rate": sum(j == "pass" and h == "fail" for j, h in pairs) / n if n else None, "false_fail_rate": sum(j == "fail" and h == "pass" for j, h in pairs) / n if n else None})
        return result
    grouped = defaultdict(list)
    for item in reference_rows:
        grouped[item[0]["family"]].append(item)
    seeded = any(bool(row.get("seeded_label")) for row, _, _ in labelled_rows)
    return {"warning": "SEEDED LABELS: test output only; not human calibration." if seeded else None, "reviewer_columns": [{"reviewer_id": r, "pass_id": p, "column": c} for r, p, c in specs], "within_reviewer_repeatability": within, "inter_reviewer_agreement": between, "reference": {"n": len(reference_rows), "contested_count": len(contested), "contested_rows": contested}, "overall": stats(reference_rows), "per_family": {family: stats(items) for family, items in sorted(grouped.items())}, "disagreements": [{"case_id": r["case_id"], "family": r["family"], "judge_verdict": j, "reference_label": h, "judge_reason": r.get("judge_reason")} for r, j, h in reference_rows if j != h]}


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(data, dict):
        data = data.get("rows", data.get("results", []))
    if not isinstance(data, list):
        raise ValueError("evidence must be a JSON array or an object containing rows")
    return [dict(row) for row in data]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--reviewer", action="append", default=[], metavar="ID:PASS:COLUMN")
    ap.add_argument("--output", type=Path)
    ns = ap.parse_args()
    if ns.input.suffix.lower() in {".xlsx", ".xlsm"}:
        report = measure(ns.input, ns.reviewer or None)
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    else:
        text = render_metrics(agreement_metrics(load_rows(ns.input)))
    if ns.output:
        ns.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
