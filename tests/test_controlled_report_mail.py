import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('mail', Path(__file__).resolve().parents[1] / 'scripts/controlled_report_mail.py')
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'accepted.json'
        self.db = self.root / 'outbox.sqlite'
        self.m = dict(run_id='sole-new-run', report_id='report-1', revision='r1', reviewer_decision='SHIP', qa_decision='PASS', reviewer_acceptance_ref='review/1', qa_acceptance_ref='qa/1')
        for kind, data in [('pdf', b'%PDF-accepted'), ('html', b'<html>accepted</html>')]:
            (self.root / ('report.' + kind)).write_bytes(data)
            self.m[kind] = dict(path='report.' + kind, sha256=mail.digest(data))
        self.save()
        self.config = self.root / "service-config.json"
        self.config.write_text(json.dumps(dict(recipient=mail.RECIPIENT, allowed_recipients=[mail.RECIPIENT], run_id="sole-new-run", report_id="report-1", revision="r1")))
        self.calls = []
    def save(self):
        self.path.write_text(json.dumps(self.m))
    def transport(self, payload, key):
        self.calls.append((payload, key))
        return 201, {'id': 'provider-1'}
    def test_exact_bytes_fixed_recipient_restart_replay(self):
        box = mail.Outbox(self.db)
        self.assertEqual(box.send(self.path, self.transport, self.config)['state'], 'provider_accepted')
        self.assertIsNone(box.receipt()['inbox_evidence'])
        self.assertEqual(self.calls[0][0]['to'], ['info@backbond.ai'])
        self.assertEqual(self.calls[0][0]['from'], 'BackBond <reports@backbond.ai>')
        self.assertEqual(self.calls[0][0]['reply_to'], 'info@backbond.ai')
        self.assertEqual([mail.base64.b64decode(a['content']) for a in self.calls[0][0]['attachments']], [b'%PDF-accepted', b'<html>accepted</html>'])
        mail.Outbox(self.db).send(self.path, self.transport, self.config)
        self.assertEqual(len(self.calls), 1)
        box.reconcile('inbox_received', 'mailbox/receipt-1')
        self.assertEqual(box.receipt()['inbox_evidence'], 'mailbox/receipt-1')
    def test_bad_acceptance_recipient_hash(self):
        for field, value in [('recipient', 'customer@example.com'), ('revision', ''), ('reviewer_decision', 'FIX'), ('qa_decision', 'PENDING')]:
            original = dict(self.m)
            self.m[field] = value
            self.save()
            with self.assertRaises(ValueError):
                mail.Outbox(self.db).send(self.path, self.transport, self.config)
            self.m = original
        self.save()
        (self.root / 'report.pdf').write_bytes(b'changed-after-review')
        with self.assertRaises(ValueError):
            mail.Outbox(self.db).send(self.path, self.transport, self.config)
        self.assertEqual(self.calls, [])
    def test_default_configuration_is_unarmed(self):
        with self.assertRaises(ValueError):
            mail.Outbox(self.db).send(self.path, self.transport, Path(__file__).resolve().parents[1] / 'config/controlled-report-mail.json')
        self.assertEqual(self.calls, [])
    def test_config_override_rejected(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps(dict(recipient='other@example.com', allowed_recipients=['other@example.com'])))
        with self.assertRaises(ValueError):
            mail.Outbox(self.db).send(self.path, self.transport, config)
    def test_installed_paths_match_service_custody(self):
        self.assertEqual(str(mail.DEFAULT_CONFIG), '/etc/backbond-scan-intake/controlled-report-mail.json')
        self.assertEqual(str(mail.DEFAULT_OUTBOX), '/var/lib/backbond-scan-intake/controlled-report-mail/mail.sqlite')

    def test_cli_refuses_alternate_paths_before_transport(self):
        with patch.object(mail, 'resend_transport') as transport:
            for alternate in (str(self.root / 'second.sqlite'),
                              '/var/lib/backbond/controlled-report-mail.sqlite'):
                with self.assertRaises(ValueError):
                    mail.main(['--outbox', alternate, 'status'])
            with self.assertRaises(ValueError):
                mail.main(['send', '--config', str(self.config), '--acceptance', str(self.path)])
            with self.assertRaises(ValueError):
                mail.main(['send', '--config', '/etc/backbond/controlled-report-mail.json', '--acceptance', str(self.path)])
            transport.assert_not_called()
        self.assertFalse((self.root / 'second.sqlite').exists())
    def test_missing_artifacts_raise_value_error(self):
        for kind in ('pdf', 'html'):
            old = self.m.pop(kind)
            self.save()
            with self.assertRaises(ValueError):
                mail.prepare(self.path, self.config)
            self.m[kind] = old
        self.assertEqual(self.calls, [])
    def test_inbox_evidence_cannot_be_overwritten(self):
        box = mail.Outbox(self.db)
        box.send(self.path, self.transport, self.config)
        box.reconcile('inbox_received', 'mailbox/original')
        with self.assertRaises(ValueError):
            box.reconcile('inbox_received', 'mailbox/replacement')
        self.assertEqual(box.receipt()['inbox_evidence'], 'mailbox/original')
    def test_rebind_rejected(self):
        box = mail.Outbox(self.db)
        box.send(self.path, self.transport, self.config)
        for field in ('revision', 'run_id', 'report_id'):
            old = self.m[field]
            self.m[field] = 'new'
            self.save()
            with self.assertRaises(ValueError):
                box.send(self.path, self.transport, self.config)
            self.m[field] = old
        self.assertEqual(len(self.calls), 1)
    def test_concurrent_claims(self):
        mail.Outbox(self.db)
        def attempt(_):
            return mail.Outbox(self.db).send(self.path, self.transport, self.config)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(attempt, range(16)))
        self.assertEqual(len(self.calls), 1)
    def test_failed_unknown_no_retries(self):
        for status, expected in [(422, 'failed'), (429, 'unknown'), (500, 'unknown'), (201, 'unknown')]:
            db = self.root / str(status)
            def transport(payload, key):
                self.calls.append(key)
                return status, {}
            box = mail.Outbox(db)
            self.assertEqual(box.send(self.path, transport, self.config)['state'], expected)
            mail.Outbox(db).send(self.path, transport, self.config)
        self.assertEqual(len(self.calls), 4)
    def test_timeout_reconcile(self):
        def timeout(payload, key):
            self.calls.append(key)
            raise TimeoutError()
        box = mail.Outbox(self.db)
        self.assertEqual(box.send(self.path, timeout, self.config)['state'], 'unknown')
        mail.Outbox(self.db).send(self.path, self.transport, self.config)
        with self.assertRaises(ValueError):
            box.reconcile('inbox_received', 'mailbox')
        box.reconcile('provider_accepted', 'provider-lookup/1', 'message-1')
        box.reconcile('inbox_received', 'mailbox/1')
        self.assertEqual(len(self.calls), 1)
    def test_crash_pending_no_retry(self):
        def crash(payload, key):
            self.calls.append(key)
            raise SystemExit()
        with self.assertRaises(SystemExit):
            mail.Outbox(self.db).send(self.path, crash, self.config)
        box = mail.Outbox(self.db)
        self.assertEqual(box.send(self.path, self.transport, self.config)['state'], 'pending')
        box.reconcile('failed', 'provider-confirmed-no-acceptance/1')
        box.send(self.path, self.transport, self.config)
        self.assertEqual(len(self.calls), 1)

if __name__ == '__main__':
    unittest.main()
