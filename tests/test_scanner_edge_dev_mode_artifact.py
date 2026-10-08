from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scanner-edge-image.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_image_and_release_evidence_workflow_publishes_on_master_only() -> None:
    """Master commits publish a signed image; nothing else may trigger, and no lane deploys.

    BEAAA-28837: the automatic build-on-master lane is deliberate. What stays
    forbidden is any trigger that is not a master commit or an explicit manual
    dispatch, and any deploy step in either lane.
    """
    _, workflow = _workflow()
    triggers = workflow[True]

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["master"]}
    dispatch = triggers["workflow_dispatch"]
    assert set(dispatch["inputs"]) == {
        "source_sha",
        "expected_image_digest",
        "source_issue_id",
        "sequence",
    }
    for automatic in (
        "pull_request",
        "pull_request_target",
        "schedule",
        "workflow_run",
        "workflow_call",
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
        "Sign exact digest and write Sigstore bundle",
        "Verify exact digest signature",
        "Write SLSA provenance predicate",
        "Attest CycloneDX SBOM",
        "Attest SLSA provenance",
        "Verify SLSA provenance attestation",
        "Verify CycloneDX SBOM attestation",
        "Write immutable release candidate manifest",
        "Validate release identity gate",
        "Upload governed release evidence",
    ):
        assert any(step.get("name") == name for step in steps)
    assert "--new-bundle-format=true" in text
    assert "scanner-edge-image.sigstore.sha256" in text
    assert "--type cyclonedx" in text
    assert "--type slsaprovenance" in text
    assert "verify-blob-attestation" in text
    assert "--check-claims=true" in text
    assert "--digestAlg sha256" in text
    assert text.count("--timeout 90s") == 6
    assert "scanner-edge-slsa-provenance.json" in text
    assert "release_identity_gate.py" in text
    assert workflow["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }


def test_only_the_master_push_deploy_job_contacts_the_scanner_server() -> None:
    text, workflow = _workflow()
    build_text, _, deploy_text = text.partition("\n  deploy:\n")
    assert deploy_text, "deploy job missing"
    for token in ("paperclip_release_candidate_key", "/release-candidates"):
        assert token not in text.lower(), token
    for token in ("ssh ", "scp ", "deploy_key", "known_hosts", "scanner-deploy", "paperclip_api_key"):
        assert token not in build_text.lower(), token
        assert token in deploy_text.lower(), token
    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "image-build"
    assert deploy["if"] == "${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}"
    assert 'ssh_dir="$RUNNER_TEMP/scanner-deploy-ssh"' in deploy_text
    assert "secrets.SCANNER_EDGE_DEPLOY_SSH_PRIVATE_KEY" in deploy_text
    assert "secrets.SCANNER_EDGE_DEPLOY_SSH_KNOWN_HOSTS" in deploy_text
    assert 'chmod 0600 "$deploy_key" "$known_hosts"' in deploy_text
    assert "secrets.SCANNER_DEPLOY_SSH_KEY" not in deploy_text
    assert "secrets.SCANNER_DEPLOY_KNOWN_HOSTS" not in deploy_text
    assert "-o StrictHostKeyChecking=yes" in deploy_text


def test_governed_release_evidence_upload_excludes_the_image_tars() -> None:
    """BEAAA-35580: governed evidence is kilobytes of receipts, not a 75 MiB image tar.

    The image tar is a transport; the registry is the store. The uploaded
    receipts keep ``scanner-edge-image.digest`` and
    ``scanner-edge-image.tar.sha256``, so the evidence still resolves against
    ``ghcr.io/corwinfoundation/are-scanner`` by digest, and the full payload
    (image tar included) stays in GHCR under the reference recorded in
    ``scanner-release-evidence-publication.json``.
    """
    _, workflow = _workflow()
    steps = workflow["jobs"]["image-build"]["steps"]

    upload = next(
        step for step in steps if step.get("name") == "Upload governed release evidence"
    )
    paths = [line for line in upload["with"]["path"].splitlines() if line.strip()]
    assert paths == [
        "release-candidate/",
        "durable-evidence/",
        "!release-candidate/scanner-edge-image.tar",
        "!durable-evidence/scanner-release-evidence.tar",
    ]
    assert upload["with"]["retention-days"] == 90

    deploy_download = next(
        step
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Download image built by this run"
    )
    assert deploy_download["with"]["name"] == "scanner-immutable-image-${{ github.sha }}"
