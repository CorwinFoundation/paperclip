from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scanner-edge-image.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def _deploy_workflow() -> tuple[str, dict]:
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_image_build_triggers_are_master_push_and_manual_only() -> None:
    """Building an image is not deploying one.

    `ad55e165` added `push: branches: [master]` so that every merge publishes a
    verified image and the deploy click has nothing to wait for. This workflow
    only builds and pushes to GHCR; it reaches no host, and the three tests
    below pin that. What must stay true here is that it never runs on
    untrusted `pull_request` input and carries no deploy ceremony.

    The "nothing deploys by itself" guarantee is asserted against deploy.yml in
    the next test, which is where deployment actually lives. Asserting it here
    only ever tested this file's trigger list.
    """
    text, workflow = _workflow()
    triggers = workflow[True]

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["master"]}
    assert triggers["workflow_dispatch"] is None
    assert "pull_request" not in triggers
    assert "sequence" not in text.lower()


def test_deploy_workflow_never_fires_without_a_human() -> None:
    """The real live-deploy guard: nothing reaches srv1749248 unattended.

    deploy.yml is the only workflow that touches the production host. It must
    carry exactly one trigger, `workflow_dispatch`, so that every deployment is
    an explicit human action against a named SHA. Each automatic trigger is
    also named individually, so a future addition fails on the specific
    trigger rather than on an opaque set comparison.
    """
    _text, workflow = _deploy_workflow()
    triggers = workflow[True]

    assert set(triggers) == {"workflow_dispatch"}
    for automatic in (
        "push",
        "pull_request",
        "schedule",
        "workflow_run",
        "repository_dispatch",
    ):
        assert automatic not in triggers, automatic


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
