import base64
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/deploy/install_ssh_identity.sh"


def test_workflow_uses_secret_safe_identity_normalizer():
    workflow = (ROOT / ".github/workflows/scanner-edge-image.yml").read_text()
    assert "scripts/deploy/install_ssh_identity.sh" in workflow
    assert "ssh-keygen -y -f \"$deploy_key\" >/dev/null" in workflow
    assert "printf '%s' \"$DEPLOY_SSH_PRIVATE_KEY\"" not in workflow


def test_normalizer_accepts_raw_escaped_and_base64_keys(tmp_path):
    source = tmp_path / "source"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source)],
        check=True,
    )
    raw = source.read_text()
    variants = (
        raw.replace("\n", "\r\n"),
        raw.rstrip("\n").replace("\n", r"\n"),
        base64.b64encode(raw.encode()).decode(),
    )

    for index, value in enumerate(variants):
        key = tmp_path / f"key-{index}"
        hosts = tmp_path / f"hosts-{index}"
        env = os.environ | {
            "DEPLOY_SSH_PRIVATE_KEY": value,
            "DEPLOY_SSH_KNOWN_HOSTS": "example.invalid ssh-ed25519 AAAA",
        }
        subprocess.run([str(INSTALLER), str(key), str(hosts)], env=env, check=True)
        subprocess.run(
            ["ssh-keygen", "-y", "-f", str(key)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
