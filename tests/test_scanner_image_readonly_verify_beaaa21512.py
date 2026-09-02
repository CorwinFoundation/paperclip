"""BEAAA-21512 read-only Scanner image verification lane.

Covers the workflow's read-only posture and every fail-closed branch of
`scripts/verify_scanner_image.py`, driven through the real `main()` entrypoint
so that deleting a pin in `main()` fails the suite (BEAAA-21513 D2).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/scanner-image-readonly-verify.yml"
SCRIPT = REPO / "scripts/verify_scanner_image.py"

# Single source of truth for the lane's granted scopes, fixed by the CTO scope
# ruling and co-sign on BEAAA-23837 (Option A, closing BEAAA-21513 D1):
# exactly three repository-scoped read-only scopes, no job-level override.
EXPECTED_PERMISSIONS = {"contents": "read", "packages": "read", "actions": "read"}

spec = importlib.util.spec_from_file_location("verify_scanner_image", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _strings(value)]
    if isinstance(node, list):
        return [s for item in node for s in _strings(item)]
    return []


def test_workflow_permissions_are_exactly_the_granted_read_only_scopes() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == EXPECTED_PERMISSIONS
    assert set(workflow["permissions"].values()) == {"read"}
    for job in workflow["jobs"].values():
        assert "permissions" not in job, "job-level scope override would widen the lane"


def test_workflow_grants_no_secret_signing_or_production_access() -> None:
    workflow = _workflow()
    for value in _strings(workflow):
        assert "secrets." not in value
        assert "ssh" not in value.lower()
        assert "cosign" not in value.lower()
    assert "id-token" not in workflow["permissions"]
    # Only the ambient repository token may authenticate anything in this lane.
    tokens = re.findall(r"\$\{\{\s*([a-z.]+)\s*\}\}", WORKFLOW.read_text(encoding="utf-8"))
    assert set(t for t in tokens if "token" in t or "secret" in t) <= {"github.token"}


def test_every_third_party_action_is_pinned_by_commit_sha() -> None:
    """BEAAA-21513 D4: an immutability lane must not resolve its own tooling
    through mutable tags."""
    uses = [
        value
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
        if (value := step.get("uses"))
    ]
    assert uses, "workflow should consume at least one action"
    for value in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", value), value


# --------------------------------------------------------------------------
# fixtures for the verifier itself
# --------------------------------------------------------------------------

DIGEST = "sha256:" + "e" * 64
REVISION = "7850289333ffc1de6eebea9c2201e0f1059c4c12"


def _inspect(tmp_path: Path, revision: str, manifest_sha: str, *, count: int = 1) -> Path:
    entry = {
        "Config": {
            "Labels": {
                "org.opencontainers.image.revision": revision,
                "ai.backbond.release.manifest-sha256": manifest_sha,
            }
        }
    }
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps([entry] * count), encoding="utf-8")
    return path


def _artifact(
    tmp_path: Path,
    *,
    bundled_sha: str | None = None,
    checksum_line: str | None = None,
    members: tuple[str, ...] = ("scanner-edge-image.tar", "scanner-edge-image.tar.sha256"),
) -> tuple[Path, str]:
    tar = b"immutable scanner image archive"
    actual = hashlib.sha256(tar).hexdigest()
    line = checksum_line if checksum_line is not None else (
        f"{bundled_sha or actual}  dev-image/scanner-edge-image.tar\n"
    )
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in members:
            bundle.writestr(name, line if name.endswith(".sha256") else tar)
    return archive, module.sha256(archive)


def _manifest(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "frozen-release-manifest.json"
    path.write_text('{"scanner_version": "0.5.0"}\n', encoding="utf-8")
    return path, module.sha256(path)


def _run(monkeypatch, tmp_path: Path, **overrides) -> tuple[int, Path]:
    """Drive the real CLI entrypoint with the workflow's argument shape."""
    manifest, manifest_sha = _manifest(tmp_path)
    artifact, archive_sha = _artifact(
        tmp_path, **{k: v for k, v in overrides.items() if k in {"bundled_sha", "checksum_line", "members"}}
    )
    evidence = tmp_path / "verification.json"
    evidence.write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")

    args = {
        "--inspect-json": str(
            overrides.get("inspect_json")
            or _inspect(
                tmp_path,
                overrides.get("label_revision", REVISION),
                overrides.get("label_manifest_sha", manifest_sha),
                count=overrides.get("image_count", 1),
            )
        ),
        "--artifact-zip": str(overrides.get("artifact_zip") or artifact),
        "--source-manifest": str(manifest),
        "--evidence": str(evidence),
        "--repository": "ghcr.io/corwinfoundation/are-scanner",
        "--tag": f"sha-{REVISION}",
        "--resolved-digest": overrides.get("resolved_digest", DIGEST),
        "--expected-digest": overrides.get("expected_digest", DIGEST),
        "--expected-revision": overrides.get("expected_revision", REVISION),
        "--expected-manifest-sha256": overrides.get("expected_manifest_sha256", manifest_sha),
        "--expected-artifact-sha256": overrides.get("expected_artifact_sha256", archive_sha),
        "--source-run": "30672001992",
        "--source-artifact": "8809120470",
        "--verification-run-url": "https://github.com/CorwinFoundation/are-scanner/actions/runs/1",
    }
    argv = ["verify_scanner_image.py"]
    for key, value in args.items():
        argv += [key, value]
    monkeypatch.setattr(sys, "argv", argv)
    return module.main(), evidence


