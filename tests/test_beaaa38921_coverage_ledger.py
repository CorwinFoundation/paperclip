import copy
import json
from pathlib import Path

import pytest

from tools.coverage_ledger import project, render_markdown, validate_ledger


LEDGER = Path("artifacts/beaaa-38921/coverage-ledger-2026-10-09.json")


def load():
    return json.loads(LEDGER.read_text())


def test_inventory_reconciliation_and_no_additive_total():
    result = project(load())
    by_pack = {row["binding"]["pack_id"]: row for row in result["bindings"]}
    assert by_pack["conversation-2350"]["inventory"] == {
        "registered_unique_scenarios": 2350, "selectable": 2350,
        "selected_unique_scenarios": 2350, "evaluation_rows": 2350,
        "attempt_rows": 2350, "decisive": 114, "pass": 64, "fail": 50,
        "flaky": 6, "could_not_run": 2230, "calibrated": 0,
    }
    assert by_pack["action-494"]["inventory"]["registered_unique_scenarios"] == 494
    assert by_pack["action-494"]["inventory"]["evaluation_rows"] == 579
    assert by_pack["action-494"]["inventory"]["attempt_rows"] == 494
    assert "total" not in result
    assert result["inventories_are_additive"] is False


def test_projection_keeps_trace_selection_separate_from_scenarios():
    result = project(load())
    trace = next(row for row in result["bindings"] if row["binding"]["pack_id"] == "trace-binding-1181")
    assert trace["inventory"]["registered_unique_scenarios"] == 1181
    assert trace["inventory"]["selectable"] == 480


def test_invalid_outcome_reconciliation_fails_closed():
    ledger = copy.deepcopy(load())
    ledger["bindings"][0]["counts"]["could_not_run"] -= 1
    with pytest.raises(ValueError, match="attempt_rows must equal"):
        validate_ledger(ledger)


def test_scenario_and_attempt_ids_cannot_share_a_namespace():
    ledger = copy.deepcopy(load())
    ledger["bindings"][0]["attempt_id_namespace"] = ledger["bindings"][0]["scenario_id_namespace"]
    with pytest.raises(ValueError, match="namespaces must differ"):
        validate_ledger(ledger)


def test_report_is_concise_and_does_not_claim_a_supported_total():
    report = render_markdown(project(load()))
    assert "No supported-total sum is reported" in report
    assert "64 | 50 | 6 | 2230" in report
    assert "306 | 134 | 54 | 0" in report


def test_refuse_everything_control_cannot_pass_the_aggregate():
    result = project(load())
    control = result["aggregate_controls"][0]
    assert control["control_type"] == "refuse_everything"
    assert control["pooled_pass_rate"] == 0.636
    assert control["authorized_task_completion_rate"] == 0.0
    assert control["forbidden_action_resistance_rate"] == 1.0
    assert control["aggregate_disposition"] == "fail"
    assert "authorized-task completion 0.0%" in render_markdown(result)


def test_refuse_everything_control_fails_closed_if_marked_pass():
    ledger = copy.deepcopy(load())
    ledger["aggregate_controls"][0]["aggregate_disposition"] = "pass"
    with pytest.raises(ValueError, match="must fail"):
        validate_ledger(ledger)


def test_refuse_everything_control_cannot_be_removed():
    ledger = copy.deepcopy(load())
    ledger["aggregate_controls"] = []
    with pytest.raises(ValueError, match="exactly one"):
        validate_ledger(ledger)


def test_action_source_identities_do_not_claim_unverified_semantic_uniqueness():
    result = project(load())
    action = next(row for row in result["bindings"] if row["binding"]["pack_id"] == "action-494")
    semantic = action["semantic_uniqueness"]
    assert semantic["qualified_behavioral_identities"] == 4
    assert semantic["unqualified_source_identities"] == 490
    assert semantic["status"] == "partially_verified"
    assert set(semantic["unmeasured_lifecycle_surfaces"]) == {
        "delegation", "approval_freshness", "revocation", "retry", "interruption",
    }
    assert "4 qualified / 490 unqualified" in render_markdown(result)


def test_semantic_identity_counts_must_reconcile():
    ledger = copy.deepcopy(load())
    ledger["bindings"][1]["semantic_uniqueness"]["qualified_behavioral_identities"] = 5
    with pytest.raises(ValueError, match="must reconcile"):
        validate_ledger(ledger)
