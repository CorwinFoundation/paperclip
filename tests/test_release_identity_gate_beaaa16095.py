from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "deploy" / "release_identity_gate.py"
SOURCE_SHA = "37192e2b0e9f50416170da77dcbc189c00761ecc"
WORKFLOW_SHA = "f1c4905323586e3ff4c77fd295a5f3d13846c2d5"
SOURCE_ISSUE_ID = "90ee9468-5480-4db8-a3c2-36654392ba7c"
IMAGE_REF = "ghcr.io/corwinfoundation/are-scanner@" + "sha256:" + ("a" * 64)

spec = importlib.util.spec_from_file_location("release_identity_gate", SCRIPT)
release_identity_gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(release_identity_gate)


def _candidate(**overrides):
    data = {
        "commitSha": SOURCE_SHA,
        "sourceIssueId": SOURCE_ISSUE_ID,
        "sourceCommitSha": SOURCE_SHA,
        "ociRevision": SOURCE_SHA,
        "workflowCommitSha": WORKFLOW_SHA,
        "workflowRef": (
            "CorwinFoundation/are-scanner/.github/workflows/"
            "scanner-edge-image.yml@refs/heads/master"
        ),
        "originMasterTip": SOURCE_SHA,
        "imageDigest": IMAGE_REF,
        "signatureSubject": release_identity_gate.EXPECTED_SUBJECT,
        "signatureOidcIssuer": release_identity_gate.EXPECTED_ISSUER,
        "signatureWorkflowRef": release_identity_gate.EXPECTED_WORKFLOW_REF,
    }
    data.update(overrides)
    return data


def _cosign(**optional_overrides):
    optional = {
        "Subject": release_identity_gate.EXPECTED_SUBJECT,
        "Issuer": release_identity_gate.EXPECTED_ISSUER,
        "githubWorkflowRef": release_identity_gate.EXPECTED_WORKFLOW_REF,
        "githubWorkflowSha": WORKFLOW_SHA,
    }
    optional.update(optional_overrides)
    return [
        {
            "critical": {
                "image": {
                    "docker-manifest-digest": IMAGE_REF.rsplit("@", 1)[1],
                },
                "identity": {
                    "docker-reference": "ghcr.io/corwinfoundation/are-scanner",
                },
            },
            "optional": optional,
        }
    ]


def test_release_identity_gate_accepts_master_workflow_with_bound_source_revision():
    report = release_identity_gate.validate_release_identity(
        _candidate(),
        _cosign(),
        expected_source_sha=SOURCE_SHA,
        expected_source_issue_id=SOURCE_ISSUE_ID,
        expected_image_digest=IMAGE_REF,
    )

    assert report["sourceCommitSha"] == SOURCE_SHA
    assert report["sourceIssueId"] == SOURCE_ISSUE_ID
    assert report["signatureSubject"] == release_identity_gate.EXPECTED_SUBJECT


def test_release_identity_gate_rejects_tag_signed_candidate():
    bad_subject = (
        "https://github.com/CorwinFoundation/are-scanner/"
        ".github/workflows/scanner-edge-image.yml@refs/tags/beaaa-15699-candidate-3"
    )

    with pytest.raises(release_identity_gate.ReleaseIdentityError) as exc:
        release_identity_gate.validate_release_identity(
            _candidate(signatureSubject=bad_subject),
            _cosign(Subject=bad_subject),
        )

    assert "refs/heads/master" in str(exc.value)
    assert "refs/tags/beaaa-15699-candidate-3" in str(exc.value)


def test_release_identity_gate_rejects_missing_explicit_oci_revision_binding():
    with pytest.raises(release_identity_gate.ReleaseIdentityError) as exc:
        release_identity_gate.validate_release_identity(
            _candidate(ociRevision=None),
            _cosign(),
        )

    assert "ociRevision" in str(exc.value)
    assert "sourceCommitSha" in str(exc.value)


def test_release_identity_gate_rejects_origin_master_drift():
    with pytest.raises(release_identity_gate.ReleaseIdentityError) as exc:
        release_identity_gate.validate_release_identity(
            _candidate(originMasterTip="0" * 40),
            _cosign(),
        )

    assert "originMasterTip" in str(exc.value)
    assert "sourceCommitSha" in str(exc.value)


def test_release_identity_gate_rejects_source_issue_drift():
    with pytest.raises(release_identity_gate.ReleaseIdentityError) as exc:
        release_identity_gate.validate_release_identity(
            _candidate(sourceIssueId="59747255-955b-41e2-addd-89a78b118380"),
            _cosign(),
            expected_source_issue_id=SOURCE_ISSUE_ID,
        )

    assert "sourceIssueId" in str(exc.value)
    assert SOURCE_ISSUE_ID in str(exc.value)


def test_release_identity_gate_accepts_cosign_v3_output_with_empty_optional_claims():
    cosign = _cosign()[0]
    cosign["optional"] = {}

    report = release_identity_gate.validate_release_identity(_candidate(), [cosign])

    assert report["signatureWorkflowRef"] == release_identity_gate.EXPECTED_WORKFLOW_REF


def test_release_identity_gate_cli_exits_nonzero_for_source_revision_drift(tmp_path: Path):
    candidate = tmp_path / "candidate.json"
    cosign = tmp_path / "cosign.json"
    candidate.write_text(json.dumps(_candidate(sourceCommitSha="0" * 40)), encoding="utf-8")
    cosign.write_text(json.dumps(_cosign()), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--release-candidate",
            str(candidate),
            "--cosign-verify",
            str(cosign),
            "--expected-source-sha",
            SOURCE_SHA,
        ],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 1
    assert "release identity gate FAILED" in proc.stderr
    assert "sourceCommitSha" in proc.stderr
