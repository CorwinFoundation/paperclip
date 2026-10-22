"""Exercise the narrow helper without Docker, credentials, or provider calls."""
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
DIGEST = '3b030eacc3752336235ace831a467139b8297a831558477bf59e58ac6ea9a341'


def test_grade_evidence_checks_identity_and_preserves_single_docker_operation(tmp_path):
    curl = tmp_path / 'curl'
    curl.write_text('#!/bin/sh\nprintf \'%s\' "$TEST_IDENTITY"\n')
    curl.chmod(0o755)
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import ast, sys
assert sys.argv[1:] == ['exec', '-i', 'backbond-scanner-live-edge', 'python', '-']
source = sys.stdin.read()
ast.parse(source)
assert "io.StringIO" in source
assert "grade_browser_evidence.py" in source
print('{"event":"mock_only"}')
''')
    docker.chmod(0o755)
    stem = Path('/var/tmp') / ('test-regrade-' + uuid.uuid4().hex)
    job, wrapper, output = [Path(str(stem) + ext) for ext in ('.json', '.py', '.events')]
    try:
        job.write_text(json.dumps({'rows': [{}], 'remaining_budget_usd':'2', 'remaining_call_allowance':400}))
        wrapper.write_bytes((ROOT / 'artifacts/BEAAA-43440/grade_browser_evidence.py').read_bytes())
        env = dict(os.environ, SCANNER_CURL=str(curl), SCANNER_DOCKER=str(docker), TEST_IDENTITY=json.dumps({'commit_sha':'a'*40,'status':'activated'}))
        args = ['bash',str(ROOT/'scripts/deploy/scanner-deploy.sh'),'grade-evidence','a'*40,str(job),str(wrapper),DIGEST,str(output)]
        def run():
            return subprocess.run(args, env=env, capture_output=True, text=True)
        env['TEST_IDENTITY'] = json.dumps({'commit_sha':'b'*40,'status':'activated'})
        assert run().returncode != 0
        assert not output.exists()
        env['TEST_IDENTITY'] = json.dumps({'commit_sha':'a'*40,'status':'activating'})
        assert run().returncode != 0
        env['TEST_IDENTITY'] = json.dumps({'commit_sha':'a'*40,'status':'activated'})
        result = run()
        assert result.returncode == 0, result.stderr
        assert json.loads(output.read_text()) == {'event':'mock_only'}
        assert run().returncode != 0  # Never overwrite retained events.
        output.unlink()
        wrapper.write_bytes(wrapper.read_bytes()+b'\n')
        assert run().returncode != 0
        assert not output.exists()
    finally:
        for path in (job, wrapper, output):
            path.unlink(missing_ok=True)
