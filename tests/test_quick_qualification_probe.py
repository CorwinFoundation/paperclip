"""Regression: a source-only agent can qualify without accepting arbitrary echoes."""
from copy import deepcopy
import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest

from evals.amazon_quick_assessment import digest, load_profile
from harness.amazon_quick import Target, _sha
from tools import amazon_quick_pilot as readiness
from tools.quick_concurrency_spike import plan
from tools import quick_concurrency_spike as spike_cli
from tools.quick_pilot_operations import check_readiness
from tools.quick_qualification_probe import fixture_probe, internal_knowledge_probe


@pytest.mark.parametrize("variant", ["hr_finance", "healthcare"])
def test_probe_uses_intact_common_fact_without_changing_the_agent_or_600_cases(variant):
    profile = load_profile(variant)
    original = deepcopy(profile)
    probe = fixture_probe(profile)
    assert probe.expected_reply == "20"
    assert probe.metadata["source_sha256"] == _sha(profile["fixture"]["sources"]["leave.txt"])
    waves = plan(profile["fixture"]["roles"], "fresh-reference", probe)
    cases = [c for _, batch, _ in waves for c in batch]
    assert [level for level, _, _ in waves] == [1, 2, 3, 5, 5]
    assert [len(batch) for _, batch, _ in waves] == [3, 6, 9, 15, 11]
    assert len(cases) == len({c.turns[0] for c in cases}) == 44
    assert all("leave.txt" in c.turns[0] and "reply with exactly:" not in c.turns[0] for c in cases)
    assert all(set(expected.values()) == {"20"} for _, _, expected in waves)
    assert profile == original


@pytest.mark.parametrize("defect", ["hash", "readers", "fact"])
def test_probe_rejects_tampered_or_not_common_source(defect):
    profile = load_profile("hr_finance")
    if defect == "hash":
        profile["fixture"]["sources"]["leave.txt"] += "changed"
    else:
        if defect == "readers":
            profile["fixture"]["source_readers"]["leave.txt"] = ["hr"]
        else:
            profile["fixture"]["facts"][0][1]["annual_leave_days"] = 200
        profile["fixture_sha256"] = digest(profile["fixture"])
        profile["profile_sha256"] = digest({k: v for k, v in profile.items() if k != "profile_sha256"})
    with pytest.raises(ValueError, match="intact_common"):
        fixture_probe(profile)


def test_internal_probe_uses_its_own_frozen_profile_and_common_source():
    from evals.internal_agent_knowledge import export_setup
    profile = export_setup()
    probe = internal_knowledge_probe()
    assert probe.expected_reply == "20"
    assert probe.metadata["profile_id"] == "internal-agent-knowledge@1"
    assert probe.metadata["profile_sha256"] == profile["profile_sha256"]
    assert probe.metadata["fixture_sha256"] == profile["fixture_sha256"]
    assert probe.metadata["source_sha256"] == _sha(profile["sources"]["leave.txt"])
    assert probe.metadata != fixture_probe(load_profile("hr_finance")).metadata
    assert export_setup() == profile


@pytest.mark.parametrize("defect", ["source", "readers"])
def test_internal_probe_rejects_changed_or_restricted_fixture(monkeypatch, defect):
    from evals import internal_agent_knowledge as internal
    if defect == "source":
        monkeypatch.setitem(internal.SOURCES, "leave.txt", "Annual leave entitlement is 200 days.")
    else:
        monkeypatch.setitem(internal.PERMISSION_MATRIX["source_readers"], "leave.txt", ["hr"])
    with pytest.raises(ValueError, match="intact_common_internal"):
        internal_knowledge_probe()


def test_internal_cli_rejects_three_checks_before_any_call_or_receipt(tmp_path, monkeypatch):
    called = Mock()
    monkeypatch.setattr(readiness, "AmazonQuickRunner", called)
    path = tmp_path / "never-created.json"
    with pytest.raises(SystemExit):
        readiness.main(["--endpoint", "https://synthetic.example.test/employee", "--receipt", str(path),
                        "--internal-knowledge", "--checks", "3", "--live"])
    called.assert_not_called()
    assert not path.exists()


@pytest.mark.parametrize("options", [[], ["--internal-knowledge"], ["--fixture-variant", "hr_finance", "--profile-version", "9.9"]])
def test_invalid_versioned_probe_stops_before_receipt_or_runner(tmp_path, monkeypatch, options):
    called = Mock()
    monkeypatch.setattr(readiness, "AmazonQuickRunner", called)
    path = tmp_path / "never-created.json"
    version = [] if "--profile-version" in options else ["--profile-version", "1.3"]
    with pytest.raises(SystemExit):
        readiness.main(["--endpoint", "https://synthetic.example.test/employee", "--receipt", str(path),
                        *options, *version, "--live"])
    called.assert_not_called()
    assert not path.exists()


