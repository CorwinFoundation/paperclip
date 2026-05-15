"""BEAAA-387 — persist v0.2.3 baselines for fixtures lockable via deterministic
replay (those with eval_outputs.json + expected_score.json already filed).

Scope today: B1 (Pi.ai) and M1 (HuggingChat/Llama-3.3-70B). Those two fixtures
already have v0.2.3-stamped expected_score.json files locked under
rubric_version=0.1.2; their L1 reproduction is already in CI. This script
formalises them as baseline artifacts under artifacts/beaaa-387/ so the §2
calibration-delta / §3 N=5 / §5 discrimination wiring (BEAAA-387d) can consume
a uniform manifest across all 9 fixtures.

The remaining 7 fixtures (G1-G3, B2, B3, M2, M3) require live API/path-B runs
and are owned by BEAAA-387b and BEAAA-387c.

Usage:
    python3 scripts/beaaa387_persist_deterministic_baselines.py

Exits non-zero if any deterministic replay diverges from its locked
expected_score (L1 contract violation; do not commit).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.golden_set.persist_baseline import (
    all_fixture_ids,
    fixture_lock_state,
    persist_from_locked_fixture,
    update_manifest,
)
from tests.test_golden_agents import _is_live  # type: ignore


DETERMINISTIC_FIXTURES = ("B1", "M1")


def main() -> int:
    locked: dict[str, dict] = {}
    for fid in DETERMINISTIC_FIXTURES:
        if not _is_live(fid):
            print(f"SKIP {fid}: not marked status=live", flush=True)
            continue
        print(f"PERSIST {fid}: deterministic_replay ...", flush=True)
        meta = persist_from_locked_fixture(fid)
        locked[fid] = {
            "fixture_id": fid,
            "lock_source": meta["lock_source"],
            "locked_at": meta["locked_at"],
            "scanner_version": meta["scanner_version"],
            "rubric_version": meta["rubric_version"],
            "corpus_version": meta["corpus_version"],
            "seed": meta["seed"],
            "ari_total": meta["ari_total"],
            "band_display": meta["band_display"],
            "expected_bmi": meta["agent_metadata"].get("expected_bmi"),
            "expected_band": meta["agent_metadata"].get("expected_band"),
            "slot": meta["agent_metadata"].get("slot"),
            "baseline_persisted": True,
        }
        print(
            f"  ARE={meta['ari_total']}  band={meta['band_display']}  "
            f"scanner={meta['scanner_version']}  rubric={meta['rubric_version']}",
            flush=True,
        )

    # Seed manifest with the remaining 7 pending fixtures so §2/§3/§5 wiring
    # has a stable index of the full set, with baseline_persisted=False on
    # rows BEAAA-387b/c are still to fill.
    for fid in all_fixture_ids():
        if fid in locked:
            continue
        locked.setdefault(fid, fixture_lock_state(fid))

    manifest_path = update_manifest(locked)
    print(f"\nMANIFEST {manifest_path.relative_to(REPO)}", flush=True)
    print(json.dumps(
        {k: v.get("baseline_persisted", False) for k, v in locked.items()},
        indent=2, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
