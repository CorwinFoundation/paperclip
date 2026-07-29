"""Generate the BEAAA-18880 inert Augustus evidence-only pilot."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.bench.augustus_evidence import run_pilot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = run_pilot(args.output_dir)
    print(f"executions={manifest['executions']} scoring_effect={manifest['scoring_effect']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
