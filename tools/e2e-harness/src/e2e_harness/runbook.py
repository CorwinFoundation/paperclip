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
from .pipeline import (
    CONTRACT_VERSION,
    CORPUS_VERSION,
    PROBE_SET_VERSION,
    RUBRIC_VERSION,
    SCANNER_TAG,
    SCANNER_VERSION,
    SEED,
    run_captured_session_chain,
    run_full_kit,
)
from .scenario_kit import SCENARIOS


def _load_creds(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text())


def run_fixture_mode(mode: str = "shadow") -> dict[str, Any]:
    return run_full_kit(FixtureAdapter(), SCENARIOS, mode=mode)


def run_live_mode(
    vendor: str,
    creds_path: str | None,
    mode: str = "shadow",
    *,
    retell_agent_id: str | None = None,
    retell_call_id: str | None = None,
    max_calls: int | None = None,
    max_qa_minutes: int | None = None,
    drive_retell: bool = False,
    poll_seconds: float | None = None,
    poll_attempts: int | None = None,
) -> dict[str, Any]:  # pragma: no cover
    adapter = get_adapter(vendor)
    creds = _load_creds(creds_path)
    fetch_params = {
        "agent_id": retell_agent_id,
        "call_id": retell_call_id,
        "max_calls": max_calls,
        "max_qa_minutes": max_qa_minutes,
        "poll_seconds": poll_seconds,
        "poll_attempts": poll_attempts,
    }
    if drive_retell and vendor != "retell":
        raise ValueError("--drive-retell only applies to --vendor retell")
    if drive_retell:
        raw_exports = adapter.drive_scenarios(creds, SCENARIOS, **fetch_params)  # type: ignore[attr-defined]
    else:
        raw_exports = adapter.fetch(creds, **fetch_params)

    by_id = {s.scenario_id: s for s in SCENARIOS}
    per_scenario = []
    for raw in raw_exports:
        session = adapter.to_session(raw)
        scenario_id = (
            session.metadata.get("scenario_id")
            or session.metadata.get("backbond_scenario_id")
            or session.session_id
        )
        scenario = by_id.get(str(scenario_id)) or _scenario_from_session(session)
        per_scenario.append(run_captured_session_chain(session, scenario, mode=mode))
    all_are = [d for s in per_scenario for d in s["are_detections"]]
    all_monitor = [d for s in per_scenario for d in s["monitor_claim_candidates"]]
    summary = {
        "mode": mode,
        "vendor": vendor,
        "perils_covered": sorted({s["peril"] for s in per_scenario}),
        "scenarios_run": len(per_scenario),
        "are_detections_fired": sum(1 for d in all_are if d["fired"]),
        "are_money_movement_detections": sum(1 for d in all_are if d["fired"] and "money_movement" in d["attack_family"]),
        "are_tool_misuse_detections": sum(1 for d in all_are if d["fired"] and "tool_misuse" in d["attack_family"]),
        "are_stop_instruction_detections": sum(1 for d in all_are if d["fired"] and "stop_instruction" in d["attack_family"]),
        "monitor_claim_candidates": sum(1 for d in all_monitor if d["fired"]),
    }
    return {
        "vendor": vendor,
        "mode": mode,
        "scanner_version": SCANNER_VERSION,
        "scanner_tag": SCANNER_TAG,
        "rubric_version": RUBRIC_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "contract_version": CONTRACT_VERSION,
        "seed": SEED,
        "summary": summary,
        "scenarios": per_scenario,
    }


def _scenario_from_session(session):
    from .scenario_kit import AdversarialScenario

    return AdversarialScenario(
        scenario_id=session.session_id,
        peril="P0",
        attack_family="live_capture",
        severity_target="S0",
        description="live vendor capture",
        conversation=session.turns,
    )


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
    parser.add_argument("--retell-agent-id", help="Retell agent id for /v2/list-calls backfill")
    parser.add_argument("--retell-call-id", help="Retell call id for /v2/get-call/{call_id}")
    parser.add_argument("--drive-retell", action="store_true", help="create one Retell web call per P1-P6 scenario before fetch")
    parser.add_argument("--max-calls", type=int, help="live Retell call cap; fails above the free envelope")
    parser.add_argument("--max-qa-minutes", type=int, help="live Retell QA minute cap; fails above the free envelope")
    parser.add_argument("--poll-seconds", type=float, help="seconds between Retell get-call polls after create-web-call")
    parser.add_argument("--poll-attempts", type=int, help="Retell get-call poll attempts after create-web-call")
    parser.add_argument("--mode", default="shadow", choices=["shadow"], help="run mode (shadow only)")
    parser.add_argument("--out", help="write the JSON report here (default: stdout)")
    args = parser.parse_args(argv)

    if args.vendor and not args.fixture:
        report = run_live_mode(
            args.vendor,
            args.creds_file,
            mode=args.mode,
            retell_agent_id=args.retell_agent_id,
            retell_call_id=args.retell_call_id,
            max_calls=args.max_calls,
            max_qa_minutes=args.max_qa_minutes,
            drive_retell=args.drive_retell,
            poll_seconds=args.poll_seconds,
            poll_attempts=args.poll_attempts,
        )
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
