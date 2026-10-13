from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy/provision_host_dns.py"
spec = importlib.util.spec_from_file_location("provision_host_dns", SCRIPT)
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)
NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def key(tag=38696, start="2024-07-18T00:00:00Z", end="", digest="A" * 64):
    until = f' validUntil="{end}"' if end else ""
    return (f'<KeyDigest validFrom="{start}"{until}><KeyTag>{tag}</KeyTag>'
            f'<Algorithm>8</Algorithm><DigestType>2</DigestType>'
            f'<Digest>{digest}</Digest></KeyDigest>')


def xml(*keys):
    return ('<TrustAnchor><Zone>.</Zone>' + ''.join(keys) + '</TrustAnchor>').encode()


def test_current_keys_survive_and_expired_future_keys_are_excluded():
    payload = xml(key(), key(20326), key(19036, end="2019-01-11T00:00:00Z"),
                  key(12345, start="2027-01-01T00:00:00Z"))
    result = dns.render_anchors(payload, NOW).decode()
    assert "20326" in result and "38696" in result
    assert "19036" not in result and "12345" not in result


@pytest.mark.parametrize("payload", [
    xml(key(end="2026-10-12T00:00:00Z")),
    xml(key(digest="bad")),
    xml(key(start="2024-07-18T00:00:00")),
    b'<TrustAnchor><Zone>example.com.</Zone></TrustAnchor>',
    b'x' * (dns.MAX_BYTES + 1),
])
def test_invalid_or_empty_current_anchor_set_is_rejected(payload):
    with pytest.raises(ValueError):
        dns.render_anchors(payload, NOW)


def test_install_is_atomic_and_idempotent(tmp_path):
    target = tmp_path / "iana-root.positive"
    records = dns.render_anchors(xml(key()), NOW)
    with patch.object(dns.subprocess, "run") as run:
        assert dns.install_anchors(records, target)
        assert not dns.install_anchors(records, target)
    assert target.read_bytes() == records
    assert run.call_count == 2
    assert run.call_args_list[0].args[0] == ["systemctl", "restart", "systemd-resolved"]
    assert run.call_args_list[1].args[0] == ["resolvectl", "query", ".", "--type=DNSKEY"]
    assert not list(tmp_path.glob(".iana-root-*"))


def test_duplicate_root_anchor_source_is_rejected_before_mutation(tmp_path):
    target = tmp_path / "iana-root.positive"
    legacy = tmp_path / "iana-root-2026.positive"
    records = dns.render_anchors(xml(key()), NOW)
    legacy.write_bytes(records)
    with patch.object(dns.subprocess, "run") as run:
        with pytest.raises(ValueError, match="migrate"):
            dns.install_anchors(records, target)
    assert not target.exists()
    assert legacy.read_bytes() == records
    run.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
def test_failed_resolver_validation_restores_previous_file(tmp_path, existing):
    target = tmp_path / "iana-root.positive"
    old = b"previous anchors\n"
    if existing:
        target.write_bytes(old)
    failure = subprocess.CalledProcessError(1, "resolvectl")
    with patch.object(dns.subprocess, "run", side_effect=[None, failure, None]):
        with pytest.raises(subprocess.CalledProcessError):
            dns.install_anchors(dns.render_anchors(xml(key()), NOW), target)
    assert target.read_bytes() == old if existing else not target.exists()
