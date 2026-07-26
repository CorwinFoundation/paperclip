import json
from pathlib import Path

import pytest

from tools.registered_case_executor import BudgetExceeded, ScanBudgetLedger
from tools.wave3_executor import AppendOnlyScrubbedEvidence, wave3_budget
from tools.wave3_first100 import MANIFEST, SELECTION, load_jsonl, validate


def test_frozen_selection_and_pins() -> None:
    assert validate(load_jsonl(SELECTION), json.loads(MANIFEST.read_text(encoding="utf-8"))) == {
        "count": 100,
        "unique_stable_ids": 100,
        "duplicate_hashes": 0,
        "aliases": 0,
    }


def test_exact_wave3_budget_hard_stops() -> None:
    budget = wave3_budget()
    assert (budget.max_target_calls, budget.max_judge_calls) == (136, 100)
    assert budget.max_estimated_judge_cost_usd == 100
    assert budget.max_wall_clock_seconds == 28800
    ledger = ScanBudgetLedger(budget)
    ledger.target_calls = 136
    with pytest.raises(BudgetExceeded, match="max_target_calls"):
        ledger.reserve_target_call()
    ledger.judge_calls = 100
    with pytest.raises(BudgetExceeded, match="max_judge_calls"):
        ledger.reserve_judge_call("x")
    ledger.judge_calls = 0
    ledger.estimated_judge_cost_usd = 100
    with pytest.raises(BudgetExceeded, match="max_estimated_judge_cost_usd"):
        ledger.reserve_judge_call("x")
    ledger.started_monotonic -= 28801
    with pytest.raises(BudgetExceeded, match="max_wall_clock_seconds"):
        ledger.reserve_target_call()


def test_append_only_scrubbed_retention(tmp_path: Path) -> None:
    store = AppendOnlyScrubbedEvidence(tmp_path)
    path = store.write(
        "case-1",
        {"email": "person@example.com", "proprietary_prompt": "secret", "customer_weights": {"x": 1}, "result": "ok"},
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["email"] == "[REDACTED]"
    assert "proprietary_prompt" not in body and "customer_weights" not in body
    assert body["retention"] == {"mode": "append_only", "years": 7}
    assert len(body["redacted_evidence_sha256"]) == 64
    with pytest.raises(FileExistsError):
        store.write("case-1", {"result": "overwrite"})