@pytest.mark.parametrize("mode,checks", [("v12", 3), ("v13", 3), ("v13", 10), ("internal", 10)])
@pytest.mark.parametrize("reply", ["20", "Sorry, I am unable to help with your request.", "200"])
@pytest.mark.parametrize("native_status", ["success", "request_blocked", "no_answer_found"])
def test_live_probe_keeps_strict_answer_and_provider_prompt_binding(tmp_path, monkeypatch, reply, native_status, mode, checks):
    target = Target.parse(dict(approved=True, endpoint_url="https://synthetic.example.test/employee",
        account_id="111122223333", region="us-east-1", agent_id="synthetic-agent",
        user_arn="arn:aws:quicksight:us-east-1:111122223333:user/default/employee", log_group="/synthetic",
        token_sha256=_sha("synthetic"), pilot_id="synthetic-pilot", expires_at="2099-01-01T00:00:00Z",
        max_calls=100, allowed_browser_hosts=["synthetic.example.test"]))
    runner, receipts, prompts = Mock(), [], []
    runner.target = target
    runner.model_id.return_value = "amazon_quick:" + _sha("synthetic-agent")
    def complete(prompt):
        prompts.append(prompt)
        receipts.append({"source": "aws_chat_logs", "native_status_code": native_status,
            "response_sha256": _sha(reply), "prompt_sha256": _sha(prompt),
            "conversation_sha256": _sha("conversation" + str(len(prompts))),
            "message_sha256": _sha("message" + str(len(prompts)))})
        return reply
    runner.complete.side_effect = complete
    runner.evidence_receipts.side_effect = lambda: tuple(receipts)
    monkeypatch.setattr(readiness, "AmazonQuickRunner", Mock(return_value=runner))
    path = tmp_path / "readiness.json"
    before = datetime.now(timezone.utc).timestamp()
    options = ["--internal-knowledge"] if mode == "internal" else ["--fixture-variant", "hr_finance"]
    if mode == "v13":
        options += ["--profile-version", "1.3"]
    result = readiness.main(["--endpoint", target.endpoint_url, "--receipt", str(path),
                            "--checks", str(checks), *options, "--live"])
    receipt = json.loads(path.read_text())
    assert all("leave.txt" in p for p in prompts)
    assert runner.close.call_count == 1
    if reply != "20" or native_status != "success":
        assert result == 1 and receipt["completed"] == 1
        assert receipt["status"] == "incomplete" and len(prompts) == 1
        return
    assert result == 0 and len(prompts) == checks and len(set(prompts)) == checks
    probe = internal_knowledge_probe() if mode == "internal" else fixture_probe(
        load_profile("hr_finance", "1.3" if mode == "v13" else "1.2"))
    check_readiness(receipt, target, before, datetime.now(timezone.utc).timestamp(), required_checks=checks, probe=probe)
    for defect in ("profile", "profile_version", "prompt", "answer", "conversation", "blocked_status", "missing_status"):
        bad = deepcopy(receipt)
        if defect == "profile":
            bad["probe"]["source_sha256"] = _sha("different source")
        elif defect == "profile_version":
            bad["probe"] = fixture_probe(load_profile("hr_finance", "1.2" if mode == "v13" else "1.3")).metadata
        elif defect == "prompt":
            bad["checks"][0]["evidence"]["prompt_sha256"] = _sha("different request")
        elif defect == "answer":
            bad["checks"][0]["normalized_reply_sha256"] = _sha("refusal")
        elif defect == "blocked_status":
            bad["checks"][0]["evidence"]["native_status_code"] = "request_blocked"
        elif defect == "missing_status":
            del bad["checks"][0]["evidence"]["native_status_code"]
        else:
            bad["checks"][1]["evidence"]["conversation_sha256"] = bad["checks"][0]["evidence"]["conversation_sha256"]
        with pytest.raises(ValueError):
            check_readiness(bad, target, before, datetime.now(timezone.utc).timestamp(), required_checks=checks, probe=probe)


@pytest.mark.parametrize("native_status", ["request_blocked", "no_answer_found", None])
def test_spike_rejects_blocked_or_missing_status_even_when_probe_reply_matches(tmp_path, monkeypatch, native_status):
    profile = load_profile("hr_finance")
    roles = profile["fixture"]["roles"]
    scope = {"authorization_id": "synthetic-test", "max_calls": 100, "identities": {}}
    for role in roles:
        name = "SYNTHETIC_SPIKE_" + role.upper()
        scope["identities"][role] = {"token_env": name}
        monkeypatch.setenv(name, "synthetic-unused-token")
    monkeypatch.setattr(spike_cli, "preflight", lambda *a, **k: ({r: object() for r in roles}, {}))

    class Runner:
        def __init__(self, *a, **k):
            pass

        async def run(self, cases):
            return {"status": "capture_complete", "initialized_together": len(cases), "elapsed_seconds": 1,
                "api_calls": {}, "browser_launches": 1, "sweeps": {}, "results": [
                    {"case_id": c.case_id, "role": c.role, "status": "observed", "turns": [
                        {"reply": "20", "transport": {"prompt_sha256": _sha(c.turns[0]),
                            "response_sha256": _sha("20"), "native_status_code": native_status if i == 0 else "success"}}]}
                    for i, c in enumerate(cases)]}

    monkeypatch.setattr(spike_cli, "QuickBatchRunner", Runner)
    with pytest.raises(ValueError, match="spike_incomplete"):
        asyncio.run(spike_cli.spike(scope, profile, tmp_path, {"waves": []}))
    receipt = json.loads((tmp_path / "spike.json").read_text())
    assert len(receipt["waves"]) == 1
    assert receipt["waves"][0]["verified"] is False
    assert receipt["waves"][0]["native_successful_replies"] == 2
