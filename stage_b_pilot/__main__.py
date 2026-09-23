from __future__ import annotations

import argparse
import json
from pathlib import Path

from stage_a_demo.core import EvidenceStore

from .pilot import run_seeded_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the two seeded Stage B pilot scenarios end to end"
    )
    parser.add_argument("--db", default="stage-b-pilot.sqlite3")
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="print only the per-run telemetry counts",
    )
    args = parser.parse_args()

    path = Path(args.db)
    store = EvidenceStore(path)
    try:
        output = run_seeded_scenarios(store)
    finally:
        store.close()

    if args.counts_only:
        print(json.dumps(output["telemetry"]["counts"], indent=2, sort_keys=True))
    else:
        output["database"] = str(path)
        print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
