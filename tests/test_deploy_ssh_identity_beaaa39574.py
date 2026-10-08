from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/deploy/install_ssh_identity.sh"


def test_workflow_uses_secret_safe_identity_normalizer():
    workflow = (ROOT / ".github/workflows/scanner-edge-image.yml").read_text()
    assert "normalized_key=${DEPLOY_SSH_PRIVATE_KEY//$'\\r'/}" in workflow
    assert "normalized_key=${normalized_key//\\\\n/$'\\n'}" in workflow
    assert "ssh-keygen -y -f \"$deploy_key\" >/dev/null" in workflow
    assert "printf '%s' \"$DEPLOY_SSH_PRIVATE_KEY\"" not in workflow
