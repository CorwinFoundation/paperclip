import importlib.util
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('repair', ROOT / 'scripts/deploy/repair-intake-report-mail-namespace.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def test_repair_preserves_edge_settings_and_is_repeatable(tmp_path, monkeypatch):
    source = tmp_path / 'repo/backbond/scan-intake/server/app.py'
    source.parent.mkdir(parents=True)
    source.write_text((ROOT / 'backbond/scan-intake/server/app.py').read_text())
    monkeypatch.setattr(repair, '__file__', str(tmp_path / 'repo/scripts/deploy/repair.py'))
    def mapped(value):
        path = Path(value)
        return tmp_path / str(path).lstrip('/') if str(path).startswith(('/etc/', '/opt/')) else path
    monkeypatch.setattr(repair, 'Path', mapped)
    monkeypatch.setattr(repair.os, 'geteuid', lambda: 0)
    run = Mock()
    monkeypatch.setattr(repair.subprocess, 'run', run)
    active = mapped('/opt/backbond/scan-intake/server/app.py')
    active.parent.mkdir(parents=True)
    active.write_text("def _report_mail_gateway():\n    return 'old'\n\nuntouched = True\n")
    for name in ['/etc/systemd/system/backbond-scan-intake.service.d/report-mail-grant.conf', '/etc/backbond/report-mail.env']:
        file = mapped(name)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('#Environment=\"ARE_KEY_STORE_PATH=/mail\"\n#QA_REPORT_MAIL_ENABLED=0\n' if name.endswith('.conf') else 'ARE_KEY_STORE_PATH=/mail\nQA_REPORT_MAIL_ENABLED=1\n')
    edge = mapped('/etc/backbond/scan-intake.env')
    edge.write_text('ARE_KEY_STORE_PATH=/edge\n')
    repair.repair()
    first = active.read_text()
    repair.repair()
    assert active.read_text() == first
    assert "untouched = True" in first
    assert edge.read_text() == 'ARE_KEY_STORE_PATH=/edge\n'
    assert mapped('/etc/backbond/report-mail.env').read_text().startswith('QA_REPORT_MAIL_KEY_STORE_PATH=/mail\n')
    assert '--port 8795' in mapped('/etc/systemd/system/backbond-qa-review.service.d/99-separate-port.conf').read_text()
    assert '\nARE_KEY_STORE_PATH=' not in '\n' + mapped('/etc/backbond/report-mail.env').read_text()
    config = mapped('/etc/systemd/system/backbond-scan-intake.service.d/report-mail-grant.conf')
    assert config.read_text() == 'Environment="QA_REPORT_MAIL_KEY_STORE_PATH=/mail"\n#QA_REPORT_MAIL_ENABLED=0\n'
    assert config.with_name(config.name + '.before-beaaa44392').read_text().startswith('#Environment="ARE_KEY_STORE_PATH=')
    assert run.call_count == 4
