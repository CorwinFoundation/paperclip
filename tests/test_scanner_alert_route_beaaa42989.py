import importlib.util
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('alert', Path(__file__).parents[1] / 'scripts/deploy/backbond-paperclip-alert.py')
alert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alert)


def test_routes_and_rejection():
    response = MagicMock()
    response.__enter__.return_value.status = 201
    for unit, destination in [('backbond-scanner-live.service', 'BEAAA-42989'), ('are-scanner-scanlog-backup.service', 'BEAAA-18049'), ('are-scanner-scanlog-restoretest.service', 'BEAAA-18049')]:
        with patch.object(alert.sys, 'argv', ['alert', unit]), patch.object(alert.urllib.request, 'urlopen', return_value=response) as send:
            assert alert.main() == 0
            request = send.call_args.args[0]
            assert request.full_url.endswith(f'/{destination}/comments')
            assert json.loads(request.data)['resume'] is True
    with patch.object(alert.sys, 'argv', ['alert', 'unknown.service']), patch.object(alert.urllib.request, 'urlopen') as send:
        assert alert.main() == 64
        send.assert_not_called()


def test_conflict_fallback_keeps_scanner_route():
    response = MagicMock()
    response.__enter__.return_value.status = 201
    with patch.object(alert.sys, 'argv', ['alert', 'backbond-scanner-live.service']), patch.object(alert.urllib.request, 'urlopen', side_effect=[HTTPError('url', 409, 'conflict', {}, None), response]) as send:
        assert alert.main() == 0
        assert send.call_count == 2
        assert all(c.args[0].full_url.endswith('/BEAAA-42989/comments') for c in send.call_args_list)
        assert json.loads(send.call_args.args[0].data)['resume'] is False
