"""Original form capture must be read-only, owner-bound and credential-free."""
import hashlib
import json
import sqlite3

import pytest
from api.selection_capture import export_selection, canonical

SCAN='282ef090-786a-4b60-bf1e-3214178b1f59'


def fixture(root):
    tenant='partner-test';agent='target'
    scope=hashlib.sha256(canonical([tenant,agent]).encode()).hexdigest()
    manifest={'policy':{'strata':[dict(stratum_id='s',family_id='f',count=1)],'bank_version':'bank'},
        'cases':[dict(scenario_id='starter/X',stratum_id='s',payload={'private':'unused-secret'})],
        'test_count':1,'policy_hash':'3'*64,'excluded_prior_form_ids':[],'selection_seed':'private-seed'}
    with sqlite3.connect(root/'accepted_scans.sqlite3') as db:
        db.execute('CREATE TABLE accepted_scans(scan_id,tenant_id,state,request_json,result_json)')
        req=dict(scan_id=SCAN,tenant_id=tenant,agent_id=agent,pack_id='starter-100@4',corpus_version='0.1.0',
                 corpus_snapshot_hash='sha256:'+'1'*64,seed=42,credential_ref='private-provider-key')
        db.execute('INSERT INTO accepted_scans VALUES(?,?,?,?,?)',(SCAN,tenant,'terminal',json.dumps(req),json.dumps(
            {'starter_result_rows':[dict(test_id='X',reply='whole',verdict='passed')]})))
    with sqlite3.connect(root/'variable-forms.sqlite3') as db:
        db.execute('CREATE TABLE variable_test_forms(form_id,scope_hash,product,content_hash,manifest)')
        raw=canonical(manifest)
        db.execute('INSERT INTO variable_test_forms VALUES(?,?,?,?,?)',(SCAN,scope,'starter',hashlib.sha256(raw.encode()).hexdigest(),raw))
    return {'scan_id':SCAN,'tenant_id':tenant}


def test_owner_bound_projection_excludes_private_material_and_never_writes(tmp_path):
    request=fixture(tmp_path)
    before={p.name:p.read_bytes() for p in tmp_path.glob('*.sqlite3')}
    exported=export_selection(request,tmp_path)
    assert exported['selected_cases']==[{'id':'X','family':'f'}]
    assert exported['expected_families']=={'f':1} and exported['unavailable_case_ids']==[]
    assert 'private' not in canonical(exported)
    assert before=={p.name:p.read_bytes() for p in tmp_path.glob('*.sqlite3')}
    with pytest.raises(ValueError):export_selection(dict(request,tenant_id='partner-other'),tmp_path)
    with pytest.raises(ValueError):export_selection(dict(request,scan_id='../../etc/passwd'),tmp_path)
    with sqlite3.connect(tmp_path/'variable-forms.sqlite3') as db:db.execute("UPDATE variable_test_forms SET manifest='{}'")
    with pytest.raises(ValueError,match='unavailable'):export_selection(request,tmp_path)
