from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scanner-edge-image.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_workflow_is_master_only_and_has_no_release_sequence() -> None:
    text, workflow = _workflow()
    triggers = workflow[True]

    assert triggers["push"]["branches"] == ["master"]
    assert triggers["workflow_dispatch"] is None
    assert "pull_request" not in triggers
    assert "sequence" not in text.lower()


def test_workflow_builds_exact_sha_image_and_uploads_dev_bundle() -> None:
    text, workflow = _workflow()
    job = workflow["jobs"]["image-build"]
    steps = job["steps"]

    assert job["env"]["LOCAL_IMAGE_TAG"] == "backbond-scanner-live:${{ github.sha }}"
    assert any(step.get("name") == "Verify image identity" for step in steps)
    assert any(step.get("name") == "Verify runtime import closure" for step in steps)
    assert any(step.get("name") == "Create dev deploy bundle" for step in steps)
    assert any(step.get("name") == "Upload dev deploy bundle" for step in steps)
    assert "docker save" in text
    assert "scanner-edge-image.tar.sha256" in text
    assert "scripts/deploy/scanner-deploy.sh" in text


def test_suspended_production_ceremony_is_absent() -> None:
    text, workflow = _workflow()

    forbidden = (
        "paperclip",
        "release-candidate",
        "cosign",
        "syft",
        "sbom",
        "id-token",
        "packages: write",
        "docker push",
        "ghcr.io",
        "ssh_private_key",
        "known_hosts",
        "deploy exact",
        "scanner-ship",
    )
    lowered = text.lower()
    for token in forbidden:
        assert token not in lowered

    assert workflow["permissions"] == {"contents": "read"}


def test_workflow_does_not_contact_or_mutate_the_live_host() -> None:
    text, _ = _workflow()
    lowered = text.lower()

    assert "ssh " not in lowered
    assert "scp " not in lowered
    assert "srv1749248" not in lowered
    assert "scanner-deploy " not in lowered
