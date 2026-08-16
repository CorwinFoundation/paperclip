"""Capture the three golden-set entrypoint emissions under a stated boundary mode.

Usage: capture.py <outdir> <test_mode:true|false>

test_mode=true  -> rubric.scoring._TEST_MODE = True, which restores the exact
                   pre-BEAAA-5276 boundary (corpus_version=None accepted).
                   This is how the PRE-fix reference is obtained: the crash is
                   an entrypoint-boundary artifact, not a scoring difference,
                   so suppressing the raise recovers the emission the harness
                   produced before abd9116e landed.
test_mode=false -> production default. Used for the POST-fix run.

`generated_at` is wall-clock and is stripped from every emission before
hashing; every other byte is compared.
"""
import hashlib, json, sys
from pathlib import Path

outdir = Path(sys.argv[1]); outdir.mkdir(parents=True, exist_ok=True)
test_mode = sys.argv[2] == "true"

import rubric.scoring as scoring
scoring._TEST_MODE = test_mode

from harness.golden_set.calibration_delta import compute_delta
from harness.golden_set.n5_band import run_l1_n5
from harness.golden_set.discrimination import compute_discrimination

TODAY = "2026-08-16"
ENTRYPOINTS = {
    "calibration_delta": lambda: compute_delta(today=TODAY),
    "n5_band":           lambda: run_l1_n5(today=TODAY),
    "discrimination":    lambda: compute_discrimination(today=TODAY),
}

for name, fn in ENTRYPOINTS.items():
    out = fn()
    out.pop("generated_at", None)
    blob = json.dumps(out, indent=2, sort_keys=True).encode()
    (outdir / f"{name}.json").write_bytes(blob)
    print(f"{name}: sha256={hashlib.sha256(blob).hexdigest()}")
    print(f"  summary={out.get('summary', {}).get('summary_line', out.get('summary'))}")
