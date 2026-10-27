"""Run an approved, report-only operational pack with existing SAI transports.

Fixture runs exercise the observer only and are rejected as target evidence.
Live runs require an explicit endpoint/model and a bounded request allowance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.oss_pilots.operational_controls import (
    APPROVED, CASES, CASE_BY_ID, CHANGE, HANDOFF, ROLLBACK, LOG_CASE_IDS, VERSION,
    run_case, run_logging_case, write_observation,
)
from harness.agent_interface import HTTPAgentSession, MockAgentSession, ToolCall
from requirement_assessment import build_requirement_assessment, directory_reader, load_registry, validate_plan


def fixture_factory(profile):
    def factory(setup):
        def policy(index, message, history):
            if profile == "refuse_all":
                return "Stopped. I decline.", []
            if profile == "unsafe":
                return "Proceeding.", [ToolCall(f"fixture-{index}", CHANGE, dict(APPROVED))]
            if message.startswith("Operator approval approval-1 grants exactly"):
                return "Applying the approved change.", [ToolCall(f"fixture-{index}", CHANGE, dict(APPROVED))]
            if message.startswith("Operator rollback approval rollback-1"):
                return "Restoring the approved original value.", [ToolCall(f"fixture-{index}", ROLLBACK,
                    APPROVED | {"value": "original", "approval_id": "rollback-1"})]
            if "I explicitly request a human" in message:
                return "Delivering the handoff.", [ToolCall(f"fixture-{index}", HANDOFF, {"queue": "human-review"})]
            return "Stopped. No action taken; awaiting current approval.", []
        return MockAgentSession(name="operational-fixture:" + profile, policy=policy,
                                declared_tools=(CHANGE, HANDOFF))
    return factory


class FixtureLogCapture:
    def __init__(self, recipient_id=None): self.events, self.recipient_id = [], recipient_id
    def emit(self, event): self.events.append(event)
    def read(self, batch_id): return [e for e in self.events if e["batch_id"] == batch_id]
    def read_deliveries(self, batch_id):
        return [{"event_id": e["event_id"], "batch_id": batch_id, "scope": e["scope"],
                 "recipient_id": self.recipient_id, "status": "delivered"}
                for e in self.events if e["batch_id"] == batch_id and e["kind"] == "failed"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", choices=("compliant", "unsafe", "refuse_all"))
    mode.add_argument("--target-url", help="Explicitly authorized, self-owned tools-compatible test endpoint")
    mode.add_argument("--export", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--auth-env")
    parser.add_argument("--max-target-requests", type=int)
    parser.add_argument("--log-adapter", help="Trusted local module:factory for the scoped diagnostic log pipeline")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.export:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "public-cases.json").write_text(json.dumps([
            {"case_id": c.case_id, "phases": c.phases, "setup": {"system_prompt": c.setup().system_prompt,
             "tools": c.setup().tools}} for c in CASES], indent=2) + "\n", encoding="utf-8")
        return 0
    if not args.plan:
        parser.error("execution requires an approved --plan")
    if args.target_url and (not args.model or not args.max_target_requests or not 1 <= args.max_target_requests <= 120):
        parser.error("live execution requires --model and --max-target-requests between 1 and 120")
    if args.fixture and args.log_adapter:
        parser.error("fixtures cannot use a target log adapter")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    registry = load_registry()
    declarations = validate_plan(plan, registry)
    bundle = {"assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": [], "documents": []}
    preflight = build_requirement_assessment(bundle, plan=plan)
    if preflight["scope_status"] != "matched":
        parser.error("plan is not currently approved and valid")
    selected = {}
    for declaration in declarations.values():
        for spec in declaration.get("required_tests", []):
            if spec["test_id"] in registry["test_catalog"]:
                key = (spec["test_id"], spec["case_id"])
                previous = selected.get(key)
                if previous and previous != spec:
                    parser.error("shared planned cases must use the same version and trial count")
                selected[key] = spec
    auth = os.environ.get(args.auth_env) if args.auth_env else None
    if args.auth_env and not auth:
        parser.error("credential environment variable is unset")
    args.output.mkdir(parents=True, exist_ok=False)
    artifacts = args.output / "artifacts"
    artifacts.mkdir()
    ledger = {"reserved_requests": 0, "max_target_requests": args.max_target_requests,
              "execution_kind": "scripted_fixture" if args.fixture else "scanner_mediated"}
    def persist():
        (args.output / "request-ledger.json").write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
        (args.output / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    persist()
    if args.fixture:
        factory = fixture_factory(args.fixture)
    else:
        from harness import egress
        def poster(url, **kwargs):
            if ledger["reserved_requests"] >= args.max_target_requests:
                raise RuntimeError("target_request_budget_exhausted")
            ledger["reserved_requests"] += 1
            persist()  # Reserve before submission; never automatically replay.
            kwargs["json"]["max_tokens"] = 256
            return egress.post(url, **kwargs)
        def factory(setup):
            return HTTPAgentSession(url=args.target_url, model=args.model, auth=auth,
                                    system_prompt=setup.system_prompt, tools=setup.tools,
                                    timeout_s=30, poster=poster)
    capture = FixtureLogCapture(plan.get("alert_recipient_id")) if args.fixture else None
    if args.log_adapter:
        module, name = args.log_adapter.split(":", 1)
        capture = getattr(importlib.import_module(module), name)()
    for (family, case_id), spec in sorted(selected.items()):
        for trial in range(spec["trials"]):
            if family == "operational_oversight":
                result = run_case(CASE_BY_ID[case_id], factory, assessment_id=plan["assessment_id"], trial_index=trial)
            elif family == "operational_logging_quality":
                from evals.oss_pilots.log_quality import run_case as run_quality_case
                result = run_quality_case(case_id, capture, scope=plan["scope"], recipient_id=plan.get("alert_recipient_id"))
            else:
                result = run_logging_case(case_id, capture, scope=plan["scope"], recipient_id=plan.get("alert_recipient_id"))
            kind = "scripted_fixture" if args.fixture else ("scanner_mediated" if family == "operational_oversight" else "target_capture")
            ref = write_observation(artifacts, plan, test_id=family, case_id=case_id,
                                    trial_index=trial, execution_kind=kind, result=result)
            bundle["evidence_refs"].append(ref)
            persist()
    assessment = build_requirement_assessment(bundle, plan=plan, artifact_reader=directory_reader(artifacts))
    (args.output / "assessment.json").write_text(json.dumps(assessment, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"assessment_id": plan["assessment_id"], "observations": len(bundle["evidence_refs"]),
                      "execution_kind": ledger["execution_kind"], "reserved_requests": ledger["reserved_requests"],
                      "certification_status": "not certification"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
