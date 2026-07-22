from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/scanner-edge-image.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> dict[str, dict]:
    workflow = _workflow()
    steps = workflow["jobs"]["image-build"]["steps"]
    return {step["name"]: step for step in steps if isinstance(step, dict) and "name" in step}


def test_manual_release_sequence_accepts_unbounded_positive_integers() -> None:
    dispatch = _workflow()[True]["workflow_dispatch"]
    sequence = dispatch["inputs"]["sequence"]
    validate = _steps()["Validate release candidate sequence"]

    assert sequence["type"] == "string"
    assert "options" not in sequence
    assert validate["if"] == "github.event_name == 'workflow_dispatch'"
    assert "^[1-9][0-9]*$" in validate["run"]


def test_release_candidate_contains_verified_sigstore_bundle_hash() -> None:
    steps = _steps()
    install = steps["Install cosign"]
    sign = steps["Sign image digest and attest provenance"]
    payload = steps["Write release candidate payload"]

    assert install["uses"] == "sigstore/cosign-installer@v4.1.2"
    assert install["with"]["cosign-release"] == "v3.1.1"
    assert sign["id"] == "signed_candidate"
    assert "--new-bundle-format=true --bundle" in sign["run"]
    assert sign["run"].count('--bundle "$bundle_path"') == 1
    assert "cosign verify" in sign["run"]
    assert "--certificate-identity" in sign["run"]
    assert "signature_bundle_hash=" in sign["run"]
    assert payload["env"]["SIGNATURE_BUNDLE_HASH"] == "${{ steps.signed_candidate.outputs.signature_bundle_hash }}"
    assert "--arg signatureBundleSha256" in payload["run"]
    assert "signatureBundleSha256: $signatureBundleSha256" in payload["run"]


def test_scoped_release_evidence_upload_remains_fail_closed() -> None:
    steps = _steps()
    payload = steps["Write release candidate payload"]
    upload = steps["Upload release candidate artifacts"]
    post = steps["Upload scoped release evidence to Paperclip"]

    assert payload["if"] == "github.event_name != 'pull_request'"
    assert upload["if"] == "github.event_name != 'pull_request'"
    assert post["if"] == "github.event_name != 'pull_request'"
    assert "audience=paperclip-release-evidence" in post["run"]
    assert "/api/release-evidence/v1/exchange" in post["run"]
    assert "X-Paperclip-Release-Evidence-Capability" in post["run"]
