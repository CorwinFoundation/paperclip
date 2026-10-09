import json

import tools.materialize_full_scan_h_agentdojo as materializer


class Task:
    def __init__(self, *, prompt="", goal=""):
        self.PROMPT = prompt
        self.GOAL = goal


class Suite:
    def __init__(self, user_count: int, injection_count: int):
        self.user_tasks = {
            f"user_task_{i}": Task(prompt=f"user prompt {i}") for i in range(user_count)
        }
        self.injection_tasks = {
            f"injection_task_{i}": Task(goal=f"attacker goal {i}")
            for i in range(injection_count)
        }


def published_suites():
    return {
        "workspace": Suite(40, 14),
        "slack": Suite(21, 5),
        "travel": Suite(20, 7),
        "banking": Suite(16, 9),
    }


def test_reference_cases_match_published_629_security_inventory():
    cases = materializer.reference_cases(published_suites())
    assert len(cases) == 629
    assert cases[0].source_case_id == "workspace.user_task_0.injection_task_0"
    assert cases[-1].source_case_id == "banking.user_task_15.injection_task_8"
    assert all(case.source_version == "0.1.35" for case in cases)


def test_materialize_emits_report_only_fixed_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(materializer, "load_suites", published_suites)
    output = tmp_path / "agentdojo.jsonl"
    summary = materializer.materialize(output)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary["source_cases"] == 629
    assert summary["materialized_cases"] + summary["deduplicated_cases"] == 629
    assert len(rows) == summary["materialized_cases"]
    assert all(row["forms"] == [{"form_id": "fixed", "prompt": row["prompt"]}] for row in rows)
    assert all(not row["starter_eligible"] and not row["score_input"] for row in rows)
    assert all(not row["underwriting_active"] and row["priced_score_weight"] == 0.0 for row in rows)
