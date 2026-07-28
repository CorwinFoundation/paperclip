from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scanner-edge-image.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_workflow_syntax_and_master_push_trigger() -> None:
    _, workflow = _workflow()
    triggers = workflow[True]
    assert triggers["push"]["branches"] == ["main", "master"]
    assert triggers["pull_request"]["branches"] == ["main", "master"]


def test_deploy_is_successful_master_push_only() -> None:
    _, workflow = _workflow()
    steps = workflow["jobs"]["image-build"]["steps"]
    deploy = next(step for step in steps if step.get("name") == "Deploy exact master image tar to live edge")
    condition = deploy["if"]
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/master'" in condition
    assert "success()" in condition
    assert "pull_request" not in condition


def test_exact_sha_and_tar_are_coupled_and_identity_mismatch_fails_closed() -> None:
    text, _ = _workflow()
    assert 'remote_tar="/var/tmp/backbond-scanner-${GITHUB_SHA}.tar"' in text
    assert 'local_tar_sha256="$(sha256sum "$local_tar"' in text
    assert 'test \\"\\$(sha256sum \\"\\$remote_tar\\"' in text
    assert "= '$local_tar_sha256'" in text
    assert "sudo /usr/local/sbin/scanner-deploy '$GITHUB_SHA' \\\"\\$remote_tar\\\"" in text
    assert 'test "${#GITHUB_SHA}" -eq 40' in text
    assert 'echo "::error::Invalid git SHA identity."' in text


def test_release_evidence_upload_is_retained_but_non_blocking() -> None:
    _, workflow = _workflow()
    steps = workflow["jobs"]["image-build"]["steps"]
    evidence = next(step for step in steps if step.get("name") == "Upload scoped release evidence to Paperclip")
    assert evidence["continue-on-error"] is True
    assert "curl -fsS -X POST" in evidence["run"]


def test_deploy_does_not_print_or_pass_credentials_in_argv() -> None:
    text, workflow = _workflow()
    steps = workflow["jobs"]["image-build"]["steps"]
    deploy = next(step for step in steps if step.get("name") == "Deploy exact master image tar to live edge")
    script = deploy["run"]
    assert "set -x" not in script
    assert "${SSH_PRIVATE_KEY}" not in script
    assert "${SSH_KNOWN_HOSTS}" not in script
    assert "StrictHostKeyChecking=yes" in script
    assert "UserKnownHostsFile=" in script
    assert "SCANNER_EDGE_DEPLOY_SSH_PRIVATE_KEY" not in script
    assert "SCANNER_EDGE_DEPLOY_SSH_KNOWN_HOSTS" not in script
    assert "${{ secrets." not in script
    assert "password" not in script.lower()
