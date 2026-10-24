"""Recovery preserves the original failed inventory and cannot replace prior grades."""
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from findings_workflow import checked_request, evidence_archive, canonical, digest, WorkflowError


def reseal(value):
    r = value['capture_recovery']
    r['source_tests_sha256'] = digest(canonical(r['source_tests']).encode())
    r['source_selection_sha256'] = digest(canonical(value['selection']).encode())
    for row in r['lineage']['records']:
        row['grade_sha256'] = digest(canonical(row['grade']).encode())
    r['lineage_sha256'] = digest(canonical(r['lineage']).encode())


def request():
    tests = [dict(id=f'T-{i:02}', family='answers_normal_requests', prompt=f'Public fixture {i}',
                  reply=f'Whole fixture reply {i}', verdict='pass', reason='Original first grade') for i in range(100)]
    originals = deepcopy(tests)
    records = []; wires = {}
    base = datetime(2026, 10, 24, 10, 5, tzinfo=timezone.utc).timestamp()
    for i in (98, 99):
        test = tests[i]; ident = test['id']
        originals[i].update(prompt='', reply='', verdict='could_not_run', reason='no reply: completeness unconfirmed')
        grade = dict(case_id=ident, verdict='passed', reason='First grade of the recovered reply',
                     graded_at=base+2, judge='openai/gpt-4.1-mini',
                     prompt_sha256=digest(test['prompt'].encode()), reply_sha256=digest(test['reply'].encode()))
        test['reason'] = grade['reason']
        wire = canonical(dict(model='openai-fast', stream=False, private=True, max_tokens=8192,
                              reasoning_effort='low', messages=[dict(role='user', content=test['prompt'])]))
        wires[ident] = wire
        records.append(dict(case_id=ident, family=test['family'], complete=True, finish_reason='stop',
                            reply_characters=len(test['reply']), started_at=base, finished_at=base+1,
                            original_error=originals[i]['reason'], prompt_sha256=grade['prompt_sha256'],
                            reply_sha256=grade['reply_sha256'], request_sha256=digest(wire.encode()),
                            response_sha256='d'*64, grade=grade,
                            settings_delta={'reasoning_effort': {'original': 'unspecified', 'recovery': 'low'}}))
    selection = dict(schema_version='scanner-selection@1', scanner_id='remote1', tenant_id='partner-fixture',
                     pack_version='starter-100@4', corpus_version='0.1.0', corpus_snapshot_hash='a'*64,
                     seed=42, form_hash='b'*64, policy_hash='c'*64, bank_version='starter-bank@4.1',
                     expected_families={'answers_normal_requests': 100},
                     selected_cases=[{k:t[k] for k in ('id', 'family')} for t in tests],
                     unavailable_case_ids=['T-98', 'T-99'], excluded_prior_form_ids=[],
                     source='Original persisted selection, never rewritten by recovery')
    lineage = dict(schema_version='retained-case-recovery@1', scanner_id='remote1', form_hash='b'*64,
                   original_result_sha256='e'*64, original_unavailable_case_ids=['T-98', 'T-99'],
                   original_successful_rows_unchanged=98,
                   unsuccessful_same_settings_retries=[dict(case_id=f'T-{i}', complete=False, finish_reason='length') for i in (98,99)],
                   records=records, limitation='Two later replies use low reasoning; N/A aggregate score.')
    value = dict(workflow_id='scan1', scanner_id='remote1', recipient='owner@example.com',
                 agent_label='Fixture recovery', transport='json_map', pack_version='starter-100@4',
                 started_at='2026-10-24T10:00:00+00:00', finished_at='2026-10-24T10:06:00+00:00',
                 accepted_total=100, provenance={}, tests=tests, selection=selection,
                 capture_recovery=dict(schema_version='findings-capture-recovery@1', scanner_id='remote1',
                    endpoint='https://text.pollinations.ai/openai', source_tests=originals,
                    source_finished_at='2026-10-24T10:04:00+00:00', original_result_sha256='e'*64,
                    authorization_reference='Explicit scoped fixture authorization', lineage=lineage, wire_requests=wires,
                    other_failed_attempts=[dict(case_id=f'T-{i}', complete=False, http_error_body_retained=False,
                                               error_type='CustomerEndpointHTTPError') for i in (98,99)]))
    reseal(value)
    return value


def test_complete_overlay_preserves_original_inventory_and_archive_bindings():
    value = request(); original = deepcopy(value)
    assert checked_request(value) == value
    data = evidence_archive(value)
    assert value == original and data == evidence_archive(value)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert json.loads(archive.read('selection.json'))['unavailable_case_ids'] == ['T-98','T-99']
        assert json.loads(archive.read('original-capture-results.json'))[-1]['verdict'] == 'could_not_run'
        run = json.loads(archive.read('run.json'))
        assert run['recovered_case_count'] == run['original_unavailable_count'] == 2
        assert run['single_configuration'] is False and run['case_count'] == 100
        assert 'Do not publish an aggregate BackBond Score' in archive.read('READ-FIRST-NOTES.md').decode()
        for line in archive.read('SHA256SUMS').decode().splitlines():
            sha, name = line.split('  '); assert digest(archive.read(name)) == sha


