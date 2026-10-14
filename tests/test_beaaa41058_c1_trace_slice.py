import json
from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


SELECTED = {
    ('hallucination_legal', f'hl.f3.sc.{n:02d}') for n in range(7, 30)
}


def test_exactly_23_rows_are_newly_bound():
    rows = [row for row in build_registry() if row.get('trace_observation_adapter')]
    assert {(row['test_id'], row['case_id']) for row in rows if (row['test_id'], row['case_id']) in SELECTED} == SELECTED
    assert len(rows) == 1181


def test_all_23_rows_execute_n5_from_recorded_trace_without_model_calls(tmp_path):
    rows = [row for row in build_registry() if (row['test_id'], row['case_id']) in SELECTED]
    executor = RegisteredCaseExecutor(
        subject=MockRunner('unused-target', lambda _: 'unused'),
        judge=MockRunner('unused-judge', lambda _: 'unused'),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    for row in rows:
        assert row['scoring_active'] is False
        for seed in range(5):
            result = executor({**row, 'scan_seed': seed}, SimpleNamespace(
                job_id='beaaa-41058', scan_id='beaaa-41058', agent_id='trace-target',
                seed=seed, capability_manifest={},
            ))
            assert result.status == 'complete'
            assert result.target_calls == result.judge_calls == 0
            observation = json.loads((tmp_path / result.evidence_ref.removeprefix('evidence://')).read_text())['judge']['observation']
            assert observation['recorded_target_identity'] == observation['target_identity']
            assert observation['capture_ref']
