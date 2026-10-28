"""Failure projection must retain observed work without changing successful results."""
import pytest
from test_operations_status import app_env


@pytest.mark.parametrize('outcome,label,expected', [
    ('failed', 'Scan failed', 71),
    ('incomplete', 'Scan stopped before completion', 71),
    ('complete', 'Results ready', 0),
])
def test_terminal_progress(app_env, monkeypatch, outcome, label, expected):
    app, store, queue, bac, *_ = app_env
    row = {'status': 'finished', 'outcome': outcome, 'tests_json': '[]',
           'finished_at': '2026-10-27T16:12:15+00:00'}
    monkeypatch.setattr(store, 'scan', lambda _: row)
    monkeypatch.setattr(app.findings, 'accepted_scope', lambda _: 'starter-100@4')
    monkeypatch.setattr(app.findings, 'latest_progress', lambda _: {
        'processed_count': 71, 'usable_count': 71, 'unavailable_count': 0,
        'accepted_pack_total': 100})
    value = app._engine_progress('test')
    assert value['stage_label'] == label
    assert value['processed_count'] == expected
    assert value['usable_count'] == expected
    assert value['unavailable_count'] == 0
    assert value['accepted_pack_total'] == 100


@pytest.mark.parametrize('observed', [
    {'processed_count': 71, 'usable_count': 70, 'unavailable_count': 0},
    {'processed_count': True, 'usable_count': True, 'unavailable_count': 0},
    {'processed_count': -1, 'usable_count': -1, 'unavailable_count': 0},
    {'processed_count': 71},
])
def test_invalid_snapshot_does_not_invent_counts(app_env, monkeypatch, observed):
    app, store, queue, bac, *_ = app_env
    monkeypatch.setattr(store, 'scan', lambda _: {
        'status': 'finished', 'outcome': 'failed',
        'tests_json': '[{"verdict":"pass"}]', 'finished_at': None})
    monkeypatch.setattr(app.findings, 'accepted_scope', lambda _: None)
    monkeypatch.setattr(bac, 'latest_progress', lambda _: observed)
    value = app._engine_progress('test')
    assert (value['processed_count'], value['usable_count'], value['unavailable_count']) == (1, 1, 0)


def test_report_failure_preserves_completed_execution(app_env, monkeypatch):
    app, store, queue, bac, *_ = app_env
    monkeypatch.setattr(store, 'scan', lambda _: {
        'status': 'finished', 'outcome': 'incomplete',
        'tests_json': '[{"verdict":"pass"}]',
        'incomplete_reason': 'report_preparation_error:PrivateRendererError',
        'finished_at': '2026-10-27T16:26:59+00:00'})
    monkeypatch.setattr(app.findings, 'accepted_scope', lambda _: 'starter-100@4')
    monkeypatch.setattr(app.findings, 'latest_progress', lambda _: {
        'processed_count': 100, 'usable_count': 100, 'unavailable_count': 0,
        'accepted_pack_total': 100})
    value = app._engine_progress('test')
    assert value['processed_count'] == value['usable_count'] == 100
    assert value['execution_completed'] is True
    assert value['report_retry_allowed'] is True
    assert value['retry_allowed'] is False
    assert value['report_status'] == 'failed'
    assert value['stage_label'] == 'Scan completed; report preparation failed'
    assert 'PrivateRendererError' not in str(value)
