import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('handoff', ROOT / 'scripts/deploy/issue-report-mail-handoff.py')
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class HandoffTest(unittest.TestCase):
    def test_extracted_worker_compiles_without_running_issuer(self):
        import ast
        tree = ast.parse(Path(handoff.__file__).read_text())
        assignment = next(node for node in ast.walk(tree)
                          if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'worker' for t in node.targets))
        worker = eval(compile(ast.Expression(assignment.value), '<extract>', 'eval'),
                      {'Path': Path, '__file__': handoff.__file__,
                       'args': type('Args', (), {'ttl_seconds': 7776000, 'reissue': True})()})
        compile(worker, '<container-worker>', 'exec')
        self.assertIn('print(json.dumps(issue_packet(ttl_seconds=7776000, reissue=True)))', worker)

    def test_failure_diagnostics_show_cause_and_hide_packet(self):
        import contextlib
        import io
        secret = 'S' * 64
        exc = subprocess.CalledProcessError(1, ['docker', secret],
            output=b'private exported grant',
            stderr=('old line\n' * 20 + 'ValueError: maintained runtime store mismatch\n'
                    + 'bearer=short-secret ' + secret).encode())
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            handoff.report_failure(exc)
            handoff.report_failure(ValueError('maintained issuer store is missing'))
            handoff.report_failure(KeyError('short-secret'))
        diagnostic = output.getvalue()
        self.assertIn('CalledProcessError: container command exited with status 1', diagnostic)
        self.assertIn('ValueError: maintained runtime store mismatch', diagnostic)
        self.assertIn('ValueError: maintained issuer store is missing', diagnostic)
        self.assertNotIn(secret, diagnostic)
        self.assertNotIn('short-secret', diagnostic)
        self.assertNotIn('private exported grant', diagnostic)
        self.assertLessEqual(diagnostic.count('old line'), 10)

    def test_retry_preserves_grant_and_unrelated_identity(self):
        from api.edge_gateway import KeyStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keys.json'
            KeyStore(path, clock=lambda: 100).issue(token='existing-secret', key_id='operations',
                tenant_id='other', ttl_seconds=90000, scopes=['POST /scanner/v1/scans'])
            before = json.loads(path.read_text())['keys']['existing-secret']
            first = handoff.issue_packet(path, now=100)
            second = handoff.issue_packet(path, now=101)
            self.assertEqual(first, second)
            self.assertEqual(first['expires_at'], 7776100)
            self.assertEqual(first['key_store']['keys'][first['bearer']]['scopes'], [handoff.SCOPE])
            self.assertNotIn('existing-secret', first['key_store']['keys'])
            self.assertEqual(before, json.loads(path.read_text())['keys']['existing-secret'])
            with self.assertRaises(ValueError):
                handoff.issue_packet(path, now=7776101)
            self.assertEqual(len(json.loads(path.read_text())['keys']), 2)

    def test_encryption_failure_preserves_existing_packet_and_hides_bearer(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'packet.gpg'
            target.write_bytes(b'existing encrypted packet')
            with patch.object(handoff.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'gpg')) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    handoff.encrypt_packet(b'secret bearer payload', target, 'A' * 40)
                self.assertNotIn('secret bearer payload', str(run.call_args.args))
                self.assertEqual(run.call_args.kwargs['input'], b'secret bearer payload')
            self.assertEqual(target.read_bytes(), b'existing encrypted packet')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_plain_output_is_private_and_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'packet.json'
            target.write_bytes(b'old')
            target.chmod(0o644)
            handoff.write_plain_packet(b'{"synthetic": true}', target)
            self.assertEqual(target.read_bytes(), b'{"synthetic": true}')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_override_and_explicit_reissue(self):
        from api.edge_gateway import KeyStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keys.json'
            KeyStore(path).issue(token='unrelated', key_id='other', tenant_id='other', ttl_seconds=100)
            before = json.loads(path.read_text())['keys']['unrelated']
            first = handoff.issue_packet(path, now=100, ttl_seconds=3600)
            self.assertEqual(first['expires_at'], 3700)
            self.assertEqual(handoff.issue_packet(path, now=101), first)
            renewed = handoff.issue_packet(path, now=4000, reissue=True)
            self.assertEqual(renewed['expires_at'], 4000 + 7776000)
            self.assertEqual(renewed['bearer'], first['bearer'])
            self.assertEqual(before, json.loads(path.read_text())['keys']['unrelated'])
            KeyStore(path).revoke(handoff.KEY_ID)
            with self.assertRaises(ValueError):
                handoff.issue_packet(path, now=4001, reissue=True)
            for invalid in (0, -1, float('inf'), float('nan')):
                with self.assertRaises(ValueError):
                    handoff.issue_packet(path, now=4001, ttl_seconds=invalid)

    def test_missing_store_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.json'
            with self.assertRaises(ValueError):
                handoff.issue_packet(path, now=100)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
