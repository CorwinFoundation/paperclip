"""BEAAA-15883 multi-turn harness -> Passive Monitor determination/rescan lane.

Rule-execution fixture: no BackBond Score, rubric, judge, or API-contract field
changes. The test proves existing multi-turn/tool-call evidence can be consumed
by the existing Determination Engine and can drive the existing rescan-required
automatic-decision annotation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from determination.engine import DeterminationEngine
from determination.multiturn_rescan_lane import (
    CORPUS_VERSION,
    MODEL_VERSIONS,
    PROBE_SET_VERSION,
    PROMPT_VERSION,
    RUBRIC_POINT_ID,
    SCANNER_VERSION,
    SEED,
    envelope_with_determination_findings,
    evidence_packet_from_behavioral_observation,
)
from determination.records import DECISION_COVERED_CANDIDATE
from scripts import behavioral_golden_fixtures as bgf
from underwriting.automatic_decision import decide

REPO = Path(__file__).resolve().parents[1]
RETELL = REPO / "artifacts" / "beaaa-8225" / "consolidated_envelope_seed0.json"


def _fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_ready(envelope: dict, *, score: int = 91, band: str = "A") -> dict:
    out = copy.deepcopy(envelope)
    out["are_total"] = score
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
    out["uncovered_dimensions"] = []
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


def test_multiturn_tool_call_evidence_feeds_determination_and_rescan_trigger():
    observation = bgf._observe_warranty_authority_bound()
    assert observation["pass_k"] is True
    assert observation["scored"] is None
    assert len(observation["evidence_refs"]) == 15

    packet = evidence_packet_from_behavioral_observation(observation)
    records = DeterminationEngine(emit=False).evaluate(packet)
    assert len(records) == 1
    record = records[0]
    assert record.final_decision == DECISION_COVERED_CANDIDATE
    assert record.peril == "unauthorized_action"
    assert record.triggered_rule_id == "DET-UNA-001"
    assert record.audit == {
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "collector_version": "passive-monitor-fixture-collector@1",
        "manifest_signature_algorithm": "fixture-sha256",
    }
    assert record.source_spans[0]["trace_id"] == "trace-beaaa-15883"

    envelope = envelope_with_determination_findings(
        _policy_ready(_fixture(RETELL)),
        [record.to_dict()],
        evidence_refs=observation["evidence_refs"],
    )
    decision = decide(envelope)
    fired = {rule.rule_id: rule for rule in decision.fired_rules}
    assert "PERIL-001" in fired
    assert fired["PERIL-001"].rescan_required is True
    assert decision.real_policy_bound is False
    assert decision.audit["scanner_version"] == SCANNER_VERSION
    assert decision.audit["corpus_version"] == CORPUS_VERSION
    assert decision.audit["model_versions"] == MODEL_VERSIONS
    assert decision.audit["seed"] == SEED
    assert envelope["probe-set-version"] == PROBE_SET_VERSION
    assert envelope["probe_set_version"] == PROBE_SET_VERSION
    assert envelope["evidence_refs"][0]["rubric_point_id"] == RUBRIC_POINT_ID
    assert PROMPT_VERSION.endswith("@fixture")
