#!/usr/bin/env python3
"""CLI wrapper for the BEAAA-15495 demo readiness gate."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.demo_readiness_gate import main


if __name__ == "__main__":
    raise SystemExit(main())
