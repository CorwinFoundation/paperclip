#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.vapi_deterministic.verifier import build_results, canonical_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = build_results(args.source)
    if result["counts"]["executed_deterministic"] != 255:
        raise SystemExit("expected exactly 255 executed deterministic rows")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(result))


if __name__ == "__main__":
    main()
