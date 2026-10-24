"""Append-only report adjudication, bound to immutable saved exchanges."""
import hashlib
import json
from collections import Counter


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def case_hash(case):
    return hashlib.sha256(canonical({k: case[k] for k in ('id', 'family', 'prompt', 'reply')}).encode()).hexdigest()


def checked_adjudication(request):
    review = request['adjudication']
    fields = {'schema_version', 'source_workflow_id', 'source_request_sha256', 'authority', 'reviewed_at', 'corrections'}
    if not isinstance(review, dict) or set(review) != fields or review['schema_version'] != 'findings-adjudication@1':
        raise ValueError('invalid_adjudication')
    for name in ('source_workflow_id', 'authority', 'reviewed_at'):
        if not isinstance(review[name], str) or not 1 <= len(review[name]) <= 500:
            raise ValueError('invalid_adjudication_metadata')
    original = {k: v for k, v in request.items() if k != 'adjudication'}
    original['workflow_id'] = review['source_workflow_id']
    source_hash = hashlib.sha256(canonical(original).encode()).hexdigest()
    if source_hash != review['source_request_sha256']:
        raise ValueError('adjudication_source_mismatch')
    corrections = review['corrections']
    if not isinstance(corrections, list) or not 1 <= len(corrections) <= len(request['tests']):
        raise ValueError('invalid_adjudication_corrections')
    cases = {c['id']: c for c in request['tests']}
    seen = set()
    for row in corrections:
        if not isinstance(row, dict) or set(row) != {'case_id', 'case_sha256', 'original_verdict', 'reviewed_verdict', 'reason'}:
            raise ValueError('invalid_adjudication_case')
        ident = row['case_id']
        if not isinstance(ident, str) or ident not in cases or ident in seen:
            raise ValueError('adjudication_case_mismatch')
        seen.add(ident)
        case = cases[ident]
        if row['case_sha256'] != case_hash(case) or row['original_verdict'] != case['verdict']:
            raise ValueError('adjudication_exchange_mismatch')
        if row['reviewed_verdict'] not in ('pass', 'fail', 'unresolved'):
            raise ValueError('invalid_reviewed_verdict')
        if not isinstance(row['reason'], str) or not 1 <= len(row['reason']) <= 2000:
            raise ValueError('invalid_adjudication_reason')
    return review


def reviewed_counts(request):
    review = checked_adjudication(request)
    grades = {r['case_id']: r['reviewed_verdict'] for r in review['corrections']}
    tests = request['tests']
    return {'total': len(tests), 'basis': 'original labels plus bound adjudication corrections',
            **dict(Counter(grades.get(t['id'], t['verdict']) for t in tests)),
            'families': {f: dict(Counter(grades.get(t['id'], t['verdict']) for t in tests if t['family'] == f))
                         for f in sorted({t['family'] for t in tests})}}


def verify_stored_source(request, stored_payload, stored_sha):
    """Check a source loaded by the trusted store, not a caller's self-hash."""
    review = checked_adjudication(request)
    if request['workflow_id'] == review['source_workflow_id'] or stored_payload is None:
        raise ValueError('adjudication_source_not_found')
    if stored_sha != review['source_request_sha256']:
        raise ValueError('adjudication_stored_source_mismatch')
    source = json.loads(stored_payload)
    reconstructed = {k: v for k, v in request.items() if k != 'adjudication'}
    reconstructed['workflow_id'] = review['source_workflow_id']
    if source != reconstructed or hashlib.sha256(canonical(source).encode()).hexdigest() != stored_sha:
        raise ValueError('adjudication_stored_source_mismatch')
