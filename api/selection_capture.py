"""Read-only, tenant-bound projection of Scanner's pre-dispatch form.

Called only after the existing result route authenticates its tenant. Never return target credentials, private
selection seeds, unused bank content or scenario payloads. Never reserve a form.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def export_selection(value, directory='/var/lib/are-scanner'):
    if (not isinstance(value, dict) or set(value) != {'scan_id', 'tenant_id'}
            or not isinstance(value['scan_id'], str)
            or not re.fullmatch(r'[a-f0-9-]{36}', value['scan_id'])
            or not isinstance(value['tenant_id'], str)
            or not re.fullmatch(r'partner-[A-Za-z0-9_-]{1,128}', value['tenant_id'])):
        raise ValueError('invalid_owner')
    root = Path(directory)
    with sqlite3.connect('file:' + str(root / 'accepted_scans.sqlite3') + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT state,request_json,result_json FROM accepted_scans WHERE scan_id=? AND tenant_id=?',
                         (value['scan_id'], value['tenant_id'])).fetchone()
    if not row or row[0] != 'terminal':
        raise ValueError('terminal_scan_required')
    request, result = json.loads(row[1]), json.loads(row[2])
    if request.get('tenant_id') != value['tenant_id'] or request.get('scan_id') != value['scan_id']:
        raise ValueError('owner_mismatch')
    scope = hashlib.sha256(canonical([value['tenant_id'], request['agent_id']]).encode()).hexdigest()
    with sqlite3.connect('file:' + str(root / 'variable-forms.sqlite3') + '?mode=ro', uri=True) as db:
        form = db.execute("SELECT content_hash,manifest FROM variable_test_forms WHERE form_id=? AND scope_hash=? AND product='starter'",
                          (value['scan_id'], scope)).fetchone()
    if not form or hashlib.sha256(form[1].encode()).hexdigest() != form[0]:
        raise ValueError('saved_form_unavailable')
    manifest = json.loads(form[1])
    strata = {s['stratum_id']: s for s in manifest['policy']['strata']}
    cases = [{'id': c['scenario_id'].removeprefix('starter/'),
              'family': strata[c['stratum_id']]['family_id']} for c in manifest['cases']]
    expected = dict(Counter({s['family_id']: s['count'] for s in strata.values()}))
    if Counter(c['family'] for c in cases) != expected or len(cases) != manifest['test_count']:
        raise ValueError('damaged_selection')
    completed = {r['test_id'] for r in result.get('starter_result_rows', []) if r.get('reply') and r.get('verdict') in {'pass', 'fail', 'passed', 'failed'}}
    return {'schema_version': 'scanner-selection@1', 'scanner_id': value['scan_id'],
            'tenant_id': value['tenant_id'], 'pack_version': request['pack_id'],
            'corpus_version': request['corpus_version'], 'corpus_snapshot_hash': request['corpus_snapshot_hash'],
            'seed': request['seed'], 'form_hash': form[0], 'policy_hash': manifest['policy_hash'],
            'bank_version': manifest['policy']['bank_version'], 'expected_families': expected,
            'selected_cases': cases, 'unavailable_case_ids': sorted(c['id'] for c in cases if c['id'] not in completed),
            'excluded_prior_form_ids': manifest['excluded_prior_form_ids'],
            'source': 'Scanner persisted accepted request and pre-dispatch variable_test_forms; read-only owner-scoped export'}


