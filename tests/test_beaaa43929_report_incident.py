import hashlib, json, os, sqlite3, subprocess, tarfile
from pathlib import Path
HELPER = Path(__file__).resolve().parents[1] / 'scripts/deploy/scanner-deploy.sh'
def test_incident_scope_and_no_dispatch(tmp_path):
    ident = 'eb90ce19-91f5-4c98-a7b0-d1a489b953ea'
    ref = 'BB-' + hashlib.sha256(('starter-public:' + ident).encode()).hexdigest()[:20]
    helper = tmp_path / 'helper'
    helper.write_text(HELPER.read_text().replace('BB-e4b6027ddf30702e50ad', ref))
    intake, jobs = tmp_path / 'intake.sqlite', tmp_path / 'jobs.sqlite'
    with sqlite3.connect(intake) as db:
        db.execute('CREATE TABLE scans (scan_id TEXT, value TEXT)')
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
    assert archive.stat().st_mode & 0o777 == 0o400
    with tarfile.open(archive) as tar:
        records = json.load(tar.extractfile('./records.json'))
        assert [r['job_id'] for r in records['jobs']] == [job]
        assert 'excluded' not in json.dumps(records)
        assert tar.extractfile('./worker.log').read().decode().strip() == ''
    assert before == (jobs.read_bytes(),intake.read_bytes())
    assert subprocess.run(['bash',str(helper),'report-incident','other'],env=env,capture_output=True).returncode != 0


def test_missing_intake_preserves_accepted_raw_result_and_hashes(tmp_path):
    ident = 'eb90ce19-91f5-4c98-a7b0-d1a489b953ea'
    raw = ' {"error": "report preparation failed", "rows": []} \n'
    accepted = tmp_path / 'accepted.sqlite'
    with sqlite3.connect(accepted) as db:
        db.execute('CREATE TABLE accepted_scans (scan_id TEXT, result_json TEXT)')
        db.executemany('INSERT INTO accepted_scans VALUES (?,?)', [(ident, raw), ('other', 'unrelated-private-data')])
        db.execute('CREATE TABLE scan_case_progress (scan_id TEXT, progress_json TEXT)')
        db.executemany('INSERT INTO scan_case_progress VALUES (?,?)', [(ident, '{"usable":100}'),('other','unrelated-private-data')])
    docker = tmp_path / 'docker'
    docker.write_text('#!/bin/sh\nexit 1\n')
    docker.chmod(0o755)
    before = accepted.read_bytes()
    env = {**os.environ, 'SCANNER_DOCKER':str(docker), 'SCANNER_ACCEPTED_SCAN_STORE':str(accepted), 'SCANNER_INCIDENT_INTAKE_DB':str(tmp_path/'missing'), 'SCANNER_FULL_SCAN_JOB_STORE':str(tmp_path/'missing-jobs'), 'SCANNER_EVIDENCE_STATE_DIR':str(tmp_path), 'SCANNER_EVIDENCE_EXPORT_DIR':str(tmp_path/'exports')}
    result = subprocess.run(['bash',str(HELPER),'report-incident'],env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    archive = Path(result.stdout.strip().splitlines()[-1])
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile('./manifest.json'))
        name = next(n for n in manifest['files'] if n.endswith('.result_json'))
        assert tar.extractfile('./'+name).read() == raw.encode()
        for name, digest in manifest['files'].items():
            assert hashlib.sha256(tar.extractfile('./'+name).read()).hexdigest() == digest
        records = tar.extractfile('./records.json').read()
        assert b'unrelated-private-data' not in records
        assert b'usable' in records
        summary = json.load(tar.extractfile('./summary.json'))
        assert any(s['source']=='intake' and s['status']=='absent' for s in summary['sources'])
    assert accepted.read_bytes() == before
    assert archive.stat().st_mode & 0o777 == 0o400
    assert hashlib.sha256(archive.read_bytes()).hexdigest() in Path(str(archive)+'.sha256').read_text()
