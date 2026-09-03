"""Deterministic shadow evaluator; never posts a required GitHub check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .authority import connect, evaluate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text())
    decision = evaluate(connect(args.db), **payload)
    result = {"check_name": "paperclip-qa-evidence-shadow", **decision.__dict__}
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if decision.conclusion == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

