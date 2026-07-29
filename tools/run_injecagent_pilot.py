"""Execute the disabled OSS-003 pilot and emit deterministic evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.oss_pilots.injecagent import run_pilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run = run_pilot()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "metric-report.json").write_text(
        json.dumps(run.report, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "evidence.json").write_text(
        json.dumps(run.evidence, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(run.report, sort_keys=True))
    return 0 if run.report["thresholds_met"] and run.report["security"]["attacker_tools_inert"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
