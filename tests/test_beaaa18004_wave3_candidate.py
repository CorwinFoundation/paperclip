import json
from pathlib import Path

import pytest

from tools.registered_case_executor import BudgetExceeded, ScanBudgetLedger
from tools.wave3_executor import (
    AppendOnlyScrubbedEvidence,
    materialize_prompts,
    selected_rows,
    verify_manifest,
    wave3_budget,
    write_manifest,
)
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
    assert (budget.max_target_calls, budget.max_judge_calls) == (172, 100)
    assert budget.max_estimated_judge_cost_usd == 100
    assert budget.max_wall_clock_seconds == 28800
    ledger = ScanBudgetLedger(budget)
    ledger.target_calls = 172
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


def test_paired_cases_are_two_independent_calls_and_roster_reconciles() -> None:
    rows = selected_rows()
    paired = [row for row in rows if row["oracle_type"] == "paired_comparator"]
    assert len(paired) == 72
    assert all(len(materialize_prompts(row)) == 2 for row in paired)
    assert sum(len(materialize_prompts(row)) for row in rows) == 172


def test_signed_manifest_round_trip_and_tamper_rejection(tmp_path: Path, monkeypatch) -> None:
    key = "k" * 32
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", key)
    store = AppendOnlyScrubbedEvidence(tmp_path)
    rows = selected_rows()
    for row in rows:
        store.write(row["stable_case_id"], {
            "stable_case_id": row["stable_case_id"],
            "verdict": {"pass": True},
            "call_accounting": {
                "target_calls": len(materialize_prompts(row)),
                "judge_calls": 1,
                "judge_input_tokens": 1,
                "judge_output_tokens": 1,
                "judge_cost_usd": 0.1,
            },
        })
    manifest = write_manifest(tmp_path, rows, {"target_calls": 172, "judge_calls": 100})
    assert verify_manifest(manifest, key)["counts"]["expected"] == 100
    first = store.path(rows[0]["stable_case_id"])
    body = json.loads(first.read_text())
    body["verdict"]["pass"] = False
    first.chmod(0o600)
    first.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="evidence hash mismatch"):
        verify_manifest(manifest, key)
