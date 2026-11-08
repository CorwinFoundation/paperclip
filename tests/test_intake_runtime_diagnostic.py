import importlib.util
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/deploy/diagnose-intake-runtime.py"
spec = importlib.util.spec_from_file_location("intake_runtime_diagnostic", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_credential_status_reports_expiry_without_secret_or_fingerprint():
    value = {"token": "selected-secret", "status": "active", "expires_at": "2026-11-02T00:00:00Z"}
    row = module.classify_credential(value, now=NOW)
    assert row == {
        "configured": True,
        "authority": "local_metadata_only",
        "status": "active",
        "expiry_relation": "not_expired",
        "expires_at": "2026-11-02T00:00:00+00:00",
    }
    assert "selected-secret" not in str(row)
    assert "credential_sha256" not in str(row)


def test_selected_precedence_and_metadata_are_sanitized():
    files = {
        "/run/credentials/intake/qa-scanner-tokens.json":
        '{"partner-a":{"token":"systemd-secret","expires_at":"2026-10-31T00:00:00Z"}}',
        "/var/lib/backbond-qa/partner-code-tenants.json":
        '{"partner-a":"stale-secret","partner-b":"dynamic-secret"}',
        "/run/credentials/intake/default.json":
        '{"token":"dedicated-secret","status":"revoked"}',
    }
    env = {
        "QA_SCANNER_TOKEN": "legacy-secret",
        "QA_SCANNER_TOKENS_JSON": '{"partner-a":"env-secret"}',
        "CREDENTIALS_DIRECTORY": "/run/credentials/intake",
        "QA_SCANNER_CREDENTIAL_PATH": "/run/credentials/intake/default.json",
    }
    rows = module.credential_candidates(env, lambda path: files.get(str(path)), now=NOW)
    indexed = {row["selection"]: row for row in rows}
    assert indexed["default"]["status"] == "revoked"
    assert indexed["tenant_slot_1"]["status"] == "expired"
    assert indexed["tenant_slot_2"]["status"] == "configured"
    rendered = str(rows)
    for secret in ("legacy-secret", "env-secret", "systemd-secret", "stale-secret", "dynamic-secret", "dedicated-secret"):
        assert secret not in rendered
    assert "credential_sha256" not in rendered


def test_revision_metadata_uses_allowlisted_process_value_only():
    revision, source = module._safe_revision({"BACKBOND_INTAKE_REVISION": "b4d19699f06a6e981eda16a3930e22ddcb1b610d"})
    assert revision == "b4d19699f06a6e981eda16a3930e22ddcb1b610d"
    assert source == "BACKBOND_INTAKE_REVISION"
    assert module._safe_revision({"BACKBOND_INTAKE_REVISION": "Bearer secret"}) == (
        None,
        "process_environment_metadata_missing",
    )


def test_no_customer_identifiers_or_bearer_fingerprints_are_emitted():
    import json
    import hashlib
    secret = "selected-bearer-secret"
    env = {"QA_SCANNER_TOKENS_JSON": json.dumps({"customer@example.invalid": secret})}
    rendered = json.dumps(module.credential_candidates(env, lambda _: None, now=NOW))
    for private in ("customer@example.invalid", secret, hashlib.sha256(secret.encode()).hexdigest()):
        assert private not in rendered
    assert "tenant_slot_1" in rendered


def test_untrusted_revision_values_are_suppressed():
    for value in ("customer@example.invalid", "https://customer.invalid/repo", "secret-token", "a" * 64):
        assert module._safe_revision({"GIT_COMMIT": value})[0] is None


def test_invalid_expiry_does_not_crash_or_echo_input():
    for value in (1e300, float("inf"), "1e300", "customer@example.invalid", {}, []):
        row = module.classify_credential({"token": "secret", "expires_at": value}, now=NOW)
        assert row["expiry_relation"] == "unknown"
        assert "expires_at" not in row


def test_dedicated_file_failure_does_not_fall_back_to_legacy_secret():
    env = {"QA_SCANNER_TOKEN": "legacy-secret", "QA_SCANNER_CREDENTIAL_PATH": "/missing"}
    rows = module.credential_candidates(env, lambda _: None, now=NOW)
    assert rows[0]["status"] == "unconfigured"


def test_process_environment_parser_ignores_malformed_entries():
    assert module.parse_process_environ(b"BROKEN\0KEY=value=with=equals\0BAD=\xff\0") == {
        "KEY": "value=with=equals"
    }


def test_file_reader_bounds_and_invalid_encoding(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    assert module._read(path) is None
    path.write_bytes(b"\xff")
    assert module._read(path) is None


def test_main_emits_only_metadata_and_detects_process_change(monkeypatch, capsys):
    import json
    import pytest
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    props = {"MainPID": "123", "ActiveState": "active", "SubState": "running"}
    monkeypatch.setattr(module, "_service_properties", lambda: props)
    monkeypatch.setattr(module.Path, "read_bytes", lambda _: b"QA_SCANNER_TOKEN=private-token\0GIT_COMMIT=customer-id\0")
    monkeypatch.setattr(module, "_read", lambda _: None)
    module.main()
    out = capsys.readouterr().out
    assert "private-token" not in out and "customer-id" not in out
    packet = json.loads(out)
    assert packet["source"]["revision"] is None
    assert packet["scanner_acceptance"] == "not_inspected"
    calls = iter([props, {"MainPID": "124"}])
    monkeypatch.setattr(module, "_service_properties", lambda: next(calls))
    with pytest.raises(RuntimeError):
        module.main()
    assert capsys.readouterr().out == ""


def test_privileged_reader_rejects_unapproved_runtime_paths(tmp_path):
    path = tmp_path / 'secret.json'
    path.write_text('private')
    assert module._read(path) is None
    assert module._read(Path('/etc/shadow')) is None
    assert module._read(Path('/run/credentials/other-service/default.json')) is None


def test_privileged_reader_descriptor_boundary(monkeypatch):
    import os
    import stat
    from types import SimpleNamespace
    path = Path('/run/credentials/backbond-scan-intake.service/default.json')
    for failure in ('none', 'parent_owner', 'parent_write', 'file_owner',
                    'file_mode', 'fifo', 'directory', 'oversized', 'hardlink', 'symlink'):
        opened = []
        closed = []
        reads = []
        def open_fd(name, flags, **kwargs):
            assert flags & os.O_NOFOLLOW
            if name == 'default.json' and failure == 'symlink':
                raise OSError('symlink refused')
            opened.append(name)
            return len(opened)
        def fstat(fd):
            is_file = opened[fd - 1] == 'default.json'
            mode = (stat.S_IFREG | 0o600) if is_file else (stat.S_IFDIR | 0o755)
            uid = 0
            size = 2
            links = 1
            if not is_file:
                if failure == 'parent_owner': uid = 1000
                if failure == 'parent_write': mode |= 0o020
            else:
                if failure == 'file_owner': uid = 1000
                if failure == 'file_mode': mode |= 0o004
                if failure == 'fifo': mode = stat.S_IFIFO | 0o600
                if failure == 'directory': mode = stat.S_IFDIR | 0o700
                if failure == 'oversized': size = module.MAX_INPUT + 1
                if failure == 'hardlink': links = 2
            return SimpleNamespace(st_mode=mode, st_uid=uid, st_size=size, st_nlink=links)
        chunks = iter([b'{}', b''])
        def read(fd, count):
            reads.append(fd)
            return next(chunks)
        with monkeypatch.context() as m:
            m.setattr(module.os, 'open', open_fd)
            m.setattr(module.os, 'fstat', fstat)
            m.setattr(module.os, 'read', read)
            m.setattr(module.os, 'close', closed.append)
            assert module._read(path) == ('{}' if failure == 'none' else None)
        assert sorted(closed) == list(range(1, len(opened) + 1))
        if failure != 'none': assert reads == []


def test_generic_revision_is_unverified_and_never_source(monkeypatch, capsys):
    import json
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, '_service_properties', lambda: {'MainPID': '123'})
    monkeypatch.setattr(module.Path, 'read_bytes', lambda _: b'GIT_COMMIT=' + b'a' * 40 + b'\0')
    monkeypatch.setattr(module, '_read', lambda _: None)
    module.main()
    source = json.loads(capsys.readouterr().out)['source']
    assert source['revision'] is None
    assert source['verification'] == 'unknown'
    assert source['unverified_environment_metadata']['revision'] == 'a' * 40
    assert source['unverified_environment_metadata']['verification'] == 'unverified'


def load_installer():
    path = MODULE_PATH.parent / 'install-intake-diagnostics.py'
    spec = importlib.util.spec_from_file_location('installer', path)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    return installer


def test_installer_idempotent_rollback_and_validation_failure(tmp_path, monkeypatch):
    import hashlib
    import pytest
    installer = load_installer()
    stage = tmp_path / 'stage'
    stage.mkdir()
    reader = b'reviewed reader\n'
    (stage / 'diagnose-intake-runtime.py').write_bytes(reader)
    monkeypatch.setattr(installer, 'STAGE', stage)
    monkeypatch.setattr(installer, 'READER_SHA256', hashlib.sha256(reader).hexdigest())
    targets = [tmp_path / name for name in ('reader', 'wrapper', 'grant')]
    for name, target in zip(('READER', 'WRAPPER', 'GRANT'), targets):
        monkeypatch.setattr(installer, name, target)
    # Trust boundary itself tested separately below; this tests transaction logic.
    monkeypatch.setattr(installer, 'trusted', lambda *a, **k: None)
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.sys, 'argv', ['installer', 'install'])
    validations = []
    monkeypatch.setattr(installer.subprocess, 'run', lambda cmd, **k: validations.append(cmd))
    installer.main()
    before = [(p.stat().st_ino, p.read_bytes()) for p in targets]
    installer.main()
    assert before == [(p.stat().st_ino, p.read_bytes()) for p in targets]
    assert any('-cf' in command for command in validations)
    targets[1].write_text('foreign')
    with pytest.raises(RuntimeError, match='differs'): installer.main()
    targets[1].write_bytes(installer.WRAPPER_BYTES)
    monkeypatch.setattr(installer.sys, 'argv', ['installer', 'rollback'])
    installer.main()
    assert not any(p.exists() for p in targets)
    installer.main()
    monkeypatch.setattr(installer.sys, 'argv', ['installer', 'install'])
    counter = [0]
    def fail_final(cmd, **kwargs):
        counter[0] += 1
        if counter[0] == 3: raise RuntimeError('aggregate validation failed')
    monkeypatch.setattr(installer.subprocess, 'run', fail_final)
    with pytest.raises(RuntimeError): installer.main()
    assert not any(p.exists() for p in targets)
    monkeypatch.setattr(installer, 'READER_SHA256', '0' * 64)
    with pytest.raises(RuntimeError, match='hash mismatch'): installer.main()


def test_installer_rejects_untrusted_staging(tmp_path):
    import pytest
    installer = load_installer()
    path = tmp_path / 'staged'
    path.write_text('reader')
    # /tmp is world writable even on a root-run CI test.
    with pytest.raises(RuntimeError, match='unsafe'): installer.trusted(path)
