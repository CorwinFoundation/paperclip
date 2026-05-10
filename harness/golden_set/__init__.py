"""Golden-Agent Set v1.0 — calibration anchor for the ARE Scanner.

The 9 agents in `tests/fixtures/golden_agents/` are the regression-test
target for every release of the rubric, scoring engine, or pack runner.
Routed under BEAAA-163 Option C/B+C combined.

Layer 1 (deterministic, every CI run): rerun rubric/scoring against the
locked eval_outputs in each fixture; assert byte-identical match against
the recorded expected_score.

Layer 2 (stochastic, quarterly): rerun the probe packs against the live
agent N=5; assert per-dim raw within +/-0.05 and ARE total within +/-1.

The hardened system prompt at `system_prompt_v1.txt` is the literal text
sent to G1/G2/G3 during baseline scans. SHA-256 of that file is stamped
into snapshot.prompt_version so cross-vendor calibration purity is
auditable.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

GOLDEN_SET_VERSION = "1.0"

_THIS = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = _THIS / "system_prompt_v1.txt"


def system_prompt_text() -> str:
    """Return the literal hardened system prompt sent to G1/G2/G3."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def system_prompt_sha256() -> str:
    """Return the sha256:<hex> stamp for snapshot.prompt_version."""
    return "sha256:" + hashlib.sha256(SYSTEM_PROMPT_PATH.read_bytes()).hexdigest()
