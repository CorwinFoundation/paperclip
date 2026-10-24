"""Validate an explicit recovery overlay without rewriting the original capture.

The authenticated intake producer retains source custody. This validator binds
its original inventory, unchanged successful rows, exact later request bytes and
first grades. It never calls a provider, changes a grade, or approves a report.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def timestamp(value):
    require(type(value) in (int, float) and math.isfinite(value), 'invalid_recovery_time')
    return value


def unique_object(pairs):
    result = {}
    for key, item in pairs:
        require(key not in result, 'recovery_wire_duplicate_key')
        result[key] = item
    return result


def checked_capture_recovery(value, selection=None):
    """Return recovered IDs only after verifying the entire closed overlay."""
    recovery = value['capture_recovery']
    fields = {'schema_version', 'scanner_id', 'endpoint', 'source_tests', 'source_tests_sha256',
              'source_selection_sha256', 'source_finished_at', 'original_result_sha256',
              'authorization_reference', 'lineage', 'lineage_sha256', 'wire_requests', 'other_failed_attempts'}
    require(isinstance(recovery, dict) and set(recovery) == fields
            and recovery['schema_version'] == 'findings-capture-recovery@1', 'invalid_capture_recovery')
    require('adjudication' not in value and value['pack_version'] == 'starter-100@4'
            and value['transport'] == 'json_map' and value['accepted_total'] == 100
            and recovery['endpoint'] == 'https://text.pollinations.ai/openai'
            and recovery['scanner_id'] == value['scanner_id'], 'invalid_recovery_scope')
    selected = selection if selection is not None else value.get('selection')
    require(isinstance(selected, dict), 'recovery_selection_required')
    for key in ('source_tests_sha256', 'source_selection_sha256', 'original_result_sha256', 'lineage_sha256'):
        require(isinstance(recovery[key], str) and re.fullmatch('[a-f0-9]{64}', recovery[key]), 'invalid_recovery_hash')
    require(digest(canonical(selected).encode()) == recovery['source_selection_sha256'], 'recovery_selection_changed')
    reference = recovery['authorization_reference']
    require(isinstance(reference, str) and 1 <= len(reference) <= 500 and not any(ord(c) < 32 for c in reference),
            'invalid_recovery_authorization_reference')
    try:
        source_finished = datetime.fromisoformat(recovery['source_finished_at'])
        finished = datetime.fromisoformat(value['finished_at'])
        require(source_finished.tzinfo is not None and finished.tzinfo is not None, 'invalid_recovery_time')
        source_finished = source_finished.timestamp()
        finished = finished.timestamp()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('invalid_recovery_time') from exc
    source = recovery['source_tests']
    require(isinstance(source, list) and len(source) == 100
            and digest(canonical(source).encode()) == recovery['source_tests_sha256'], 'recovery_source_mismatch')
    require(all(isinstance(t, dict) and isinstance(t.get('id'), str) and isinstance(t.get('family'), str)
                for t in source), 'invalid_recovery_source')
    originals = {t['id']: t for t in source}
    tests = {t['id']: t for t in value['tests']}
    require(len(originals) == 100 and set(originals) == set(tests)
            and all(originals[k]['family'] == tests[k]['family'] for k in tests), 'recovery_case_mismatch')
    unavailable = set()
    for ident, original in originals.items():
        if original.get('verdict') in ('pass', 'fail'):
            require(original.get('prompt') and original.get('reply') and original == tests[ident],
                    'recovery_changed_successful_case')
        else:
            require(original.get('verdict') in ('could_not_run', 'connection_error')
                    and not original.get('reply') and (original.get('reason') or original.get('error')),
                    'invalid_recovery_source')
            unavailable.add(ident)
    listed = selected.get('unavailable_case_ids')
    require(isinstance(listed, list) and len(set(listed)) == len(listed)
            and set(listed) == unavailable and 0 < len(unavailable) < 100, 'recovery_inventory_mismatch')
    lineage = recovery['lineage']
    lineage_fields = {'schema_version', 'scanner_id', 'form_hash', 'original_result_sha256',
                      'original_unavailable_case_ids', 'original_successful_rows_unchanged',
                      'unsuccessful_same_settings_retries', 'records', 'limitation'}
    require(isinstance(lineage, dict) and set(lineage) == lineage_fields
            and lineage['schema_version'] == 'retained-case-recovery@1'
            and digest(canonical(lineage).encode()) == recovery['lineage_sha256'], 'recovery_lineage_mismatch')
    require(lineage['scanner_id'] == value['scanner_id'] and lineage['form_hash'] == selected.get('form_hash')
            and lineage['original_result_sha256'] == recovery['original_result_sha256']
            and lineage['original_unavailable_case_ids'] == sorted(unavailable)
            and type(lineage['original_successful_rows_unchanged']) is int
            and lineage['original_successful_rows_unchanged'] == 100-len(unavailable), 'recovery_lineage_mismatch')
    records, wires = lineage['records'], recovery['wire_requests']
    require(isinstance(records, list) and len(records) == len(unavailable)
            and all(isinstance(r, dict) and isinstance(r.get('case_id'), str) for r in records)
            and {r['case_id'] for r in records} == unavailable
            and isinstance(wires, dict) and set(wires) == unavailable, 'recovery_case_mismatch')
    # A recovery need not have an intervening diagnostic failure. Preserve the
    # producer's complete known history, whether empty or repeated, rather than
    # requiring this incident's missing HTTP-body capture to recur forever.
    for history in (lineage['unsuccessful_same_settings_retries'], recovery['other_failed_attempts']):
        require(isinstance(history, list) and len(history) <= 1000, 'invalid_recovery_prior_attempts')
        for attempt in history:
            require(isinstance(attempt, dict) and isinstance(attempt.get('case_id'), str)
                    and attempt['case_id'] in unavailable and attempt.get('complete') is False,
                    'invalid_recovery_prior_attempts')
            if 'prompt_sha256' in attempt:
                require(attempt['prompt_sha256'] == digest(tests[attempt['case_id']]['prompt'].encode()),
                        'invalid_recovery_prior_attempts')
            if 'http_error_body_retained' in attempt:
                require(type(attempt['http_error_body_retained']) is bool, 'invalid_recovery_prior_attempts')
                if attempt['http_error_body_retained']:
                    require(isinstance(attempt.get('error_body_sha256'), str)
                            and re.fullmatch('[a-f0-9]{64}', attempt['error_body_sha256']),
                            'invalid_recovery_prior_attempts')
    for record in records:
        ident = record['case_id']; test = tests[ident]
        require(record.get('complete') is True and record.get('finish_reason') == 'stop'
                and record.get('family') == test['family'] and record.get('reply_characters') == len(test['reply'])
                and record.get('original_error') == originals[ident].get('reason')
                and record.get('prompt_sha256') == digest(test['prompt'].encode())
                and record.get('reply_sha256') == digest(test['reply'].encode())
                and isinstance(record.get('response_sha256'), str)
                and re.fullmatch('[a-f0-9]{64}', record['response_sha256']), 'incomplete_recovery_capture')
        require(source_finished <= timestamp(record['started_at']) <= timestamp(record['finished_at']) <= finished,
                'invalid_recovery_time')
        require(record.get('settings_delta') == {'reasoning_effort': {'original': 'unspecified', 'recovery': 'low'}},
                'invalid_recovery_settings')
        wire = wires[ident]
        require(isinstance(wire, str) and digest(wire.encode()) == record.get('request_sha256'), 'recovery_wire_mismatch')
        try:
            body = json.loads(wire, object_pairs_hook=unique_object)
        except (TypeError, ValueError) as exc:
            raise ValueError('recovery_wire_mismatch') from exc
        require(body == {'model': 'openai-fast', 'stream': False, 'private': True, 'max_tokens': 8192,
                         'reasoning_effort': 'low', 'messages': [{'role': 'user', 'content': test['prompt']}]},
                'invalid_recovery_settings')
        require(type(body['max_tokens']) is int and type(body['stream']) is bool and type(body['private']) is bool,
                'invalid_recovery_settings')
        grade = record.get('grade')
        grade_fields = {'case_id', 'verdict', 'reason', 'graded_at', 'judge', 'reply_sha256', 'prompt_sha256'}
        require(isinstance(grade, dict) and set(grade) == grade_fields
                and digest(canonical(grade).encode()) == record.get('grade_sha256')
                and grade['case_id'] == ident and grade['judge'] == 'openai/gpt-4.1-mini'
                and grade['prompt_sha256'] == record['prompt_sha256'] and grade['reply_sha256'] == record['reply_sha256']
                and grade['verdict'] in ('passed', 'failed')
                and isinstance(grade['reason'], str) and 1 <= len(grade['reason']) <= 2000
                and timestamp(grade['graded_at']) >= record['finished_at'], 'recovery_grade_mismatch')
        expected = {'id': ident, 'family': test['family'], 'prompt': test['prompt'], 'reply': test['reply'],
                    'verdict': {'passed': 'pass', 'failed': 'fail'}[grade['verdict']], 'reason': grade['reason']}
        require(test == expected, 'recovery_grade_mismatch')
    require(Counter(t['family'] for t in source) == Counter(t['family'] for t in value['tests']), 'recovery_case_mismatch')
    return unavailable


RECOVERY_NOTICE = (
    'RECOVERED EVIDENCE: selection.json retains the original unavailable cases. '
    'capture-recovery.json binds their later complete replies and first grades, while '
    'original-capture-results.json preserves the original inventory and all prior grades. '
    'This is combined same-form evidence, not a new uniformly configured scan. The report '
    'must disclose original/recovered counts and the reasoning setting change next to its '
    'coverage statement. Do not publish an aggregate BackBond Score; use N/A. Counts are '
    'recorded outcomes across the stated attempts, not a uniform-configuration rating. '
    'The independent Reviewer must verify these disclosures and source bindings.\n'
)
