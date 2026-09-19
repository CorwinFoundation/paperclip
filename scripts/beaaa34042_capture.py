"""BEAAA-34042 round 2, step CAPTURE: re-measure the 14 cac-* rows held below the stability floor.

Budget, priced and posted on the issue before any call went out:
  14 rows, 1 attempt each, hard cap 14 target calls, 0 judge calls, USD 0.00.
  A row that errors STAYS errored and is reported with its literal reason.
  No top-up, no retry, no second pass. CEO ruling 2026-09-18: a floor that gets
  re-rolled until it passes is not a floor.

The 14 rows are DERIVED from the committed ledger, not listed here: they are the
``consequential_action_confirmation`` rows whose only hold reason is the spread
literal. ``cac-04`` holds ``unsupported_verdict`` and is out of scope by the card.

Same case, same seed, same oracle, same target as round 1. The only thing that
differs is when it ran - which is the measurement, not a confound. A family that
answers differently across two separated captures is precisely what the stability
floor exists to catch.
"""
import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.consequential_action_confirmation_adapter import (
    DecisionContractViolation, UnstructuredDecision, build_instruction, case_for_id, read_and_grade)
from harness.customer_runner import _VapiCustomerAgentRunner
from tools.beaaa31092_content_slice import BELOW_STABILITY_FLOOR

ASSISTANT = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
TARGET_ID = "vapi:09aea361@api.vapi.ai"
MODEL_VERSION = "vapi:09aea361-04b3-4a62-bcb0-2f2582f11ec8@api.vapi.ai"
FAMILY = "consequential_action_confirmation"
RUBRIC_POINT_ID = "ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION"
SEED = 42
LEDGER = ROOT / "artifacts/beaaa-31092/rank2-ledger.json"
MAX_TARGET_CALLS = 14


def in_scope(ledger_path: Path = LEDGER) -> list[str]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    return sorted(row["stable_case_id"] for row in ledger["rows"]
                  if row["family_id"] == FAMILY and row["hold_reasons"] == [BELOW_STABILITY_FLOOR])


def capture(round_id: str, out_dir: Path) -> dict:
    cases = in_scope()
    if len(cases) != MAX_TARGET_CALLS:
        raise SystemExit(f"expected {MAX_TARGET_CALLS} rows below the floor, ledger names {len(cases)}: {cases}")
    out_dir.mkdir(parents=True, exist_ok=True)
    subject = _VapiCustomerAgentRunner(secret=os.environ["VAPI_API_KEY"], assistant_id=ASSISTANT)

    spent, records = 0, []
    for case_id in cases:
        if spent >= MAX_TARGET_CALLS:
            raise SystemExit(f"ceiling reached at {spent} target calls; stopping with {case_id} unmeasured")
        case = case_for_id(case_id)
        instruction = build_instruction(case, SEED)
        started = time.time()
        spent += 1
        try:
            response = subject.complete(instruction)
        except Exception as exc:  # a row that errors stays errored
            records.append({"stable_case_id": case_id, "family_id": FAMILY, "capture_round": round_id,
                            "verdict": None, "status": "incomplete", "target_calls": 1,
                            "incomplete_reason": f"target_call_failed: {type(exc).__name__}: {exc}"[:300]})
            print(f"{case_id}: ERROR {type(exc).__name__}", flush=True)
            continue
        try:
            decision, verdict = read_and_grade(case, response)
            detail = {"decision": decision}
        except UnstructuredDecision as exc:
            decision, verdict = None, "not_applicable"
            detail = {"applicability_reason": "target_returned_no_structured_decision_object",
                      "applicability_detail": str(exc)[:500]}
        except DecisionContractViolation as exc:
            decision, verdict = None, "fail"
            detail = {"decision_contract_violation": str(exc)[:500]}
        record = {
            "stable_case_id": case_id, "family_id": FAMILY, "capture_round": round_id,
            "evidence_ref": f"{round_id}/{FAMILY}/{case_id}.json",
            "rubric_point_id": RUBRIC_POINT_ID, "recorded_target_response": response,
            "verdict": verdict, "status": "complete", "acquisition_basis": "target-measured",
            "target_calls": 1, "judge_calls": 0, "estimated_judge_cost_usd": 0.0,
            "target_id": TARGET_ID, "model_version": MODEL_VERSION, "seed": SEED,
            "duration_ms": int((time.time() - started) * 1000),
            "expected": dict(case.oracle), "payload": instruction, "judge": {
                "type": "consequential_action_confirmation_registered_adapter",
                "decision_field": "confirmed_before_acting", **detail},
        }
        records.append(record)
        (out_dir / FAMILY).mkdir(parents=True, exist_ok=True)
        (out_dir / FAMILY / f"{case_id}.json").write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
        print(f"{case_id}: {verdict}", flush=True)

    log = out_dir / "capture-log.jsonl"
    log.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    return {"round": round_id, "target_calls": spent, "judge_calls": 0,
            "estimated_judge_cost_usd": 0.0, "records": records}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--out", type=Path, required=True)
    result = capture(ap.parse_args().round, ap.parse_args().out)
    verdicts = {}
    for r in result["records"]:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    print(f"\nSPENT: {result['target_calls']} target calls, 0 judge calls, USD 0.00")
    print(f"VERDICTS: {json.dumps(verdicts, sort_keys=True)}")
