"""BEAAA-14828 — probe-fires proof for P3.3 shared code-runtime isolation.

Runs the deterministic shared-runtime scenario against its fixture records and
writes the evidence artifacts the ticket requires:

  * artifacts/beaaa-14828/shared_code_runtime_FIRE_leaky.json        (fires; LEAK_DETECTED, 4 boundaries)
  * artifacts/beaaa-14828/shared_code_runtime_FIRE_prose_denial.json (fires; LEAK_DETECTED despite a narrated refusal)
  * artifacts/beaaa-14828/shared_code_runtime_FIRE_isolated.json     (fires; RESISTED)
  * artifacts/beaaa-14828/shared_code_runtime_NA_dedicated.json      (N/A; flag false)

Coverage-realization only — reported-not-scored, weight 0.0, no scoring math and
no rubric/applicability_policy.yaml or probe_mappings change (see
harness/shared_code_runtime_isolation.py module docstring).

Usage:
    python3 scripts/beaaa14828_shared_code_runtime_fire.py [--out-dir PATH] [--check]

``--check`` re-derives the artifacts and exits non-zero if the committed copies
drifted, so CI can hold the lane without a network or a live runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import shared_code_runtime_isolation as scr  # noqa: E402

DEFAULT_OUT = REPO / "artifacts" / "beaaa-14828"

OUTPUTS = {
    "leaky_shared_runtime": "shared_code_runtime_FIRE_leaky.json",
    "leaky_shared_runtime_prose_denial": "shared_code_runtime_FIRE_prose_denial.json",
    "isolated_per_tenant_runtime": "shared_code_runtime_FIRE_isolated.json",
    "dedicated_runtime_not_applicable": "shared_code_runtime_NA_dedicated.json",
}


def _payload(record_id: str, record: dict) -> dict:
    return {
        "ticket": "BEAAA-14828",
        "scenario_registration": scr.SCENARIO_REGISTRATION,
        "record_id": record_id,
        "surface_id": record["surface_id"],
        "archetype": record["archetype"],
        "variant": record["variant"],
        "fixture": "fixtures/shared_code_runtime/v0_1.json",
        "shared_runtime_basis": record.get("shared_runtime_basis"),
        "agent_message": record.get("agent_message"),
        "tool_call_ledger": record.get("tool_call_ledger"),
        "probe_result": scr.evaluate_record(record).as_dict(),
    }


def _serialize(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="BEAAA-14828 shared code-runtime isolation probe-fires proof.")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true", help="verify committed artifacts instead of writing them")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    records = scr.records_by_id(scr.load_scenario())

    drift: list[str] = []
    for record_id, filename in OUTPUTS.items():
        text = _serialize(_payload(record_id, dict(records[record_id])))
        target = out_dir / filename
        if args.check:
            if not target.exists():
                drift.append(f"missing: {target.relative_to(REPO)}")
            elif target.read_text() != text:
                drift.append(f"drifted: {target.relative_to(REPO)}")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        result = json.loads(text)["probe_result"]
        print(f"{filename}: fired={result['fired']} verdict={result['verdict']} "
              f"crossed={result['boundaries_crossed']}")

    if args.check:
        for line in drift:
            print(f"DRIFT {line}", file=sys.stderr)
        if drift:
            return 1
        print(f"shared-runtime artifacts match the fixture ({len(OUTPUTS)} checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
