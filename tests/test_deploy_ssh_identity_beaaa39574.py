import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/deploy/install_ssh_identity.sh"
requires_key_tools = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None or shutil.which("openssl") is None,
    reason="deploy identity fixtures require openssh-client and openssl",
)


def test_workflow_uses_secret_safe_identity_normalizer():
    workflow = (ROOT / ".github/workflows/scanner-edge-image.yml").read_text()
    assert 'scripts/deploy/install_ssh_identity.sh "$deploy_key" "$known_hosts" "$DEPLOY_HOST"' in workflow
    assert "ssh-keygen -y -f \"$deploy_key\" >/dev/null" in workflow
    assert "printf '%s' \"$DEPLOY_SSH_PRIVATE_KEY\"" not in workflow


def _install(tmp_path, value, index):
    key = tmp_path / f"key-{index}"
    hosts = tmp_path / f"hosts-{index}"
    env = os.environ | {
        "DEPLOY_SSH_PRIVATE_KEY": value,
        "DEPLOY_SSH_KNOWN_HOSTS": "example.invalid ssh-ed25519 AAAA",
    }
    subprocess.run(
        [str(INSTALLER), str(key), str(hosts), "example.invalid"],
        env=env,
        check=True,
    )
    subprocess.run(
        ["ssh-keygen", "-y", "-f", str(key)],
        check=True,
        stdout=subprocess.DEVNULL,
    )


@requires_key_tools
def test_normalizer_accepts_raw_escaped_and_base64_keys(tmp_path):
    source = tmp_path / "source"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source)],
        check=True,
    )
    raw = source.read_text()
    armorless = "".join(raw.splitlines()[1:-1])
    variants = (
        raw.replace("\n", "\r\n"),
        raw.rstrip("\n").replace("\n", r"\n"),
        base64.b64encode(raw.encode()).decode(),
        base64.urlsafe_b64encode(raw.encode()).decode().rstrip("="),
        f'"{base64.b64encode(raw.encode()).decode()}"',
        armorless,
    )

    for index, value in enumerate(variants):
        _install(tmp_path, value, index)


@requires_key_tools
def test_normalizer_accepts_der_private_key(tmp_path):
    pem = tmp_path / "rsa.pem"
    der = tmp_path / "rsa.der"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(pem)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(pem), "-outform", "DER", "-out", str(der)],
        check=True,
    )
    _install(tmp_path, base64.b64encode(der.read_bytes()).decode(), "der")


@requires_key_tools
def test_normalizer_rejects_pin_for_another_host(tmp_path):
    source = tmp_path / "source"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source)],
        check=True,
    )
    key = tmp_path / "key"
    hosts = tmp_path / "hosts"
    env = os.environ | {
        "DEPLOY_SSH_PRIVATE_KEY": source.read_text(),
        "DEPLOY_SSH_KNOWN_HOSTS": "other.invalid ssh-ed25519 AAAA",
    }
    result = subprocess.run(
        [str(INSTALLER), str(key), str(hosts), "example.invalid"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert result.stderr == "deploy known-hosts pin does not contain the configured deploy host\n"
