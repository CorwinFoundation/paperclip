"""Offline customer preflight and runtime checks for the AWS Quick pilot.

These commands never generate an embed URL, submit a prompt or call a judge.
Preflight checks operator evidence; it does not independently audit AWS ACLs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess

from evals.internal_agent_knowledge import CASES, ROLES
from harness.amazon_quick import load_target, _sha
from tools.internal_agent_pilot import read_json, validate_setup, preflight_targets

SCHEMA = "backbond-quick-processing-scope/v1"
# Commercial regions with Agentic Features=Yes in AWS's 2026-10-25 table.
# Refresh this dated qualification list before adding another region.
REGIONS = {"us-east-1", "us-west-2", "eu-central-1", "eu-west-1", "eu-west-2",
           "ap-southeast-1", "ap-southeast-2", "ap-northeast-1"}
REGION_SOURCE = "https://docs.aws.amazon.com/quick/latest/userguide/regions.html"
COUNTS = dict(Counter(case.role for case in CASES))


class PreflightError(ValueError):
    """Only fixed operator-actionable codes may appear in CLI diagnostics."""


def processing_example():
    return {"schema": SCHEMA, "approved": False, "scope_id": "customer-knowledge-pilot",
            "region": "REPLACE: customer's actual Quick region",
            "authentication": "REPLACE: registered_user or identity_center",
            "execution_location": "REPLACE: backbond_aws or customer_aws",
            "customer_authorization_ref": "REPLACE: customer's authorized agent, tests, identities and window",
            "processing_agreement_ref": "REPLACE: execution account/region, evidence location, evaluator and deletion",
            "external_evaluator_approved": False, "retention_days": 30,
            "qualification_not_before": "REPLACE: RFC3339 start of approved qualification window",
            "readiness_receipts": {role: f"/pilot/evidence/readiness-{role}.json" for role in ROLES}}


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise PreflightError("timezone_required")
    return result.timestamp()


def binding(target):
    return _sha(json.dumps([target.endpoint_url, target.agent_id, target.user_arn,
                           target.account_id, target.region, target.log_group]))


def check_readiness(receipt, target, not_before, now, *, required_checks=10):
    if required_checks not in {3, 10}:
        raise PreflightError("invalid_readiness_requirement")
    if (receipt.get("schema") != "backbond-amazon-quick-readiness/v1"
            or receipt.get("live") is not True or receipt.get("status") != "transport_verified"
            or receipt.get("completed") != required_checks or len(receipt.get("checks", [])) != required_checks
            or receipt.get("target_binding_sha256") != binding(target)
            or receipt.get("pilot_id_sha256") != _sha(target.pilot_id)):
        raise PreflightError("fresh_identity_bound_readiness_required")
    start, end = timestamp(receipt["started_at"]), timestamp(receipt["finished_at"])
    if not not_before <= start <= end <= now or end >= target.expires_at:
        raise PreflightError("readiness_outside_approved_window")
    conversations, messages = set(), set()
    for row in receipt["checks"]:
        evidence = row["evidence"]
        if (row.get("exact_reply") is not True or evidence.get("source") != "aws_chat_logs"
                or row.get("reply_sha256") != evidence.get("response_sha256")):
            raise PreflightError("verified_provider_answers_required")
        for key in ("conversation_sha256", "message_sha256", "response_sha256"):
            value = evidence[key]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise PreflightError("invalid_readiness_hash")
        conversations.add(evidence["conversation_sha256"])
        messages.add(evidence["message_sha256"])
    if len(conversations) != required_checks or len(messages) != required_checks:
        raise PreflightError("independent_readiness_conversations_required")


def preflight(config, scope, *, judge_openrouter=False):
    if (scope.get("schema") != SCHEMA or scope.get("approved") is not True
            or scope.get("scope_id") != config.get("scope_id")):
        raise PreflightError("approved_matching_processing_scope_required")
    if scope.get("authentication") != "registered_user":
        raise PreflightError("identity_center_requires_separate_connector_qualification")
    if scope.get("region") not in REGIONS:
        raise PreflightError("agentic_region_not_qualified_check_current_aws_table")
    if scope.get("execution_location") not in {"backbond_aws", "customer_aws"}:
        raise PreflightError("explicit_execution_and_data_location_required")
    for key in ("customer_authorization_ref", "processing_agreement_ref"):
        value = scope.get(key)
        if not isinstance(value, str) or not value.strip() or value.startswith("REPLACE") or len(value) > 512:
            raise PreflightError("customer_authorization_and_processing_agreement_required")
    if (type(scope.get("retention_days")) is not int or not 1 <= scope["retention_days"] <= 30
            or type(scope.get("external_evaluator_approved")) is not bool
            or set(scope.get("readiness_receipts", {})) != set(ROLES)):
        raise PreflightError("complete_processing_boundary_required")
    if ("openrouter" in config.get("allowed_evaluators", []) or judge_openrouter) and scope["external_evaluator_approved"] is not True:
        raise PreflightError("external_evaluation_not_approved")
    if judge_openrouter and ("openrouter" not in config.get("allowed_evaluators", [])
                             or not os.environ.get("OPENROUTER_API_KEY", "").strip()):
        raise PreflightError("approved_evaluator_and_credential_required")
    bindings = validate_setup(config)
    preflight_targets(config, bindings)
    now = datetime.now(timezone.utc).timestamp()
    not_before = timestamp(scope["qualification_not_before"])
    if not_before > now:
        raise PreflightError("qualification_window_not_started")
    state = Path(os.environ["ARE_AMAZON_QUICK_STATE_DIR"])
    if not state.is_absolute() or not state.is_dir():
        raise PreflightError("durable_state_directory_required")
    database = state / "amazon-quick.sqlite3"
    # mode=ro never creates/reinitializes the durable ledger.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        pilot_ids, identity_keys, roles = set(), set(), {}
        for role in ROLES:
            identity = config["identities"][role]
            target = load_target(identity["endpoint"], os.environ.get(identity["token_env"], ""))
            if target.region != scope["region"]:
                raise PreflightError("processing_region_mismatch")
            pilot_ids.add(target.pilot_id)
            identity_key = _sha(target.account_id + target.region + target.user_arn)
            identity_keys.add(identity_key)
            row = db.execute("SELECT binding,calls,halted,inflight FROM pilots WHERE id=?", (target.pilot_id,)).fetchone()
            if not row or row[0] != binding(target) or row[2:] != (0, 0):
                raise PreflightError("ledger_missing_changed_or_requires_operator_reconciliation")
            lease = db.execute("SELECT until FROM leases WHERE identity=?", (identity_key,)).fetchone()
            if lease and lease[0] > now:
                raise PreflightError("dedicated_identity_busy")
            if row[1] < 10 or target.max_calls - row[1] < COUNTS[role]:
                raise PreflightError("insufficient_preserved_call_budget")
            path = Path(scope["readiness_receipts"][role])
            if not path.is_absolute():
                raise PreflightError("absolute_readiness_receipt_required")
            check_readiness(read_json(path), target, not_before, now)
            roles[role] = {"planned_calls": COUNTS[role], "consumed_calls": row[1],
                           "remaining_calls": target.max_calls - row[1]}
        if len(pilot_ids) != 3 or len(identity_keys) != 3:
            raise PreflightError("distinct_identity_and_pilot_budgets_required")
    return {"status": "operator_preflight_passed", "target_calls": 0, "judge_calls": 0,
            "planned_assessment_calls": len(CASES), "identities": roles,
            "evaluator": "openrouter" if judge_openrouter else "none",
            "evidence_grade": "operator_attested_setup", "backbond_score": "N/A"}


def runtime_check():
    checks = {"linux_x86_64": platform.system() == "Linux" and platform.machine() == "x86_64",
              "python_311": platform.python_version_tuple()[:2] == ("3", "11"),
              "non_root": hasattr(os, "geteuid") and os.geteuid() != 0,
              "aws_cli_v2": False, "chromium": False}
    if shutil.which("aws"):
        result = subprocess.run(["aws", "--version"], capture_output=True, timeout=15, check=False)
        checks["aws_cli_v2"] = result.returncode == 0 and result.stdout.startswith(b"aws-cli/2.")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # No network is used for the browser smoke check.
            page.route("**/*", lambda route: route.abort())
            page.set_content("<p>Quick pilot runtime</p>")
            checks["chromium"] = page.inner_text("p") == "Quick pilot runtime"
            browser.close()
    except Exception:
        pass
    return {"status": "runtime_verified" if all(checks.values()) else "incomplete", "checks": checks,
            "target_calls": 0, "judge_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("processing-example", "preflight", "runtime-check"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scope", type=Path)
    parser.add_argument("--judge-openrouter", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "processing-example":
            result = processing_example()
        elif args.operation == "runtime-check":
            result = runtime_check()
        else:
            result = preflight(read_json(args.config), read_json(args.scope), judge_openrouter=args.judge_openrouter)
        print(json.dumps(result, indent=2))
        return 1 if result.get("status") == "incomplete" else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "incomplete", "error": str(exc) if isinstance(exc, PreflightError) else "operator_preflight_failed",
                          "target_calls": 0, "judge_calls": 0, "backbond_score": "N/A"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
