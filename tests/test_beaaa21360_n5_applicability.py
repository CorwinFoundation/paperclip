"""Regression coverage for the canonical N=5 applicability authority path."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from rubric.scoring import Rubric
from scripts.beaaa6190_v05_rc_n5 import FIXTURES, RUBRIC_PATH, _score_fixture


def test_n5_runner_authorizes_scanner_owned_golden_applicability() -> None:
    """A golden sidecar with N/A waivers reaches scoring via trusted authority."""
    rubric = Rubric.load(RUBRIC_PATH)
    expected = json.loads((FIXTURES / "G1" / "expected_score.json").read_text())

    assert _score_fixture(rubric, "G1") == expected


def test_n5_runner_bare_command_is_self_contained() -> None:
    """The documented gate command needs no external signing-key setup."""
    env = os.environ.copy()
    env.pop("ARE_BUNDLE_SIGNING_KEY", None)
    env.pop("ARE_ALLOW_DEV_SIGNING_KEY", None)

    result = subprocess.run(
        [sys.executable, "scripts/beaaa6190_v05_rc_n5.py"],
        cwd=RUBRIC_PATH.parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "tools/calibration-deltas/2026-06-22-beaaa6190-v05-rc-n5.md" in result.stdout
