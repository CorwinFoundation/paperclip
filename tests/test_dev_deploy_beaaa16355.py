from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dev_deploy_is_one_push_path_with_live_sha_proof() -> None:
    script = (ROOT / "scripts/deploy/dev_deploy.sh").read_text()
    assert "docker build" in script
    assert "docker save" in script
    assert '"${SCP[@]}"' in script
    assert "${BOX_DEPLOY}' load" in script
    assert "${BOX_DEPLOY}' activate" in script
    assert "curl -fsS" in script
    assert "rollback" in script
    assert "approval" not in script.lower()


def test_image_exposes_baked_git_sha() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    app = (ROOT / "api/app.py").read_text()
    assert 'BACKBOND_BUILD_SHA="${GIT_SHA}"' in dockerfile
    assert '@app.get("/version", include_in_schema=False)' in app
    assert 'os.environ.get("BACKBOND_BUILD_SHA", "unknown")' in app
