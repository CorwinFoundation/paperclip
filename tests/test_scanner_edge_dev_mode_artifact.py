from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scanner-edge-image.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_workflow_is_manual_only_and_has_no_live_deploy_trigger() -> None:
    text, workflow = _workflow()
    triggers = workflow[True]

    assert "push" not in triggers
    assert triggers["workflow_dispatch"] is None
    assert "pull_request" not in triggers
    assert "sequence" not in text.lower()


def test_workflow_builds_exact_sha_image_and_uploads_immutable_archive() -> None:
    text, workflow = _workflow()
    job = workflow["jobs"]["image-build"]
    steps = job["steps"]

    assert job["env"]["IMAGE_REF"] == "ghcr.io/corwinfoundation/are-scanner:sha-${{ github.sha }}"
    assert job["env"]["RELEASE_TAG"] == "sha-${{ github.sha }}"
    assert any(step.get("name") == "Validate candidate SHA and immutable target" for step in steps)
    assert any(step.get("name") == "Verify OCI and embedded release identity before push" for step in steps)
    assert any(step.get("name") == "Verify runtime import closure" for step in steps)
    assert any(step.get("name") == "Push immutable image and record repository digest" for step in steps)
    assert any(step.get("name") == "Export the same immutable image archive" for step in steps)
    assert any(step.get("name") == "Upload immutable image archive" for step in steps)
    assert "docker push" in text
    assert "docker save" in text
    assert "scanner-edge-image.tar.sha256" in text
    assert "scanner-edge-image.digest" in text


def test_registry_publish_is_unsigned_and_has_no_deploy_ceremony() -> None:
    text, workflow = _workflow()

    forbidden = (
        "paperclip",
        "release-candidate",
        "cosign",
        "syft",
        "sbom",
        "id-token",
        "ssh_private_key",
        "known_hosts",
        "deploy exact",
        "scanner-ship",
    )
    lowered = text.lower()
    for token in forbidden:
        assert token not in lowered

    assert workflow["permissions"] == {"contents": "read", "packages": "write"}


def test_workflow_does_not_contact_or_mutate_the_live_host() -> None:
    text, _ = _workflow()
    lowered = text.lower()

    assert "ssh " not in lowered
    assert "scp " not in lowered
    assert "srv1749248" not in lowered
    assert "scanner-deploy " not in lowered
