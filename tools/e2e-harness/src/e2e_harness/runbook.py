"""E2E run-book entrypoint (BEAAA-7427, Done #4).

A single script that, given a vendor + credentials, executes the whole loop
start-to-finish and emits a measured report:

    install -> load context -> run adversarial traffic -> capture
            -> ARE scan + score -> Monitor claim path

Usage
-----
Shadow self-test against the in-repo scenario kit (no vendor, no credentials —
this is what runs in CI and proves the chain is push-button)::

    python -m e2e_harness.runbook --fixture --out report.json

Live run (push-button once the Founder picks a vendor + shares creds)::

    python -m e2e_harness.runbook --vendor retell --creds-file creds.json \
        --out report.json

The live path is intentionally gated: adapters raise until scoped credentials
and a chosen vendor are supplied (BEAAA-7424), so a guessed vendor can never
silently ship.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .adapters.fixture import FixtureAdapter
from .pipeline import run_full_kit, run_scenario_chain
from .scenario_kit import SCENARIOS


def _load_creds(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text())


def run_fixture_mode(mode: str = "shadow") -> dict[str, Any]:
    return run_full_kit(FixtureAdapter(), SCENARIOS, mode=mode)


def run_live_mode(vendor: str, creds_path: str | None, mode: str = "shadow") -> dict[str, Any]:  # pragma: no cover
    adapter = get_adapter(vendor)
    creds = _load_creds(creds_path)
    raw_exports = adapter.fetch(creds)  # raises until authorized
    per_scenario = []
    for raw in raw_exports:
        session = adapter.to_session(raw)
        # Live exports are not pre-tagged with a scenario; report per session.
        from .scenario_kit import AdversarialScenario, Turn

        synthetic = AdversarialScenario(
            scenario_id=session.session_id,
            peril="P0",
            attack_family="live_capture",
            severity_target="S0",
            description="live vendor capture",
            conversation=session.turns,
        )
        per_scenario.append(run_scenario_chain(synthetic, adapter, mode=mode))
    return {"vendor": vendor, "mode": mode, "scenarios": per_scenario}


def _human_summary(report: dict[str, Any]) -> str:
    s = report.get("summary", {})
    lines = [
        f"E2E harness report — vendor={s.get('vendor')} mode={s.get('mode')}",
        f"  perils covered:            {', '.join(s.get('perils_covered', []))}",
        f"  scenarios run:             {s.get('scenarios_run')}",
        f"  ARE detections fired:      {s.get('are_detections_fired')}",
        f"    money-movement:          {s.get('are_money_movement_detections')}",
        f"    tool-misuse:             {s.get('are_tool_misuse_detections')}",
        f"    stop-instruction:        {s.get('are_stop_instruction_detections')}",
        f"  Monitor claim candidates:  {s.get('monitor_claim_candidates')}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e2e_harness.runbook", description=__doc__)
    parser.add_argument("--fixture", action="store_true", help="run the in-repo scenario kit (shadow self-test)")
    parser.add_argument("--vendor", choices=["retell", "voiceflow", "vapi"], help="live vendor")
    parser.add_argument("--creds-file", help="path to scoped vendor credentials JSON")
    parser.add_argument("--mode", default="shadow", choices=["shadow"], help="run mode (shadow only)")
    parser.add_argument("--out", help="write the JSON report here (default: stdout)")
    args = parser.parse_args(argv)

    if args.vendor and not args.fixture:
        report = run_live_mode(args.vendor, args.creds_file, mode=args.mode)
    else:
        report = run_fixture_mode(mode=args.mode)

    blob = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(blob)
    else:
        print(blob)
    print(_human_summary(report), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
