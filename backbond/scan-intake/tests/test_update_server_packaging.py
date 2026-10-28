"""Exercise an image-shaped update twice without host mutation."""
import os
from pathlib import Path
import shutil
import subprocess


def test_extracted_image_update_is_complete_and_idempotent(tmp_path):
    source = Path(__file__).resolve().parents[1]
    staged = tmp_path / 'payload'
    shutil.copytree(source, staged)
    shutil.copyfile(source.parents[1] / 'api/edge_gateway.py', staged / 'server/edge_gateway.py')
    dest = tmp_path / 'installed'
    script = (staged / 'update-server.sh').read_text()
    script = script.replace('DEST=/opt/backbond/scan-intake/server', f'DEST={dest}')
    script = script.replace('/var/backups/backbond-scan-intake/', str(tmp_path / 'backups') + '/')
    script = script.replace('/usr/local/bin/backbond-report-mail', str(tmp_path / 'client'))
    script = script.replace('/usr/local/sbin/backbond-admit-retained-voice', str(tmp_path / 'admit'))
    script = script.replace('/etc/systemd/system/$SERVICE.d', str(tmp_path / 'unit.d'))
    script = script.replace('-o root -g root ', '')
    (staged / 'update-server.sh').write_text(script)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'systemctl.log'
    fake = bin_dir / 'systemctl'
    fake.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}')
    for _ in range(2):
        subprocess.run(['bash', str(staged / 'update-server.sh')], env=env, check=True, capture_output=True)
    for module in (staged / 'server').glob('*.py'):
        assert (dest / module.name).read_bytes() == module.read_bytes()
    assert log.read_text().splitlines().count('restart backbond-scan-intake.service') == 1
    assert (tmp_path / 'unit.d/zz-tracked-runtime.conf').read_text() == f'[Service]\nWorkingDirectory={dest}\n'
    assert log.read_text().splitlines().count('daemon-reload') == 1


def test_missing_service_without_configuration_fails_before_install(tmp_path):
    source = Path(__file__).resolve().parents[1]
    script = (source / 'update-server.sh').read_text()
    script = script.replace('/etc/backbond/scan-intake.env', str(tmp_path / 'missing-config'))
    staged = tmp_path / 'update-server.sh'
    staged.write_text(script)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'systemctl'
    fake.write_text('#!/bin/sh\nexit 1\n')
    fake.chmod(0o755)
    result = subprocess.run(['bash', str(staged)], capture_output=True, text=True,
                            env=dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}'))
    assert result.returncode == 1
    assert 'missing operator configuration:' in result.stderr
    assert not (tmp_path / 'installed').exists()
