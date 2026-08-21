"""BEAAA-24612 F6 repro: undeclared MCP surface is executed and reported as a pass.

Drives the REAL production dispatch path (RegisteredCaseExecutor._execute_case)
with the real registry row for mcp_tool_integrity:mcp-01 and an empty
capability_manifest (i.e. the target declares NO MCP/A2A integration).

Required end state (BEAAA-24008 ruling D.3 + the family's own declared
applicability gate):  verdict=not_applicable, score=None, 0 target calls,
0 judge calls.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)
from pathlib import Path
import tempfile

FAMILY = "mcp_tool_integrity"
CASE = "mcp-01"


def run(capability_manifest, label):
    rows = [r for r in build_registry()
            if r.get("test_id") == FAMILY and r.get("case_id") == CASE]
    assert len(rows) == 1, f"expected exactly one {FAMILY}:{CASE} row, got {len(rows)}"
    row = dict(rows[0])
    row["capability_manifest"] = capability_manifest

    target_prompts = []
    judge_prompts = []

    def target(prompt):
        target_prompts.append(prompt)
        # A cooperative target that answers the decision contract correctly.
        return json.dumps({"rejected_tool": True, "rationale": "repro"})

    def judge(prompt):
        judge_prompts.append(prompt)
        return json.dumps({"verdict": "pass", "score": 1.0})

    with tempfile.TemporaryDirectory() as td:
        ex = RegisteredCaseExecutor(
            subject=MockRunner("target", target),
            judge=MockRunner("judge", judge),
            evidence_store=EvidenceStore(Path(td)),
            budget=ScanBudget(),
        )
        try:
            outcome = ex._execute_case(FAMILY, CASE, row)
            err = None
        except Exception as exc:  # noqa: BLE001
            outcome = {}
            err = f"{type(exc).__name__}: {exc}"

    return {
        "label": label,
        "runner_declared": row.get("runner"),
        "error": err,
        "verdict": outcome.get("verdict"),
        "score": outcome.get("score"),
        "target_calls": len(target_prompts),
        "judge_calls": len(judge_prompts),
    }


def main():
    results = []
    for flag in ("off", "on"):
        if flag == "on":
            os.environ["BACKBOND_FOUNDER_DEV_MODE"] = "on"
        else:
            os.environ.pop("BACKBOND_FOUNDER_DEV_MODE", None)
        # empty manifest = target declares no MCP/A2A surface
        results.append(run({}, f"dev_mode={flag} capability_manifest={{}}"))
    os.environ.pop("BACKBOND_FOUNDER_DEV_MODE", None)
    # control: a target that DOES declare the MCP surface must still execute
    results.append(run({"has_mcp_tool_integration": True},
                       "dev_mode=off capability_manifest={has_mcp_tool_integration:True} (POSITIVE CONTROL)"))

    print(json.dumps(results, indent=2))

    failures = []
    for r in results[:2]:
        if not (r["verdict"] == "not_applicable" and r["score"] is None
                and r["target_calls"] == 0 and r["judge_calls"] == 0):
            failures.append(r["label"])
    control = results[2]
    control_ok = control["target_calls"] > 0 and control["verdict"] != "not_applicable"

    print()
    if failures:
        print("F6 REPRODUCED - undeclared MCP surface executed on: " + "; ".join(failures))
    else:
        print("F6 NOT reproduced - applicability gate held on both flag settings")
    print(f"POSITIVE CONTROL (declared surface still executes): {'OK' if control_ok else 'BROKEN'}")
    # exit 0 when the gate holds AND the control still executes
    return 0 if (not failures and control_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
