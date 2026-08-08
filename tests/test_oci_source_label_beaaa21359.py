from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OCI_SOURCE = "https://github.com/CorwinFoundation/are-scanner"


def test_immutable_image_gate_binds_source_and_revision_labels() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/scanner-edge-image.yml").read_text(
        encoding="utf-8"
    )

    assert (
        f'org.opencontainers.image.source="{EXPECTED_OCI_SOURCE}"' in dockerfile
    )
    assert 'org.opencontainers.image.revision="${GIT_SHA}"' in dockerfile
    assert f"EXPECTED_OCI_SOURCE: {EXPECTED_OCI_SOURCE}" in workflow
    assert (
        "docker image inspect \"$IMAGE_REF\" --format "
        "'{{ index .Config.Labels \"org.opencontainers.image.source\" }}'"
    ) in workflow
    assert ' = "$EXPECTED_OCI_SOURCE"' in workflow
    assert (
        "docker image inspect \"$IMAGE_REF\" --format "
        "'{{ index .Config.Labels \"org.opencontainers.image.revision\" }}'"
    ) in workflow
    assert ' = "$GITHUB_SHA"' in workflow
