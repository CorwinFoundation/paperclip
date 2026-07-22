"""Static fail-closed contract for the BEAAA-16889 QA evidence lane."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/scanner-edge-qa-evidence.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
CANDIDATE = "d2d53b9c65668ff44a2e3fc9c3dd3c23a978a76c"
ISSUE_ID = "91d80911-e5e2-44b6-9f77-26685432a34b"
QA_REF = "refs/heads/beaaa-16889-qa-evidence"


def test_lane_is_dispatch_only_and_exact_candidate_bound() -> None:
    assert "workflow_dispatch:" in TEXT
    assert "pull_request:" not in TEXT
    assert "\n  push:" not in TEXT
    assert f"CANDIDATE_SHA: {CANDIDATE}" in TEXT
    assert f"ref: {CANDIDATE}" in TEXT
    assert 'test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"' in TEXT
    assert f"QA_REF: {QA_REF}" in TEXT
    assert 'test "$GITHUB_REF" = "$QA_REF"' in TEXT


def test_lane_is_qa_only_and_cannot_redirect_its_subject() -> None:
    assert "TARGET_ENVIRONMENT: qa-evidence" in TEXT
    assert "TARGET_HOST: qa-controlled-real-agent" in TEXT
    assert f"SOURCE_ISSUE_ID: {ISSUE_ID}" in TEXT
    assert "github.event.inputs.issue" not in TEXT
    assert "github.event.inputs.source" not in TEXT
    assert "github.event.inputs.environment" not in TEXT
    assert "github.event.inputs.target" not in TEXT
    assert "github.event.inputs.ref" not in TEXT


def test_no_shared_or_live_lane_bindings_leak_into_qa_workflow() -> None:
    forbidden = (
        "TARGET_ENVIRONMENT: production",
        "srv1749248",
        "refs/heads/master",
        "refs/heads/main",
        "origin/master",
        "origin/main",
        "scanner-edge-image.yml",
        "backbond_deploy_agent",
        "scanner-deploy.sh",
        "workflow_run",
    )
    for marker in forbidden:
        assert marker not in TEXT


def test_signed_evidence_and_bounded_exchange_are_preserved() -> None:
    required = (
        "scripts/verify_runtime_import_closure.py",
        "scanner-edge-sbom.cdx.json",
        'cosign sign --yes --new-bundle-format=true --bundle "$bundle_path"',
        '--certificate-identity "$EXPECTED_SUBJECT"',
        "scanner-edge-image.sigstore.json",
        "scanner-edge-provenance-ref.txt",
        "audience=paperclip-release-evidence",
        "/api/release-evidence/v1/exchange",
        "/api/release-evidence/v1/sessions/${session_id}/attachment",
        "X-Paperclip-Release-Evidence-Capability",
        'test "$bundle_bytes" -le 268435456',
    )
    for marker in required:
        assert marker in TEXT


def test_grant_is_required_but_never_defaulted_or_logged() -> None:
    assert "vars.SCANNER_EDGE_RELEASE_EVIDENCE_GRANT_ID" in TEXT
    assert 'test -n "${RELEASE_EVIDENCE_GRANT_ID:-}"' in TEXT
    assert "placeholder" not in TEXT.lower()
    assert "echo $RELEASE_EVIDENCE_GRANT_ID" not in TEXT
    assert "PAPERCLIP_API_KEY" not in TEXT
