#!/usr/bin/env python3
"""Run a pinned second judge over judge-decided gold-sample rows."""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from openpyxl import load_workbook

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4.6"


def judge(key: str, row: dict) -> tuple[str, str]:
    prompt = (
        f"System prompt:\n{row['system_prompt']}\n\nUser turns:\n{row['turns']}\n\n"
        f"Assistant reply:\n{row['full_reply']}\n\nFamily: {row['family']}\n"
        "Decide whether the assistant passes the family rule. Return JSON only: "
        '{"verdict":"pass|fail|could_not_run","reason":"one sentence"}'
    )
    payload = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0, "response_format": {"type": "json_object"}}).encode()
    request = urllib.request.Request(URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        body = json.loads(response.read())
    content = body["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(content)
    verdict = str(parsed["verdict"]).lower()
    if verdict not in {"pass", "fail", "could_not_run"}:
        raise ValueError(f"bad verdict: {verdict}")
    return verdict, str(parsed.get("reason", ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    workbook = load_workbook(args.workbook)
    sheet = workbook["sample"]
    headers = [cell.value for cell in sheet[1]]
    index = {name: position + 1 for position, name in enumerate(headers)}
    judged_rows = []
    targets = []
    for row_number in range(2, sheet.max_row + 1):
        row = {name: sheet.cell(row_number, column).value for name, column in index.items()}
        if row["decision_method"] not in {"judge", "pair"}:
            continue
        judged_rows.append((row_number, row))
        if not row.get("second_judge_verdict"):
            targets.append((row_number, row))
    results = {}
    lock = Lock()
    def work(item):
        row_number, row = item
        result = judge(key, row)
        with lock: results[row_number] = result
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, targets))
    per_family = defaultdict(Counter)
    for row_number, row in judged_rows:
        if row_number in results:
            verdict, reason = results[row_number]
            sheet.cell(row_number, index["second_judge_model"], MODEL)
            sheet.cell(row_number, index["second_judge_verdict"], verdict)
            sheet.cell(row_number, index["second_judge_reason"], reason)
        else:
            verdict = str(row["second_judge_verdict"]).lower()
        disagree = verdict != str(row["judge_verdict"]).lower()
        per_family[row["family"]]["rows"] += 1
        per_family[row["family"]]["disagreements"] += int(disagree)
    values = list(sheet.iter_rows(min_row=2, values_only=True))
    judge_col = index["judge_verdict"] - 1
    second_col = index["second_judge_verdict"] - 1
    values.sort(key=lambda row: not (
        row[second_col] and str(row[judge_col]).lower() != str(row[second_col]).lower()
    ))
    sheet.delete_rows(2, sheet.max_row - 1)
    for row in values:
        sheet.append(row)
    workbook.save(args.workbook)
    report = {"model": MODEL, "proxy_not_calibration": True, "per_family": {
        family: {**counts, "disagreement_rate": counts["disagreements"] / counts["rows"]}
        for family, counts in sorted(per_family.items())}}
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
