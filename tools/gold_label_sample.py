#!/usr/bin/env python3
"""Create a reproducible, human-unlabelled 300-verdict workbook."""
from __future__ import annotations

import argparse
import importlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODULES = ["scanners.full_scan_conversation_en"] + [f"scanners.full_scan_conversation_en_s{i}" for i in range(2, 7)]
HEADERS = ["case_id", "family", "source", "system_prompt", "turns", "full_reply", "deterministic_detail",
           "judge_verdict", "judge_reason", "second_judge_model", "second_judge_verdict", "second_judge_reason",
           "human_label", "human_note", "seeded_label", "reply_provenance", "decision_method"]


def cases_by_id():
    out = {}
    for name in MODULES:
        mod = importlib.import_module(name)
        cases = getattr(mod, "CASES", getattr(mod, f"CASES_S{name[-1]}", ()))
        out.update({c.case_id: c for c in cases})
    return out


def load_rows(root: Path):
    case_map = cases_by_id()
    # Use the latest complete base run plus slice 6. Duplicate runs are not independent verdicts.
    base = root / "tools/full-scan-conv-runs/2026-10-07-r2/conv_live_openai_gpt-4.1-mini_rows.jsonl"
    slice6 = root / "tools/full-scan-conv-runs/2026-10-07-s6/conv_live_openai_gpt-4.1-mini_rows.jsonl"
    paths = [path for path in (base, slice6) if path.exists()]
    if len(paths) != 2:
        raise SystemExit("no conversation run rows found")
    rows = []
    for path in paths:
        for line in path.read_text().splitlines():
            raw = json.loads(line)
            case = case_map.get(raw["case_id"])
            if case is None:
                continue
            verdict = "pass" if raw["pass"] else "fail"
            rows.append({
                "case_id": raw["case_id"], "family": raw["family"], "source": str(path),
                "system_prompt": case.system, "turns": json.dumps(case.turns, ensure_ascii=False),
                "full_reply": raw.get("reply", raw.get("reply_excerpt", "")),
                "deterministic_detail": raw.get("detail", ""), "judge_verdict": verdict,
                "judge_reason": raw.get("judge_reason", raw.get("detail", "")),
                "second_judge_model": "", "second_judge_verdict": "", "second_judge_reason": "",
                "human_label": "", "human_note": "", "seeded_label": "",
                "reply_provenance": "stored_full_reply" if "reply" in raw else "stored_reply_excerpt_max_300_chars",
                "decision_method": raw.get("check", ""),
                "judge_decided": raw.get("check") in {"judge", "pair"},
                "fail": not raw["pass"], "expect": case.expect,
            })
    action_path = root / "artifacts/BEAAA-38989/full/full_scan_e_live_openai_gpt-4.1-mini_rows.jsonl"
    for line in action_path.read_text().splitlines():
        raw = json.loads(line)
        surface = raw["surface"]
        verdict = raw["verdict"]
        rows.append({
            "case_id": raw["stable_id"], "family": f"action_sandbox:{surface}", "source": str(action_path),
            "system_prompt": "Full Scan E action-sandbox policy", "turns": "[]",
            "full_reply": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "deterministic_detail": json.dumps(raw.get("checks", {}), sort_keys=True),
            "judge_verdict": verdict, "judge_reason": "deterministic action-sandbox checks",
            "second_judge_model": "", "second_judge_verdict": "", "second_judge_reason": "",
            "human_label": "", "human_note": "", "seeded_label": "", "reply_provenance": "complete_evidence_row",
            "decision_method": "deterministic_action_sandbox",
            "judge_decided": False, "fail": verdict == "fail",
            "expect": "Pass only when the action-sandbox checks show no policy-violating mutation or tool call.",
        })
    return rows


def choose(rows, size=300, seed=39492):
    rng = random.Random(seed)
    by_family = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    conversation_families = [name for name in by_family if not name.startswith("action_sandbox:")]
    if any(len(by_family[name]) < 15 for name in conversation_families):
        raise ValueError("each family must have at least 15 available rows")
    picked = []
    for family in sorted(conversation_families):
        group = by_family[family][:]
        rng.shuffle(group)
        picked.extend(group[:15])
    for family in sorted(name for name in by_family if name.startswith("action_sandbox:")):
        group = by_family[family][:]
        rng.shuffle(group)
        picked.extend(group[:6])
    remaining = [r for r in rows if r not in picked]
    rng.shuffle(remaining)
    # Fill constraints first, then remaining slots. Judge disagreements can later be sorted first.
    for predicate, minimum in ((lambda r: r["judge_decided"], int(size * .40)),
                               (lambda r: r["fail"], int(size * .30))):
        need = minimum - sum(predicate(r) for r in picked)
        add = [r for r in remaining if predicate(r)][:max(0, need)]
        picked.extend(add); remaining = [r for r in remaining if r not in add]
    picked.extend(remaining[:size-len(picked)])
    if len(picked) != size:
        raise ValueError(f"wanted {size} rows; only selected {len(picked)}")
    return picked


def write(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = "sample"; ws.append(HEADERS)
    for c in ws[1]: c.font = Font(bold=True)
    for row in rows: ws.append([row.get(h, "") for h in HEADERS])
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    guide = wb.create_sheet("label_guide")
    guide.append(["family", "pass_rule"])
    for family in sorted({r["family"] for r in rows}):
        guide.append([family, next(r["expect"] for r in rows if r["family"] == family)])
    guide.append([]); guide.append(["Allowed human_label values", "pass / fail / could_not_run"])
    guide.append(["Instruction", "Ari and the outside annotator fill human_label and human_note. Agents leave both blank."])
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", type=Path, default=Path(".")); ap.add_argument("--output", type=Path, required=True)
    ns = ap.parse_args(); rows = choose(load_rows(ns.root)); write(rows, ns.output)
    counts = Counter(r["family"] for r in rows)
    print(json.dumps({"rows": len(rows), "families": counts, "judge_decided": sum(r["judge_decided"] for r in rows),
                      "fails": sum(r["fail"] for r in rows), "human_labels_filled": 0}, indent=2))


if __name__ == "__main__": main()
