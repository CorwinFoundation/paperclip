from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scanner-edge-image.yml"


def test_deploy_uses_actions_secrets_in_runner_temp() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "secrets.SCANNER_EDGE_DEPLOY_SSH_PRIVATE_KEY" in text
    assert "secrets.SCANNER_EDGE_DEPLOY_SSH_KNOWN_HOSTS" in text
    assert 'ssh_dir="$RUNNER_TEMP/scanner-deploy-ssh"' in text
    assert "printf '%s\\n' \"$DEPLOY_SSH_PRIVATE_KEY\" > \"$deploy_key\"" in text
    assert "printf '%s' \"$DEPLOY_SSH_PRIVATE_KEY\" > \"$deploy_key\"" not in text
    assert 'chmod 0600 "$deploy_key" "$known_hosts"' in text
    assert '-i "$DEPLOY_KEY"' in text
    assert 'UserKnownHostsFile="$DEPLOY_KNOWN_HOSTS"' in text
    assert "/home/ghrunner/.ssh/scanner_deploy_key" not in text
    assert "/home/ghrunner/.ssh/known_hosts_scanner" not in text
