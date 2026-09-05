"""Controlled-traffic proof for the shared exposure listener pack."""
from __future__ import annotations
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from appliance.pipeline import normalize
from appliance.rules.engine import RuleSet


def test_secret_egress_emits_shared_id():
    pack = os.path.join(ROOT, "appliance", "rules", "exposure_agent_pack.yaml")
    rules = RuleSet.from_yaml(pack)
    event = normalize({
        "type": "tool_call", "id": "controlled-secret-egress", "tenant_id": "fixture",
        "agent_id": "fixture-agent", "tool_name": "http_post", "secret_access": True,
        "egress_authorized": False,
    })
    findings = rules.determinations(event)
    assert [f["rule_id"] for f in findings] == ["exposure.secret_egress"]


def test_clean_tool_call_has_no_shared_exposure():
    pack = os.path.join(ROOT, "appliance", "rules", "exposure_agent_pack.yaml")
    rules = RuleSet.from_yaml(pack)
    event = normalize({
        "type": "tool_call", "id": "controlled-clean", "tenant_id": "fixture",
        "agent_id": "fixture-agent", "tool_name": "http_get", "secret_access": False,
        "egress_authorized": True, "scope_authorized": True, "policy_allowed": True,
    })
    assert rules.determinations(event) == []
