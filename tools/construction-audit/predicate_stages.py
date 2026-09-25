#!/usr/bin/env python3
"""BEAAA-35660: decompose the drop from the checkpoint-1 upper bound to the final count.

Checkpoint 1 reported 848 flagged over a 2,875-row registration inventory.
The final audit reports 0 over the 2,507-row built scoring surface. Those are
different universes, so the two numbers cannot be subtracted.

This runs all three extraction methods over the SAME 2,507 rows, so the funnel
is apples-to-apples and the drop can be attributed:

  stage_0_whole_file   the checkpoint-1 method: scan the whole source file
  stage_1_prompt_only  only string literals bound to a prompt at build time
  stage_2_target_only  of those, only the ones the target reads, not the judge

The gap between stage 0 and stage 1 is predicate tightening. The gap between
stage 1 and stage 2 is the judge-channel correction. Indeterminate movement is
reported alongside, because resolving an indeterminate is a different kind of
change from narrowing a pattern.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from prompt_extract import python_prompt_literals, record_prompt_text  # noqa: E402
from audit_construction_integrity import (  # noqa: E402
    build_constructor_index, load_candidate_index, load_pack_records,
    load_signatures, registry_rows, resolve_constructors, _read,
)


def run_stages(rows: list[dict]) -> dict:
    sigs = load_signatures()
    ctor_index = build_constructor_index()
    candidates = load_candidate_index()
    cache: dict = {}

    stages = {
        "stage_0_whole_file": {"flagged": 0, "indeterminate": 0, "flagged_prod": 0},
        "stage_1_prompt_only": {"flagged": 0, "indeterminate": 0, "flagged_prod": 0},
        "stage_2_target_only": {"flagged": 0, "indeterminate": 0, "flagged_prod": 0},
    }

    for row in rows:
        case_id = row.get("case_id") or ""
        pack = row.get("source_pack") or ""
        prod = bool(row.get("scoring_active"))

        rec = load_pack_records(REPO / pack, cache).get(case_id) if pack else None
        if rec is None and row.get("case_class") == "content_candidate_probe":
            rec = candidates.get(case_id) or candidates.get(row.get("candidate_dedupe_sha256") or "")
        corpus_text = [c for c in record_prompt_text(rec)] if rec is not None else []

        ctors, _ = resolve_constructors(row, pack, ctor_index)

        def sig_hit(texts):
            return any(rx.search(t) for sig in sigs for rx in sig["compiled"] for t in texts if t)

        corpus_hit = sig_hit(corpus_text)
        has_text = {"stage_0_whole_file": bool(corpus_text),
                    "stage_1_prompt_only": bool(corpus_text),
                    "stage_2_target_only": bool(corpus_text)}
        hits = dict.fromkeys(has_text, corpus_hit)

        for ctor in ctors:
            # Per-file signature results are identical for every row that shares
            # the constructor, so compute them once and reuse.
            fkey = ("stagehits", ctor)
            if fkey not in cache:
                raw = _read(REPO / ctor, cache) or ""
                lits = python_prompt_literals(raw) if raw else []
                cache[fkey] = {
                    "stage_0_whole_file": (sig_hit([raw]), bool(raw)),
                    "stage_1_prompt_only": (sig_hit([l["text"] for l in lits]), bool(lits)),
                    "stage_2_target_only": (
                        sig_hit([l["text"] for l in lits if l["channel"] == "target"]),
                        any(l["channel"] == "target" for l in lits),
                    ),
                }
            for stage, (hit_f, has_f) in cache[fkey].items():
                hits[stage] = hits[stage] or hit_f
                has_text[stage] = has_text[stage] or has_f

        for name in stages:
            hit, texts = hits[name], has_text[name]
            if hit:
                stages[name]["flagged"] += 1
                if prod:
                    stages[name]["flagged_prod"] += 1
            elif not texts:
                stages[name]["indeterminate"] += 1

    return stages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="/tmp/atr_rows.json")
    ap.add_argument("--out", default="artifacts/beaaa-35660/predicate_stage_decomposition.json")
    args = ap.parse_args()

    rows = registry_rows(Path(args.rows))
    stages = run_stages(rows)
    s0, s1, s2 = (stages[k]["flagged_prod"] for k in
                  ("stage_0_whole_file", "stage_1_prompt_only", "stage_2_target_only"))
    payload = {
        "issue": "BEAAA-35660",
        "universe": "the 2,507 built registry rows, identical at every stage",
        "note": (
            "The checkpoint-1 figure of 848 was measured over a different, "
            "2,875-row registration inventory and is not subtractable from these. "
            "stage_0 is that same whole-file method re-run here for comparability."
        ),
        "stages": stages,
        "attribution_production_scored_479": {
            "whole_file_upper_bound": s0,
            "removed_by_prompt_literal_narrowing": s0 - s1,
            "removed_by_judge_channel_correction": s1 - s2,
            "final_flagged": s2,
        },
    }
    (REPO / args.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["stages"], indent=1))
    print(json.dumps(payload["attribution_production_scored_479"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
