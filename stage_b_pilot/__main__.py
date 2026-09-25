from __future__ import annotations

import argparse
import json
from pathlib import Path

from stage_a_demo.core import EvidenceStore

from .pilot import run_model_agent_trials, run_seeded_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Stage B pilot scenarios end to end"
    )
    parser.add_argument("--db", default="stage-b-pilot.sqlite3")
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="print only the per-run telemetry counts (seeded mode only)",
    )
    parser.add_argument(
        "--model-agent",
        action="store_true",
        help="run the model-driven agent deviation measurement instead of seeded scenarios",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=3,
        help="number of trials per intent when --model-agent is used (default: 3)",
    )
    args = parser.parse_args()

    path = Path(args.db)
    store = EvidenceStore(path)
    try:
        if args.model_agent:
            output = run_model_agent_trials(store, trials_per_intent=args.trials)
        else:
            output = run_seeded_scenarios(store)
    finally:
        store.close()

    if not args.model_agent and args.counts_only:
        print(json.dumps(output["telemetry"]["counts"], indent=2, sort_keys=True))
    else:
        output["database"] = str(path)
        print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
