from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scanner-edge-image.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_image_and_release_evidence_workflow_is_manual_only() -> None:
    """A merge or chained workflow must never publish or deploy a candidate."""
    _, workflow = _workflow()
    triggers = workflow[True]

    assert set(triggers) == {"workflow_dispatch"}
    dispatch = triggers["workflow_dispatch"]
    assert set(dispatch["inputs"]) == {
        "source_sha",
        "expected_image_digest",
        "source_issue_id",
        "sequence",
    }
    for automatic in (
        "push",
        "pull_request",
        "schedule",
        "workflow_run",
        "repository_dispatch",
    ):
        assert automatic not in triggers, automatic
    assert not DEPLOY_WORKFLOW.exists()


def test_workflow_binds_source_sha_digest_and_master_ancestry() -> None:
    text, workflow = _workflow()
    job = workflow["jobs"]["image-build"]
    steps = job["steps"]

    assert job["env"]["SOURCE_SHA"] == "${{ inputs.source_sha || github.sha }}"
    assert job["env"]["IMAGE_REPOSITORY"] == "ghcr.io/corwinfoundation/are-scanner"
    assert any(step.get("name") == "Validate source and release inputs" for step in steps)
    assert 'test "$GITHUB_REF" = refs/heads/master' in text
    assert 'git merge-base --is-ancestor "$SOURCE_SHA" refs/remotes/origin/master' in text
    assert 'test "$SOURCE_SHA" = "$(git rev-parse refs/remotes/origin/master)"' in text
    assert "expected_image_digest" in text
    assert 'docker pull "$signed_ref"' in text
    assert "org.opencontainers.image.revision" in text


def test_workflow_builds_and_verifies_the_runtime_image() -> None:
    text, workflow = _workflow()
    steps = workflow["jobs"]["image-build"]["steps"]

    for name in (
        "Build or pull the exact candidate image",
        "Verify OCI and embedded release identity",
        "Verify runtime import closure",
        "Verify AIUC-1 direct-family production binding in image",
        "Export the exact immutable image archive",
    ):
        assert any(step.get("name") == name for step in steps)
    assert "docker build" in text
    assert "docker push" in text
    assert "docker save" in text
    assert "scanner-edge-image.tar.sha256" in text
    assert "scanner-edge-image.digest" in text


def test_release_evidence_has_sbom_bundle_and_in_toto_provenance() -> None:
    text, workflow = _workflow()
    steps = workflow["jobs"]["image-build"]["steps"]

    for name in (
        "Generate CycloneDX SBOM",
        "Sign digest and attest SBOM and provenance",
        "Write immutable release candidate manifest",
        "Validate release identity gate",
        "Upload governed release evidence",
    ):
        assert any(step.get("name") == name for step in steps)
    assert "--new-bundle-format=true" in text
    assert "scanner-edge-image.sigstore.sha256" in text
    assert "--type cyclonedx" in text
    assert "--type slsaprovenance" in text
    assert "verify-attestation" in text
    assert "scanner-edge-slsa-provenance.json" in text
    assert "release_identity_gate.py" in text
    assert workflow["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }


def test_workflow_cannot_contact_or_mutate_production() -> None:
    text, _ = _workflow()
    lowered = text.lower()

    forbidden = (
        "ssh ",
        "scp ",
        "deploy_ssh",
        "known_hosts",
        "scanner-deploy ",
        "paperclip_release_candidate_key",
        "paperclip_api_key",
        "/release-candidates",
    )
    for token in forbidden:
        assert token not in lowered
