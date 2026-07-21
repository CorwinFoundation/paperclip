from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dev_deploy_stages_signed_image_and_only_emits_operator_push() -> None:
    script = (ROOT / "scripts/deploy/dev_deploy.sh").read_text()
    assert "docker build" in script
    assert "docker push" in script
    assert "cosign sign --yes" in script
    assert "docker save" in script
    assert "git rev-parse origin/master" in script
    assert 'COMMAND="scp ' in script
    assert "box credential required" in script
    # SCP/SSH occur only inside the emitted command string, never as commands on
    # the agent side. This is the Founder-selected path-(b) trust boundary.
    executable_lines = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(line.startswith("scp ") for line in executable_lines)
    assert not any(line.startswith("ssh ") for line in executable_lines)
    assert "BACKBOND_DEPLOY_IDENTITY" not in script


def test_emitted_command_preserves_live_sha_proof_and_rollback() -> None:
    script = (ROOT / "scripts/deploy/dev_deploy.sh").read_text()
    assert "curl -fsS" in script
    assert "grep -F ${Q_SHA}" in script
    assert "rollback" in script
    assert "previous=" in script


def test_image_exposes_baked_git_sha() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    app = (ROOT / "api/app.py").read_text()
    assert 'BACKBOND_BUILD_SHA="${GIT_SHA}"' in dockerfile
    assert '@app.get("/version", include_in_schema=False)' in app
    assert 'os.environ.get("BACKBOND_BUILD_SHA", "unknown")' in app
