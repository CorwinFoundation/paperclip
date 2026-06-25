"""BEAAA-8541 automatic underwriting decision-engine tests.

The engine consumes BEAAA-8539's policy table as data and emits
underwriting-side annotations only. Scanner scoring fields remain untouched.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from underwriting.automatic_decision import canonical_decision_bytes, decide, load_policy

REPO = Path(__file__).resolve().parents[1]
RETELL = REPO / "artifacts" / "beaaa-8225" / "consolidated_envelope_seed0.json"
RASA = REPO / "artifacts" / "beaaa-8389" / "consolidated_envelope_seed0.json"


def _fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_ready(envelope: dict, *, score: int | None = None, band: str | None = None) -> dict:
    out = copy.deepcopy(envelope)
    if score is not None:
        out["are_total"] = score
    if band is not None:
        out["band"] = band
    out.setdefault(
        "subscores",
        {
            "GOVERNANCE": 22.0,
            "ROBUSTNESS": 22.0,
            "BIAS_FAIRNESS": 22.0,
            "OPS": 22.0,
        },
    )
    out["applicability_record"] = {
        "measured_dims_count": 22,
        "na_dims_count": 1,
        "pillars_with_all_na": [],
    }
    out["coverage_fraction"] = {
        "overall": 0.92,
        "by_peril": {
            "wrong_recipient": 0.90,
            "unauthorized_commitment": 0.90,
            "data_leak": 0.90,
            "hallucination": 0.90,
            "prompt_injection": 0.90,
            "regulatory_response": 0.90,
        },
    }
    out["governance_evidence"] = {"eval_ledger_p4_5": "present"}
    out["peril_findings"] = []
    out["rate_context"] = {
        "state": "NY",
        "class": "contact_center_agent",
        "limit": 50000,
        "deductible": 5000,
        "annual_agent_actions": 1000000,
        "product_tier": "Silver",
        "filed_rate_available": True,
    }
    out["appetite"] = {"prohibited_use_case": False}
    out["scan_mode"] = "offline_replay"
    out["spread_per_dim_worst"] = 0.0
    return out


def _hashes_for_n5(envelope: dict) -> set[str]:
    return {
        hashlib.sha256(canonical_decision_bytes(decide(envelope))).hexdigest()
        for _ in range(5)
    }


def test_policy_table_loads_eighteen_rules():
    policy = load_policy()
    assert policy["policy"]["version"] == "0.1"
    assert len(policy["rules"]) == 18


def test_terminal_preflight_pin_rule_precedes_score_threshold():
    envelope = _policy_ready(_fixture(RETELL), score=92, band="A")
    del envelope["scanner_version"]

    decision = decide(envelope)

    assert decision.policy_class == "D"
    assert decision.engine_outcome == "DECLINE_STANDARD_LADDER"
    assert [rule.rule_id for rule in decision.fired_rules] == ["EV-001"]
    assert decision.bind_blockers == ("EV.RUN_PIN_MISSING",)


def test_band_score_mismatch_is_terminal_bind_blocker():
    envelope = _policy_ready(_fixture(RETELL), score=90, band="C")

    decision = decide(envelope)

    assert decision.policy_class == "D"
    assert decision.fired_rules[0].rule_id == "EV-007"
    assert decision.fired_rules[0].reason_code == "EV.BAND_SCORE_MISMATCH"


def test_b_and_c_always_route_to_human_underwriter():
    b_decision = decide(_policy_ready(_fixture(RETELL), score=75, band="B"))
    c_decision = decide(_policy_ready(_fixture(RETELL), score=64, band="C"))

    assert b_decision.policy_class == "B"
    assert b_decision.human_review_required is True
    assert b_decision.auto_resolved is False
    assert b_decision.human_review_owner == "operational_underwriter"
    assert b_decision.premium_band["status"] == "advisory_only"

    assert c_decision.policy_class == "C"
    assert c_decision.human_review_required is True
    assert c_decision.auto_resolved is False
    assert c_decision.human_review_owner == "operational_underwriter"
    assert c_decision.premium_band["status"] == "remediation_only"


def test_phase1_autonomy_flags_for_clear_a_and_clear_d():
    a_decision = decide(_policy_ready(_fixture(RETELL), score=91, band="A"))
    d_decision = decide(_policy_ready(_fixture(RASA), score=44, band="D"))

    assert a_decision.policy_class == "A"
    assert a_decision.auto_resolved is True
    assert a_decision.human_review_required is True
    assert a_decision.real_policy_bound is False
    assert a_decision.premium_band["quote_bound"] is False

    assert d_decision.policy_class == "D"
    assert d_decision.auto_resolved is True
    assert d_decision.human_review_required is False
    assert d_decision.real_policy_bound is False
    assert d_decision.premium_band["status"] == "no_standard_quote"


def test_bind_blockers_and_referrals_emit_evidence_linked_rationale():
    envelope = _policy_ready(_fixture(RETELL), score=88, band="A")
    envelope["governance_evidence"]["eval_ledger_p4_5"] = "absent"

    decision = decide(envelope)

    assert decision.policy_class == "B"
    fired = {rule.rule_id: rule for rule in decision.fired_rules}
    assert "GOV-001" in fired
    assert fired["GOV-001"].reason_code == "GOV.EVAL_LEDGER_P4_5_MISSING"
    assert fired["GOV-001"].input_paths == ("governance_evidence.eval_ledger_p4_5",)
    assert fired["GOV-001"].observed_values == {"governance_evidence.eval_ledger_p4_5": "absent"}
    assert fired["GOV-001"].evidence_refs
    assert fired["GOV-001"].rescan_required is True


def test_real_retell_and_rasa_fixtures_produce_policy_correct_outputs():
    retell = _policy_ready(_fixture(RETELL))
    rasa = _policy_ready(_fixture(RASA))

    retell_decision = decide(retell)
    rasa_decision = decide(rasa)

    assert retell["agent_id"].startswith("retell")
    assert retell_decision.policy_class == "B"
    assert retell_decision.engine_outcome == "REFER_TO_UNDERWRITER_BIND_BLOCKER"
    assert retell_decision.fired_rules[-1].rule_id == "SCORE-002"

    assert rasa["agent_id"].startswith("rasa")
    assert rasa_decision.policy_class == "D"
    assert rasa_decision.engine_outcome == "DECLINE_STANDARD_LADDER"
    assert any(rule.rule_id == "SCORE-004" for rule in rasa_decision.fired_rules)


def test_n5_determinism_hash_spread_zero_on_real_fixtures():
    retell_hashes = _hashes_for_n5(_policy_ready(_fixture(RETELL)))
    rasa_hashes = _hashes_for_n5(_policy_ready(_fixture(RASA)))

    assert len(retell_hashes) == 1
    assert len(rasa_hashes) == 1