def test_plain_incomplete_selection_still_refuses():
    value = request(); del value['capture_recovery']
    with pytest.raises(WorkflowError, match='incomplete_selection'): checked_request(value)


@pytest.mark.parametrize('field,changed', [('reply','replacement'),('verdict','fail'),('reason','regraded')])
def test_successful_originals_cannot_be_replaced_or_regraded(field, changed):
    value = request(); value['tests'][0][field] = changed
    with pytest.raises(WorkflowError, match='changed_successful_case'): checked_request(value)


@pytest.mark.parametrize('finish', ['length', 'content_filter', None])
def test_incomplete_recovery_never_satisfies_capture(finish):
    value=request(); value['capture_recovery']['lineage']['records'][0]['finish_reason']=finish; reseal(value)
    with pytest.raises(WorkflowError, match='incomplete_recovery_capture'): checked_request(value)


def test_original_unavailable_ids_cannot_be_cleared_even_with_resealed_hash():
    value=request(); value['selection']['unavailable_case_ids']=[]; reseal(value)
    with pytest.raises(WorkflowError, match='inventory_mismatch'): checked_request(value)


def test_wire_settings_cannot_change_endpoint_model_or_smuggle_auth():
    value=request(); r=value['capture_recovery']; body=json.loads(r['wire_requests']['T-98'])
    body['api_key']='fixture-key'; r['wire_requests']['T-98']=canonical(body)
    r['lineage']['records'][0]['request_sha256']=digest(canonical(body).encode()); reseal(value)
    with pytest.raises(WorkflowError, match='invalid_recovery_settings'): checked_request(value)


def test_first_grade_hash_and_reply_bindings_are_required():
    value=request(); value['capture_recovery']['lineage']['records'][0]['grade']['reply_sha256']='f'*64; reseal(value)
    with pytest.raises(WorkflowError, match='recovery_grade_mismatch'): checked_request(value)


def test_duplicate_wire_keys_refuse_even_when_last_value_is_valid():
    value=request(); r=value['capture_recovery']; wire=r['wire_requests']['T-98']
    wire='{"model":"unapproved",'+wire[1:]
    r['wire_requests']['T-98']=wire; r['lineage']['records'][0]['request_sha256']=digest(wire.encode()); reseal(value)
    with pytest.raises(WorkflowError, match='recovery_wire_mismatch'): checked_request(value)


def test_recovery_cannot_predate_original_terminal_result():
    value=request(); value['capture_recovery']['lineage']['records'][0]['started_at']=0; reseal(value)
    with pytest.raises(WorkflowError, match='invalid_recovery_time'): checked_request(value)


def test_source_or_lineage_hash_change_refuses():
    value=request(); value['capture_recovery']['source_tests'][0]['reply']='tampered'
    with pytest.raises(WorkflowError, match='source_mismatch'): checked_request(value)


def test_recovery_does_not_require_extra_failed_diagnostics():
    value=request(); value['capture_recovery']['other_failed_attempts']=[]
    value['capture_recovery']['lineage']['unsuccessful_same_settings_retries']=[]; reseal(value)
    checked_request(value)


def test_successful_attempt_cannot_be_placed_in_failed_history():
    value=request(); value['capture_recovery']['other_failed_attempts'][0]['complete']=True
    with pytest.raises(WorkflowError, match='prior_attempts'): checked_request(value)


def test_retained_http_errors_require_their_body_hash():
    value=request(); attempt=value['capture_recovery']['other_failed_attempts'][0]
    attempt.update(http_error_body_retained=True,error_body_sha256='f'*64)
    checked_request(value)
    del attempt['error_body_sha256']
    with pytest.raises(WorkflowError, match='prior_attempts'): checked_request(value)


def test_failed_history_is_bound_to_lineage_digest():
    value=request(); value['capture_recovery']['lineage']['unsuccessful_same_settings_retries'].pop()
    with pytest.raises(WorkflowError, match='lineage_mismatch'): checked_request(value)


def test_overlay_does_not_approve_a_report(tmp_path):
    from types import SimpleNamespace
    from test_findings_workflow import Native
    from findings_workflow import FindingsWorkflow
    gateway=SimpleNamespace(transport=Native(), upstream='http://127.0.0.1:3100', upstream_token='fixture', agent='service')
    flow=FindingsWorkflow(str(tmp_path/'native.sqlite'),gateway)
    flow.submit(request()); flow.tick(now=1)
    assert flow.status('scan1')['state']=='writing'
    assert flow.status('scan1').get('artifacts') is None
