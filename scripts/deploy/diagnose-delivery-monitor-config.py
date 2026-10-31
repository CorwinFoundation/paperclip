#!/usr/bin/env python3
"""Read-only AriClaw configuration diagnosis; emit names and flags, never values.

Run with sudo python3. No imports or execution of deployed service code.
"""
import ast
import json
from pathlib import Path
import shlex
import subprocess

NAMES = ('QA_DELIVERY_MONITOR', 'QA_DELIVERY_MONITOR_SINCE',
         'QA_DELIVERY_ALERT_DB_PATH', 'PAPERCLIP_API_URL', 'PAPERCLIP_COMPANY_ID',
         'PAPERCLIP_API_KEY', 'QA_ALERT_ASSIGNEE_AGENT_ID', 'QA_FOUNDER_PUSH_URL',
         'FOUNDER_PUSH_URL', 'QA_FOUNDER_PUSH_TOKEN', 'FOUNDER_PUSH_TOKEN')


def flags(values):
    return {name: {'present': name in values,
                   'nonempty': bool(values.get(name, '').strip())} for name in NAMES}


def diagnose():
    result = {'errors': [], 'expected_names': [], 'sources': {}}
    expected = set()
    for filename in ('delivery_monitor.py', 'alert_adapters.py'):
        try:
            tree = ast.parse((Path('/opt/backbond/scan-intake/server') / filename).read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Attribute)
                        and isinstance(node.func.value.value, ast.Name)
                        and node.func.value.value.id == 'os'
                        and node.func.value.attr == 'environ' and node.func.attr == 'get'
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    expected.add(node.args[0].value)
        except Exception as exc:
            result['errors'].append({'operation': 'parse_' + filename, 'error': type(exc).__name__})
    result['expected_names'] = sorted(expected)
    try:
        pid = int(subprocess.check_output(['systemctl', 'show', 'backbond-scan-intake.service',
                                           '--property=MainPID', '--value'], stderr=subprocess.DEVNULL))
        if pid <= 0:
            raise ValueError('inactive')
        values = dict(part.split('=', 1) for part in Path(f'/proc/{pid}/environ').read_bytes()
                      .decode().split('\0') if '=' in part)
        result['effective_environment'] = flags(values)
        result['monitor_enabled'] = values.get('QA_DELIVERY_MONITOR') == '1'
    except Exception as exc:
        result['errors'].append({'operation': 'read_service_process_environment', 'error': type(exc).__name__})
    # systemctl cat includes fragment/drop-ins with their paths as comments.
    # Capture privately; only emit assignment names and flags from relevant files.
    try:
        unit = subprocess.check_output(['systemctl', 'cat', 'backbond-scan-intake.service'],
                                       stderr=subprocess.DEVNULL, text=True)
        files = set()
        assignments = {}
        for line in unit.splitlines():
            line = line.strip()
            if line.startswith('EnvironmentFile='):
                for entry in shlex.split(line.split('=', 1)[1]):
                    files.add(entry.lstrip('-'))
            elif line.startswith('Environment='):
                for entry in shlex.split(line.split('=', 1)[1]):
                    if '=' in entry:
                        name, value = entry.split('=', 1)
                        assignments[name] = value
        result['unit_assignments'] = flags(assignments)
        for filename in sorted(files):
            try:
                settings = {}
                for line in Path(filename).read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith(('#', ';')) and '=' in line:
                        name, value = line.split('=', 1)
                        settings[name.strip()] = value.strip().strip('\"\'')
                result['sources'][filename] = flags(settings)
            except Exception as exc:
                result['sources'][filename] = {'error': type(exc).__name__}
    except Exception as exc:
        result['errors'].append({'operation': 'inspect_unit_configuration', 'error': type(exc).__name__})
    return result


if __name__ == '__main__':
    print(json.dumps(diagnose(), indent=2))
