"""BEAAA-16321 - scanner-edge scoped release evidence workflow contract.

The scanner-edge workflow is the GitHub-side client for the Paperclip
release-evidence ingress accepted under BEAAA-16312/BEAAA-16329. These tests are
static by design: they prove the committed workflow no longer carries a broad
Paperclip agent key and that mutable workflow inputs cannot redirect release
evidence to a different issue/release tuple.

No network, Docker, scoring, rubric, corpus, provisioning, or promotion surface.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "scanner-edge-image.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")


def test_workflow_uses_github_oidc_scoped_release_evidence_ingress() -> None:
    assert "id-token: write" in WORKFLOW_TEXT
    assert "audience=paperclip-release-evidence" in WORKFLOW_TEXT
    assert "/api/release-evidence/v1/exchange" in WORKFLOW_TEXT
    assert "/api/release-evidence/v1/sessions/${session_id}/attachment" in WORKFLOW_TEXT
    assert "X-Paperclip-Release-Evidence-Capability" in WORKFLOW_TEXT
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in WORKFLOW_TEXT
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in WORKFLOW_TEXT


def test_workflow_removed_broad_paperclip_release_candidate_key() -> None:
    forbidden = (
        "PAPERCLIP_RELEASE_CANDIDATE_KEY",
        "PAPERCLIP_API_KEY",
        "/release-candidates",
        "/api/companies/${PAPERCLIP_COMPANY_ID}/release-candidates",
    )
    for marker in forbidden:
        assert marker not in WORKFLOW_TEXT


def test_exchange_payload_binds_authorized_issue_release_and_bundle_fields() -> None:
    required_fields = (
        "grantId: $grantId",
        "issueId: $issueId",
        "sourceSha: $sourceSha",
        "workflowSha: $workflowSha",
        "sequence: $sequence",
        "environment: $environment",
        "imageDigest: $imageDigest",
        "bundleSha256: $bundleSha256",
        "bundleBytes: $bundleBytes",
        "clientNonce: $clientNonce",
    )
    for field in required_fields:
        assert field in WORKFLOW_TEXT

    pinned_or_runtime_bound_values = (
        "RELEASE_EVIDENCE_GRANT_ID: ${{ vars.SCANNER_EDGE_RELEASE_EVIDENCE_GRANT_ID }}",
        "SOURCE_ISSUE_ID: 59747255-955b-41e2-addd-89a78b118380",
        "TARGET_ENVIRONMENT: production",
        'sourceSha "$GITHUB_SHA"',
        'workflowSha "$WORKFLOW_SHA"',
        'imageDigest "${IMAGE_REF%:*}@$DIGEST"',
        'bundleSha256 "$bundle_sha256"',
        'bundleBytes "$bundle_bytes"',
    )
    for value in pinned_or_runtime_bound_values:
        assert value in WORKFLOW_TEXT


def test_negative_dispatch_fixture_cannot_redirect_issue_or_release_tuple() -> None:
    """A hostile dispatch payload has nowhere to bind alternate release fields."""
    dispatch_inputs = re.findall(r"^\s{6}([a-zA-Z0-9_-]+):\n", WORKFLOW_TEXT, re.MULTILINE)
    assert dispatch_inputs == ["sequence"]

    redirectable_markers = (
        "github.event.inputs.issue",
        "github.event.inputs.issue_id",
        "github.event.inputs.source_sha",
        "github.event.inputs.workflow_sha",
        "github.event.inputs.environment",
        "github.event.inputs.image_digest",
        "github.event.inputs.grant",
        "github.event.inputs.grant_id",
    )
    for marker in redirectable_markers:
        assert marker not in WORKFLOW_TEXT

    # Sequence is intentionally the only dispatch input and accepts any
    # positive integer only after an explicit fail-closed shell validation.
    assert "type: string" in WORKFLOW_TEXT
    assert "^[1-9][0-9]*$" in WORKFLOW_TEXT


def test_workflow_fails_closed_on_missing_exchange_or_upload_capability() -> None:
    guards = (
        'test -n "${PAPERCLIP_API_URL:-}"',
        'test -n "${RELEASE_EVIDENCE_GRANT_ID:-}"',
        'test -n "${SOURCE_ISSUE_ID:-}"',
        'test -n "${DIGEST:-}"',
        'test -n "${WORKFLOW_SHA:-}"',
        'test -n "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}"',
        'test -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}"',
        'test -n "$oidc_token"',
        'test -n "$session_id"',
        'test -n "$capability"',
        'test "$upload_url" = "/api/release-evidence/v1/sessions/${session_id}/attachment"',
    )
    for guard in guards:
        assert guard in WORKFLOW_TEXT
