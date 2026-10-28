import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/deploy/install-report-mail-grant.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
    def test_installs_systemd_credential_and_rejects_wrong_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'etc/backbond').mkdir(parents=True)
            packet = dict(tenant_id='81b0c474-2b92-4397-99e6-995e6e5e85ea',
                          key_id='bac-report-mail-beaaa44023', expires_at=200, bearer='synthetic')
            record = dict(tenant_id=packet['tenant_id'], key_id=packet['key_id'],
                          expires_at=200, status='active', scopes=['POST /scan/v1/reports/mail'])
            packet['key_store'] = dict(version=1, keys={'synthetic': record})
            source = root / 'packet.json'
            source.write_text(json.dumps(packet))
            def mapped(path):
                return root / str(path).lstrip('/') if str(path).startswith('/etc/') else Path(path)
            with patch.object(installer, 'Path', side_effect=mapped), patch.object(installer.os, 'geteuid', return_value=0), patch.object(installer.time, 'time', return_value=100):
                installer.install(source)
                dest = root / 'etc/backbond/report-mail-grant.json'
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(dest.read_text()), packet['key_store'])
                unit = (root / 'etc/systemd/system/backbond-scan-intake.service.d/report-mail-grant.conf').read_text()
                self.assertIn('ARE_KEY_STORE_PATH=%d/report-mail-grant.json', unit)
                self.assertNotIn('synthetic', unit)
                record['scopes'] = ['GET /wrong']
                source.write_text(json.dumps(packet))
                with self.assertRaises(ValueError):
                    installer.install(source)
