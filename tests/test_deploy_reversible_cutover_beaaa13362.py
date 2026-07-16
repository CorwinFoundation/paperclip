import hashlib
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOX_DEPLOY = REPO / "scripts" / "deploy" / "box_deploy.sh"
OPERATOR_PUSH = REPO / "scripts" / "deploy" / "operator_push.sh"
CD_WORKFLOW = REPO / ".github" / "workflows" / "cd.yml"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip())
    path.chmod(0o755)


@pytest.mark.skipif(shutil.which("bash") is None, reason="requires Bash")
def test_box_load_verifies_archive_ref_and_revision(tmp_path: Path) -> None:
    tarball = tmp_path / "image.tar"
    tarball.write_bytes(b"exact-ci-image-archive")
    tar_sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    ref = "ghcr.io/corwinfoundation/are-scanner:" + "a" * 40
    revision = "a" * 40

    engine = tmp_path / "fake-docker"
    _write_executable(
        engine,
        """
        #!/usr/bin/env sh
        if [ "$1" = "load" ]; then
          echo "Loaded image: fake" >&2
          exit 0
        fi
        if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
          case " $* " in
            *" --format "*) printf '%s\n' "$EXPECTED_REVISION" ;;
          esac
          exit 0
        fi
        exit 99
        """,
    )

    env = os.environ.copy()
    env["ARE_CONTAINER_ENGINE"] = str(engine)
    env["EXPECTED_REVISION"] = revision
    result = subprocess.run(
        ["bash", str(BOX_DEPLOY), "load", str(tarball), tar_sha, ref, revision],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ref
    assert "loaded payload verified" in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="requires Bash")
def test_box_load_rejects_archive_mismatch_before_engine_load(tmp_path: Path) -> None:
    tarball = tmp_path / "image.tar"
    tarball.write_bytes(b"tampered")
    marker = tmp_path / "engine-called"
    engine = tmp_path / "fake-docker"
    _write_executable(
        engine,
        f"""
        #!/usr/bin/env sh
        touch "{marker}"
        exit 0
        """,
    )

    env = os.environ.copy()
    env["ARE_CONTAINER_ENGINE"] = str(engine)
    result = subprocess.run(
        [
            "bash",
            str(BOX_DEPLOY),
            "load",
            str(tarball),
            "0" * 64,
            "ghcr.io/corwinfoundation/are-scanner:" + "b" * 40,
            "b" * 40,
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 5
    assert "ARCHIVE HASH MISMATCH" in result.stderr
    assert not marker.exists()


def test_first_cutover_keeps_legacy_service_as_rollback() -> None:
    box = BOX_DEPLOY.read_text()
    operator = OPERATOR_PUSH.read_text()

    assert 'APP_PORT="${ARE_APP_PORT:-8077}"' in box
    assert '-p "${host}:${port}:${APP_PORT}"' in box
    assert "cutover-legacy)" in box
    assert "rollback-legacy-ingress)" in box
    assert 'systemctl is-active --quiet "${LEGACY_SERVICE}"' in box
    assert "systemctl stop" not in box
    assert "systemctl disable" not in box

    assert "--first-cutover" in operator
    assert 'BASE_URL="${CANDIDATE_BASE_URL:' in operator
    assert '"${BOX_DEPLOY}" cutover-legacy' in operator
    assert '"${BOX_DEPLOY}" rollback-legacy-ingress' in operator
    assert '"${BOX_DEPLOY}" deactivate' in operator
    assert 'MANIFEST_PUBKEY="${ARE_MANIFEST_PUBKEY:-/etc/are-scanner/' in operator
    assert "--manifest-pubkey" not in operator
    assert "keeping candidate container running" in operator
    assert "candidate left running because Nginx may still route to it" in operator
    assert "legacy service remains active for immediate rollback" in operator


def test_cd_binds_manual_approval_to_exact_offline_payload() -> None:
    workflow = CD_WORKFLOW.read_text()

    assert "issues: write" in workflow
    assert (
        "trstringer/manual-approval@fa642940caf8412403569b28b2db4a1df08a83a3"
        in workflow
    )
    assert "tar_sha256: ${{ steps.payload.outputs.tar_sha256 }}" in workflow
    assert '"tar_sha256": os.environ["TAR_SHA256"]' in workflow
    assert "docker save" in workflow
    assert "image-payload-${SHA}" in workflow
    assert "GIT_SHA=${{ steps.meta.outputs.sha }}" in workflow
    assert "github.event.inputs.image_sha" not in workflow
    assert "if: success()" in workflow
    assert "if: always()" not in workflow
    assert '"approver": os.environ["APPROVER"]' in workflow
    assert "APPROVER: CorwinFoundation" in workflow
    assert "GH_ACTOR" not in workflow
