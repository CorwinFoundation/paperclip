"""Rejudge retained PI replies; no target calls and no historical result mutation."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scanners.starter_judge import JUDGE_MODEL, JUDGE_POLICY_VERSION, judge_client_from_env, judge_starter_case, starter_score
from scanners.starter_pack_v3 import STARTER_V3_CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    judge = judge_client_from_env()
    if judge is None:
        parser.error("OPENROUTER_API_KEY required")
    args.output.mkdir(parents=True, exist_ok=False)
    cases = {c.case_id: c for c in STARTER_V3_CASES}
    summary = {"kind": "retained-reply replay; not live Scanner acceptance", "judge_policy_version": JUDGE_POLICY_VERSION,
               "judge_model": JUDGE_MODEL, "judge_seed": 0, "judge_temperature": 0, "targets": {}}
    for target in "ABC":
        source = args.input / (target + ".jsonl")
        rows = [json.loads(line) for line in source.read_text().splitlines()]
        assert len(rows) == 100 and len({r["test_id"] for r in rows}) == 100
        affected = [r for r in rows if r["family"] == "prompt_injection"]
        assert len(affected) == 10
        def rejudge(row):
            case = cases[row["test_id"]]
            assert case.prompt == row["prompt"]
            verdict, reason = judge_starter_case(case, row["reply"], judge_client=judge)
            return {**row, "previous_verdict": row["verdict"], "previous_reason": row["reason"],
                    "verdict": verdict, "reason": reason, "judge_policy_version": JUDGE_POLICY_VERSION}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            changed = list(pool.map(rejudge, affected))
        replacements = {r["test_id"]: r for r in changed}
        replay = [replacements.get(r["test_id"], r) for r in rows]
        (args.output / (target + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in replay))
        summary["targets"][target] = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "previous": starter_score(rows), "replayed": starter_score(replay),
            "rejudged_cases": len(changed), "unchanged_cases": len(rows)-len(changed)}
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(target, summary["targets"][target]["replayed"]["score"], flush=True)

if __name__ == "__main__":
    main()
