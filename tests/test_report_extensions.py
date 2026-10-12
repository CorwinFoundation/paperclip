"""Retained report bindings preserve results, ownership and deletion boundaries."""
import sys, json
from pathlib import Path
from datetime import datetime, timezone
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backbond/scan-intake/server'))
from qa_service import IntakeStore
from report_extensions import bind, VOICE

EVIDENCE='4efce9f3276657b4988bfb6995082dbcd5ef2716'
JUDGEMENTS='4256106f1b0b31a018a707313643ca44d76c9be0'

def fixture(tmp_path):
    store=IntakeStore(str(tmp_path/'intake.sqlite'))
    store.db.execute('PRAGMA foreign_keys=OFF')
    original=[{'id':f'T-{i}','family':'example','verdict':'pass' if i<90 else 'fail'} for i in range(100)]
    voice=[{'id':f'{p}-{i:02}','family':f,'verdict':'pass'} for p,n,f in VOICE for i in range(1,n+1)]
    for t in voice[:16]: t['verdict']='fail'
    for sid,token,pack,tests,source in [('base','a'*32,'starter-100@3',original,None),('voice','b'*32,'starter-voice-50@1',voice,'base')]:
        store.db.execute('''INSERT INTO scans(id,code,partner,email,agent_label,endpoint_host,transport,
            pack_version,created_at,status,outcome,report_token,tests_json,source_intake_id)
            VALUES(?,'code','partner','fixture@example.test','agent','learning.kaltura.com','kaltura_genie_browser',
            ?,?,'finished','complete',?,?,?)''',(sid,pack,datetime.now(timezone.utc).isoformat(),token,json.dumps(tests),source))
        store.db.execute("INSERT INTO acceptances VALUES(?,'v0.2.2',?,1,0)",(sid,datetime.now(timezone.utc).isoformat()))
    targeted=[{'id':f'KFR-{p}{i:02}-R{r}','family':'example','prompt':'Redacted fixture',
               'reply':'Redacted fixture','reason':'Fixture judgement','verdict':'pass'}
              for p,n in [('S',6),('I',6),('U',4),('P',2),('F',2)] for i in range(1,n+1) for r in (1,2,3)]
    for i,t in enumerate(targeted): t['verdict']='pass' if i<37 else 'fail' if i<48 else 'inconclusive' if i<57 else 'unverified'
    return store,targeted

def test_bind_preserves_rows_standalone_verdicts_and_never_mails(tmp_path):
    store,tests=fixture(tmp_path)
    before=[tuple(r) for r in store.db.execute('SELECT * FROM scans')]
    bind(store.db,'base','voice',tests,EVIDENCE,JUDGEMENTS)
    bind(store.db,'base','voice',tests,EVIDENCE,JUDGEMENTS)
    report=store.report('a'*32)
    assert [len(r['tests']) for r in report['result_sets']]==[100,60,60]
    assert report['counts']=={'passed':171,'failed':37,'could_not_run':12}
    assert report['starter_score'] is None
    assert report['full_scan_score'] is None
    assert report['underwriting_active'] is False
    assert report['priced_score_weight'] == 0.0
    assert len(report['uncovered_dimensions']) == 8
    assert store.report('b'*32)['counts']=={'passed':44,'failed':16,'could_not_run':0}
    assert before==[tuple(r) for r in store.db.execute('SELECT * FROM scans')]
    assert store.db.execute('SELECT count(*) FROM mail_outbox').fetchone()[0]==0
    assert store.report('c'*32) is None

@pytest.mark.parametrize('change',["email='other@example.test'","partner='other'","endpoint_host='other.test'",
    "transport='rest'","source_intake_id='other'","outcome='incomplete'","pack_version='starter-100@4'",
    "created_at='2025-01-01T00:00:00Z'"])
def test_binding_rejects_other_owner_scope_or_incomplete_source(tmp_path,change):
    store,tests=fixture(tmp_path)
    store.db.execute(f"UPDATE scans SET {change} WHERE id='voice'")
    with pytest.raises(ValueError): bind(store.db,'base','voice',tests,EVIDENCE,JUDGEMENTS)
    assert store.db.execute('SELECT count(*) FROM report_extensions').fetchone()[0]==0

def test_rejects_missing_consent_duplicate_target_and_changed_provenance(tmp_path):
    store,tests=fixture(tmp_path)
    bad=[dict(t) for t in tests];bad[0]['id']=bad[1]['id']
    with pytest.raises(ValueError): bind(store.db,'base','voice',bad,EVIDENCE,JUDGEMENTS)
    with pytest.raises(ValueError): bind(store.db,'base','voice',tests,'0'*40,JUDGEMENTS)
    store.db.execute("DELETE FROM acceptances WHERE scan_id='voice'")
    with pytest.raises(ValueError): bind(store.db,'base','voice',tests,EVIDENCE,JUDGEMENTS)

def test_bound_source_change_fails_closed_and_deletion_cascades(tmp_path):
    store,tests=fixture(tmp_path)
    bind(store.db,'base','voice',tests,EVIDENCE,JUDGEMENTS)
    store.db.execute("UPDATE scans SET tests_json=tests_json || ' ' WHERE id='voice'")
    assert store.report('a'*32) is None
    store.db.execute('PRAGMA foreign_keys=ON')
    store.db.execute("DELETE FROM acceptances WHERE scan_id='voice'")
    store.db.execute("DELETE FROM scans WHERE id='voice'")
    assert store.db.execute('SELECT count(*) FROM report_extensions').fetchone()[0]==0
