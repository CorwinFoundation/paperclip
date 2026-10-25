"""Exercise the narrow helper without Docker, credentials, or provider calls."""
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
DIGEST = '3c291054d7e113a9eebaf6f589ea43d372e510a1b30f5a565efae6155afe9d6c'


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
        job.write_text(json.dumps({'rows': [{}], 'remaining_budget_usd':'2000', 'remaining_call_allowance':400}))
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
        for change in ({'remaining_call_allowance': 401}, {'rows': [{}] * 401}):
            job.write_text(json.dumps({'rows': [{}], 'remaining_call_allowance': 400, **change}))
            assert run().returncode != 0
            assert not output.exists()
        job.write_text(json.dumps({'rows': [{}], 'remaining_budget_usd': '2000', 'remaining_call_allowance': 400}))
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


def test_metered_judge_tracks_cost_above_old_ceiling_and_stops_at_call_limit():
    import ast
    from decimal import Decimal
    from types import SimpleNamespace
    import pytest

    source = (ROOT / 'artifacts/BEAAA-43440/grade_browser_evidence.py').read_text()
    tree = ast.parse(source)
    judge = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MeteredJudge')
    events = []
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {'usage': {'cost': 3}, 'choices': [{'message': {'content': 'ok'}}]},
    )
    namespace = dict(
        Decimal=Decimal,
        reserved=Decimal(0), known_cost=Decimal(0), calls=0, unknown_cost_calls=0,
        prompt_rate=Decimal('0.001'), completion_rate=Decimal('0.001'),
        job={'remaining_budget_usd': '2', 'remaining_call_allowance': 1},
        current={'target': 'fixture', 'case_id': 'fixture', 'source_sha256': 'fixture'},
        grader=SimpleNamespace(JUDGE_URL='fixture', JUDGE_MODEL='fixture', JUDGE_TEMPERATURE=0, JUDGE_SEED=1),
        key='test-secret', emit=events.append, now=lambda: 'fixture',
        httpx=SimpleNamespace(post=lambda *args, **kwargs: response),
    )
    exec(compile(ast.Module(body=[judge], type_ignores=[]), '<metered-judge>', 'exec'), namespace)
    client = namespace['MeteredJudge']()
    assert client.complete(system='x' * 5000, user='fixture') == 'ok'
    assert namespace['known_cost'] == namespace['reserved'] == Decimal(3)
    assert namespace['calls'] == 1
    with pytest.raises(RuntimeError, match='call allowance exhausted'):
        client.complete(system='fixture', user='fixture')
    assert namespace['calls'] == 1
