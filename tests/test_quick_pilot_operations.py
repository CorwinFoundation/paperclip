"""No-network qualification gates and packaged pipeline regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

from harness import amazon_quick as quick
from tools import internal_agent_pilot as pilot
from tools import quick_pilot_operations as ops
from tools import quick_pilot_run as run_cli
from tools import quick_pilot_report as report
from tools import amazon_quick_pilot as readiness_cli
from tools.build_amazon_quick_worker import build_context
from tools.quick_qualification_probe import fixture_probe, internal_knowledge_probe


@pytest.fixture
def configured(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("ARE_AMAZON_QUICK_ENABLED", "1")
    monkeypatch.setenv("ARE_AMAZON_QUICK_STATE_DIR", str(state))
    config = pilot.example_config()
    config.update(approved=True, expires_at="2099-01-01T00:00:00Z", agent_configuration_ref="operator://agent",
                  permission_matrix_ref="operator://grants", source_setup_ref="operator://sync")
    for name in config["sources"]:
        config["sources"][name]["resource_id"] = "provider-id:" + name
    scope = ops.processing_example()
    now = datetime.now(timezone.utc)
    scope.update(approved=True, region="us-east-1", authentication="registered_user", execution_location="backbond_aws",
                 customer_authorization_ref="customer://approval", processing_agreement_ref="customer://processing",
                 qualification_not_before=(now - timedelta(hours=1)).isoformat())
    rows, receipts = [], {}
    probe = internal_knowledge_probe()
    for role in ops.ROLES:
        token = "random-token-for-" + role + "-x" * 32
        identity = config["identities"][role]
        monkeypatch.setenv(identity["token_env"], token)
        row = dict(approved=True, endpoint_url=identity["endpoint"], account_id="111122223333", region="us-east-1",
                   agent_id="agent-pilot", user_arn=identity["user_arn"], log_group="/backbond/pilot",
                   token_sha256=quick._sha(token), pilot_id="pilot-" + role, expires_at="2099-01-01T00:00:00Z",
                   allowed_browser_hosts=["pilot.customer.example"], max_calls=10 + ops.COUNTS[role])
        rows.append(row)
        target = quick.Target.parse(row)
        ledger = quick.Ledger(target)
        ledger.db.execute("UPDATE pilots SET calls=10 WHERE id=?", (target.pilot_id,))
        ledger.db.commit()
        ledger.close()
        receipt = {"schema": "backbond-amazon-quick-readiness/v1", "live": True,
                   "status": "transport_verified", "completed": 10,
                   "probe": probe.metadata, "probe_run_id": "0123456789abcdef",
                   "target_binding_sha256": ops.binding(target), "pilot_id_sha256": quick._sha(target.pilot_id),
                   "started_at": (now - timedelta(minutes=20)).isoformat(),
                   "finished_at": (now - timedelta(minutes=1)).isoformat(), "checks": []}
        for index in range(10):
            reply_hash = quick._sha("20")
            prompt = probe.prompt(f"QUICK-PILOT-{receipt['probe_run_id']}-{index}")
            receipt["checks"].append({"index": index + 1, "exact_reply": True, "reply_sha256": reply_hash,
                                      "normalized_reply_sha256": reply_hash,
                                      "evidence": {"source": "aws_chat_logs", "response_sha256": reply_hash,
                                                   "prompt_sha256": quick._sha(prompt),
                                                   "message_sha256": quick._sha(role + "message" + str(index)),
                                                   "conversation_sha256": quick._sha(role + "conversation" + str(index))}})
        path = tmp_path / (role + ".json")
        path.write_text(json.dumps(receipt))
        scope["readiness_receipts"][role] = str(path)
        receipts[role] = receipt
    registry = tmp_path / "targets.json"
    registry.write_text(json.dumps({"schema": quick.SCHEMA, "targets": rows}))
    monkeypatch.setenv("ARE_AMAZON_QUICK_TARGETS_FILE", str(registry))
    return config, scope, receipts, state


def test_preflight_reads_preserved_ledger_without_network_or_calls(configured, monkeypatch):
    config, scope, _, state = configured
    before = (state / "amazon-quick.sqlite3").read_bytes()
    monkeypatch.setattr("socket.socket", Mock(side_effect=AssertionError("no network")))
    result = ops.preflight(config, scope)
    assert result["status"] == "operator_preflight_passed"
    assert result["planned_assessment_calls"] == 18
    assert result["target_calls"] == result["judge_calls"] == 0
    assert result["identities"]["employee"]["remaining_calls"] == 12
    assert (state / "amazon-quick.sqlite3").read_bytes() == before


def test_configuration_receipt_includes_exact_identity_binding_without_aws(configured, tmp_path, monkeypatch):
    config, _, _, _ = configured
    monkeypatch.setattr(quick.AwsCli, "call", Mock(side_effect=AssertionError("no AWS call")))
    identity = config["identities"]["employee"]
    path = tmp_path / "configuration-receipt.json"
    assert readiness_cli.main(["--endpoint", identity["endpoint"], "--token-env", identity["token_env"],
                               "--receipt", str(path)]) == 0
    receipt = json.loads(path.read_text())
    target = quick.load_target(identity["endpoint"], os.environ[identity["token_env"]])
    assert receipt["target_binding_sha256"] == ops.binding(target)
    assert receipt["pilot_id_sha256"] == quick._sha(target.pilot_id)
    assert receipt["status"] == "configuration_validated_only"
    assert receipt["started_at"] <= receipt["finished_at"]


@pytest.mark.parametrize("field,value", [("authentication", "identity_center"), ("region", "il-central-1"),
    ("approved", False), ("customer_authorization_ref", "REPLACE: approval"),
    ("execution_location", "hostinger"), ("retention_days", 0)])
def test_customer_scope_gates(configured, field, value):
    config, scope, _, _ = configured
    scope[field] = value
    with pytest.raises(ValueError):
        ops.preflight(config, scope)


def test_external_judge_requires_both_boundaries_and_credential(configured, monkeypatch):
    config, scope, _, _ = configured
    config["allowed_evaluators"] = ["none", "openrouter"]
    with pytest.raises(ValueError, match="not_approved"):
        ops.preflight(config, scope)
    scope["external_evaluator_approved"] = True
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="credential"):
        ops.preflight(config, scope, judge_openrouter=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    assert ops.preflight(config, scope, judge_openrouter=True)["evaluator"] == "openrouter"


@pytest.mark.parametrize("defect", ["old_receipt", "wrong_user", "wrong_pilot", "reused_conversation", "non_live", "stale", "not_exact"])
def test_fresh_readiness_is_bound_to_each_identity(configured, defect):
    config, scope, receipts, _ = configured
    row = receipts["employee"]
    if defect == "old_receipt":
        del row["target_binding_sha256"]
    elif defect == "wrong_user":
        row["target_binding_sha256"] = receipts["hr"]["target_binding_sha256"]
    elif defect == "wrong_pilot":
        row["pilot_id_sha256"] = "0" * 64
    elif defect == "reused_conversation":
        row["checks"][1]["evidence"]["conversation_sha256"] = row["checks"][0]["evidence"]["conversation_sha256"]
    elif defect == "non_live":
        row["live"] = False
    elif defect == "stale":
        row["started_at"] = "2020-01-01T00:00:00Z"
    else:
        row["checks"][0]["exact_reply"] = False
    Path(scope["readiness_receipts"]["employee"]).write_text(json.dumps(row))
    with pytest.raises(ValueError):
        ops.preflight(config, scope)


@pytest.mark.parametrize("defect", ["generic_echo", "other_profile", "source", "prompt", "answer", "three_checks"])
def test_internal_preflight_requires_ten_source_bound_checks(configured, defect):
    config, scope, receipts, _ = configured
    row = deepcopy(receipts["employee"])
    if defect == "generic_echo":
        del row["probe"]
    elif defect == "other_profile":
        from evals.amazon_quick_assessment import load_profile
        row["probe"] = fixture_probe(load_profile("hr_finance")).metadata
    elif defect == "source":
        row["probe"]["source_sha256"] = quick._sha("different source")
    elif defect == "prompt":
        row["checks"][0]["evidence"]["prompt_sha256"] = quick._sha("different question")
    elif defect == "answer":
        row["checks"][0]["normalized_reply_sha256"] = quick._sha("refusal")
    else:
        row["completed"] = 3
        row["checks"] = row["checks"][:3]
    Path(scope["readiness_receipts"]["employee"]).write_text(json.dumps(row))
    with pytest.raises(ValueError):
        ops.preflight(config, scope)


@pytest.mark.parametrize("update", ["halted=1", "inflight=1", "calls=11", "calls=0"])
def test_uncertainty_and_consumed_budgets_cannot_be_cleared_by_preflight(configured, update):
    config, scope, _, state = configured
    with sqlite3.connect(state / "amazon-quick.sqlite3") as db:
        # Fixed test parameters, never operator-supplied SQL.
        db.execute("UPDATE pilots SET " + update + " WHERE id='pilot-employee'")
    with pytest.raises(ValueError):
        ops.preflight(config, scope)


def test_run_wrapper_cannot_submit_after_failed_preflight(tmp_path, monkeypatch):
    for name in ("config", "scope"):
        (tmp_path / name).write_text("{}")
    called = Mock()
    monkeypatch.setattr(run_cli, "pilot_main", called)
    assert run_cli.main(["--config", str(tmp_path / "config"), "--scope", str(tmp_path / "scope"),
                         "--output", str(tmp_path / "new")]) == 2
    called.assert_not_called()
    assert not (tmp_path / "new").exists()


def test_run_wrapper_sanitizes_ledger_errors_and_rejects_ephemeral_judge_budget(tmp_path, monkeypatch, capsys):
    for name in ("config", "scope"):
        (tmp_path / name).write_text("{}")
    called = Mock()
    monkeypatch.setattr(run_cli, "pilot_main", called)
    monkeypatch.setattr(run_cli, "preflight", Mock(side_effect=sqlite3.OperationalError("private database details")))
    command = ["--config", str(tmp_path / "config"), "--scope", str(tmp_path / "scope"),
               "--output", str(tmp_path / "new")]
    assert run_cli.main(command) == 2
    assert "private database details" not in capsys.readouterr().out
    monkeypatch.setenv("ARE_AMAZON_QUICK_STATE_DIR", str(tmp_path / "state"))
    check = Mock()
    monkeypatch.setattr(run_cli, "preflight", check)
    assert run_cli.main(command + ["--judge-openrouter", "--judge-budget-db", str(tmp_path / "ephemeral.sqlite3")]) == 2
    check.assert_not_called()
    called.assert_not_called()


def test_packaged_import_closure_and_grading_report_pipeline(tmp_path):
    root = Path(__file__).resolve().parents[1]
    context = tmp_path / "context"
    manifest = build_context(root, context)
    assert not any(".env" in n or "state/" in n or ".aws" in n for n in manifest["files"])
    env = dict(os.environ, PYTHONPATH="", QUICK_PILOT_SCRATCH=str(tmp_path))
    result = subprocess.run([sys.executable, "-I", str(context / "deploy/amazon-quick/verify-fixtures.py")],
                            cwd=context, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"packaged_fixture_pipeline_verified"' in result.stdout


def test_report_preserves_fixture_boundary_and_rejects_changed_summary(tmp_path):
    output = tmp_path / "fixture"
    assert pilot.main(["--fixture", "resistant", "--output", str(output)]) == 0
    run = json.loads((output / "run.json").read_text())
    text = report.render(run)
    assert "SCRIPTED FIXTURE" in text and "BackBond Score: N/A" in text
    assert "BB-PAY-4R8X" not in text
    run["summary"]["backbond_score"] = 99
    run["run_sha256"] = pilot.digest({k: v for k, v in run.items() if k != "run_sha256"})
    with pytest.raises(ValueError, match="summary"):
        report.render(run)


def test_customer_role_trust_and_policy_are_exact():
    path = Path(__file__).resolve().parents[1] / "deploy/amazon-quick/customer-access.template.json"
    template = json.loads(path.read_text())
    props = template["Resources"]["PilotRuntimeRole"]["Properties"]
    trust = props["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"]["AWS"] == {"Ref": "BackBondExecutionRoleArn"}
    assert "sts:ExternalId" in trust["Condition"]["StringEquals"]
    assert trust["Condition"]["DateLessThan"]["aws:CurrentTime"] == {"Ref": "PilotExpiresAt"}
    grants = props["Policies"][0]["PolicyDocument"]["Statement"]
    assert {r["Action"] for r in grants} == {"quicksight:GenerateEmbedUrlForRegisteredUser", "logs:FilterLogEvents"}
    assert len(grants[0]["Resource"]) == 3
    assert grants[0]["Condition"]["Null"]["quicksight:AllowedEmbeddingDomains"] == "false"
    assert all(r["Condition"]["DateLessThan"]["aws:CurrentTime"] == {"Ref": "PilotExpiresAt"} for r in grants)
