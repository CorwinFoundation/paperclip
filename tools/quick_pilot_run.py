"""Preflight the customer boundary, capture/grade once, render saved evidence.

No target retries or automatic resume. Use internal_agent_pilot --regrade for
approved offline regrading of captured evidence, then quick_pilot_report.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3

from tools.internal_agent_pilot import main as pilot_main, read_json
from tools.quick_pilot_operations import preflight
from tools.quick_pilot_report import render


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--scope", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--judge-openrouter", action="store_true")
    parser.add_argument("--judge-budget-db", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("new_output_required")
        if args.judge_openrouter and args.judge_budget_db is None:
            raise ValueError("durable_judge_budget_required")
        if args.judge_openrouter:
            state = Path(os.environ["ARE_AMAZON_QUICK_STATE_DIR"])
            if (not state.is_absolute() or not args.judge_budget_db.is_absolute()
                    or args.judge_budget_db.resolve() != state.resolve() / "evaluator.sqlite3"):
                raise ValueError("canonical_durable_judge_budget_required")
        preflight(read_json(args.config), read_json(args.scope), judge_openrouter=args.judge_openrouter)
        command = ["--live", "--config", str(args.config), "--output", str(args.output)]
        if args.judge_openrouter:
            command += ["--judge-openrouter", "--judge-budget-db", str(args.judge_budget_db)]
        status = pilot_main(command)
        path = args.output / "run.json"
        if path.is_file():
            report = render(read_json(path, limit=8 * 1024 * 1024))
            with (args.output / "report.md").open("x", encoding="utf-8") as stream:
                stream.write(report)
        return status
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error):
        print(json.dumps({"status": "incomplete", "error": "pilot_preflight_or_reporting_failed",
                          "backbond_score": "N/A"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
