#!/usr/bin/env python3
"""Write advisory skill evidence independently of the BackBond Score."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.skill_inspection import inspect_skill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", type=Path)
    parser.add_argument("--engine-python", default=sys.executable)
    parser.add_argument("--out", type=Path, required=True, help="New evidence file outside the inspected skill")
    args = parser.parse_args()
    source = args.skill.resolve(strict=True)
    output = args.out.resolve()
    if output.is_relative_to(source):
        parser.error("Evidence output must be outside the inspected skill")
    report = inspect_skill(source, python=args.engine_python)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"status": report["status"], "findings": len(report["findings"]),
                      "review_required": report["review_required"], "advisory_only": True}))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
