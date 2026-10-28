import hashlib, json, os, sqlite3, subprocess, tarfile
from pathlib import Path
HELPER = Path(__file__).resolve().parents[1] / 'scripts/deploy/scanner-deploy.sh'
def test_incident_scope_and_no_dispatch(tmp_path):
    ident = 'incident-id'
    ref = 'BB-' + hashlib.sha256(('starter-public:' + ident).encode()).hexdigest()[:20]
    helper = tmp_path / 'helper'
    helper.write_text(HELPER.read_text().replace('BB-e4b6027ddf30702e50ad', ref))
    intake, jobs = tmp_path / 'intake.sqlite', tmp_path / 'jobs.sqlite'
    with sqlite3.connect(intake) as db:
        db.execute('CREATE TABLE scans (id TEXT, value TEXT)')
        db.executemany('INSERT INTO scans VALUES (?,?)', [(ident,'preserve'),('other','excluded')])
    job = 'a935915a-ddc0-473f-ba46-ebd3593cc38b'
    with sqlite3.connect(jobs) as db:
        db.execute('CREATE TABLE full_scan_jobs (job_id TEXT, job_json TEXT)')
        db.executemany('INSERT INTO full_scan_jobs VALUES (?,?)', [(job,json.dumps({'scan_id':ident})),('other','{}')])
    evidence = tmp_path / 'evidence' / job
    evidence.mkdir(parents=True)
    (evidence / 'case.json').write_text('original')
    docker = tmp_path / 'docker'
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n[ "$1" = logs ]\n')
    docker.chmod(0o755)
    before = jobs.read_bytes(), intake.read_bytes()
    env = {**os.environ,'SCANNER_DOCKER':str(docker),'SCANNER_INCIDENT_INTAKE_DB':str(intake),'SCANNER_FULL_SCAN_JOB_STORE':str(jobs),'SCANNER_EVIDENCE_STATE_DIR':str(tmp_path),'SCANNER_EVIDENCE_EXPORT_DIR':str(tmp_path/'exports')}
    result = subprocess.run(['bash',str(helper),'report-incident'],env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    archive = Path(result.stdout.strip().splitlines()[-1])
    assert archive.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive) as tar:
        records = json.load(tar.extractfile('./records.json'))
        assert [r['job_id'] for r in records['jobs']] == [job]
        assert 'excluded' not in json.dumps(records)
        assert tar.extractfile('./worker.log').read().decode().strip() == 'logs --timestamps --since 2026-10-27T16:26:38Z --until 2026-10-27T16:27:00Z backbond-scanner-live-edge'
    assert before == (jobs.read_bytes(),intake.read_bytes())
    assert subprocess.run(['bash',str(helper),'report-incident','other'],env=env,capture_output=True).returncode != 0