def test_main_passes_and_emits_evidence_that_states_its_binding_limits(monkeypatch, tmp_path) -> None:
    code, evidence = _run(monkeypatch, tmp_path)
    assert code == 0
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["schema_version"] == "backbond-scanner-readonly-image-verification@2"
    # BEAAA-21513 D3: one PASS must not imply an image-to-artifact binding.
    assert payload["binding_model"]["image_and_actions_artifact_cross_checked"] is False
    assert payload["image"]["resolved_digest"] == DIGEST
    assert payload["actions_artifact"]["source_run"] == "30672001992"


@pytest.mark.parametrize(
    "overrides, reason",
    [
        # M1: the resolved-digest pin in main().
        ({"resolved_digest": "sha256:" + "0" * 64}, "unexpected digest"),
        # M2: the frozen release-manifest pin in main().
        ({"expected_manifest_sha256": "0" * 64, "label_manifest_sha": "0" * 64}, "release-manifest SHA-256 mismatch"),
        # M5: the single-image shape guard.
        ({"image_count": 2}, "exactly one image"),
        ({"expected_revision": "f" * 40}, "OCI label mismatch"),
        ({"label_revision": "f" * 40}, "OCI label mismatch"),
        ({"expected_artifact_sha256": "0" * 64}, "archive SHA-256 mismatch"),
        ({"bundled_sha": "0" * 64}, "bundled image checksum mismatch"),
        ({"checksum_line": "not-a-checksum  scanner-edge-image.tar\n"}, "invalid format"),
        ({"members": ("scanner-edge-image.tar.sha256",)}, "one image tar and one bundled checksum"),
    ],
)
def test_every_mismatch_path_fails_closed_through_main(
    monkeypatch, tmp_path, capsys, overrides, reason
) -> None:
    code, evidence = _run(monkeypatch, tmp_path, **overrides)
    assert code == 1
    assert reason in capsys.readouterr().err
    # Fail-closed: the pre-seeded FAIL evidence is never overwritten.
    assert json.loads(evidence.read_text(encoding="utf-8")) == {"status": "FAIL"}


def test_unreadable_inspect_payload_fails_closed(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "absent.json"
    assert _run(monkeypatch, tmp_path, inspect_json=missing)[0] == 1


def test_corrupt_artifact_archive_fails_closed(monkeypatch, tmp_path) -> None:
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not a zip file")
    code, evidence = _run(
        monkeypatch, tmp_path, artifact_zip=corrupt, expected_artifact_sha256=module.sha256(corrupt)
    )
    assert code == 1
    assert json.loads(evidence.read_text(encoding="utf-8")) == {"status": "FAIL"}


def test_labels_and_artifact_accept_exact_values(tmp_path) -> None:
    revision, manifest = "a" * 40, "b" * 64
    assert module.verify_labels(_inspect(tmp_path, revision, manifest), revision, manifest)
    artifact, archive_sha = _artifact(tmp_path)
    assert module.verify_artifact(artifact, archive_sha)["archive_sha256"] == archive_sha


def test_inspect_payload_must_be_a_single_image_list(tmp_path) -> None:
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps({"Config": {}}), encoding="utf-8")
    with pytest.raises(module.VerificationError, match="exactly one image"):
        module.verify_labels(path, "a" * 40, "b" * 64)


def test_missing_labels_block_fails(tmp_path) -> None:
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps([{"Config": {"Labels": None}}]), encoding="utf-8")
    with pytest.raises(module.VerificationError, match="OCI label mismatch"):
        module.verify_labels(path, "a" * 40, "b" * 64)
