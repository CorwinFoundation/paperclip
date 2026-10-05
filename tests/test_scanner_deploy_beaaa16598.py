"""Focused acceptance for the BEAAA-20076 atomic activation helper."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/deploy/scanner-deploy.sh"
DEV_DEPLOY = ROOT / "scripts/deploy/dev_deploy.sh"
UNIT = ROOT / "infra/live-edge/backbond-scanner-live.service"
SHA = "a" * 40
DIGEST = "example.invalid/backbond/scanner@sha256:" + "b" * 64
MANIFEST = "c" * 64
CANDIDATE = "BEAAA-19984"


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def harness(tmp_path: Path, *, duplicate=False, curl_fail=False, rollback_fail=False):
    state = tmp_path / "state"; state.mkdir(mode=0o755)
    env_file = tmp_path / "scanner.env"
    env_file.write_text("KEEP=1\nSCANNER_EDGE_IMAGE=old:image\n" + ("SCANNER_EDGE_IMAGE=duplicate\n" if duplicate else ""))
    env_file.chmod(0o600)
    unit = tmp_path / "scanner.service"
    unit.write_text("ExecStart=docker run --volume /var/lib/backbond-deploy-agent:/var/lib/backbond-deploy-agent:ro image\n")
    archive = tmp_path / f"backbond-scanner-{SHA}.tar"; archive.write_bytes(b"approved archive")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(); calls = tmp_path / "calls"
    manifest_file = tmp_path / "embedded-release.json"
    manifest_file.write_text(json.dumps({"scanner_version":"0.5.0","corpus_version":"0.2.0","build_sha":SHA,"release_tag":"sha-"+SHA},sort_keys=True,separators=(",", ":")))
    manifest_sha = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
    _write_exe(bin_dir / "docker", f'''#!/bin/sh
echo "docker $*" >>"{calls}"
case "$1 $2" in
 "image ls") case "$*" in *"label=org.opencontainers.image.revision=$STUB_SHA"*) echo sha256:loaded;; *) printf '%b\n' "${{STUB_IMAGE_ROWS:-loaded:tag\\tsha256:loaded}}";; esac;;
 "image inspect") case "$*" in *"{{.ID}}"*) case "$*" in *old:image*) echo sha256:current;; *rollback:image*) echo sha256:rollback;; *) echo sha256:loaded;; esac;; *) echo "$STUB_SHA";; esac;;
 "image rm")
   if [ -n "$STUB_IMAGE_RM_CONFLICT_REF" ] && [ "$3" = "$STUB_IMAGE_RM_CONFLICT_REF" ]; then
     echo "Error response from daemon: conflict: unable to remove repository reference \"$STUB_IMAGE_RM_CONFLICT_REF\" (must force) - container abc123 is using its referenced image deadbeef" >&2
     exit 1
   fi
   if [ -n "$STUB_IMAGE_RM_FAIL_REF" ] && [ "$3" = "$STUB_IMAGE_RM_FAIL_REF" ]; then
     echo "Error response from daemon: unexpected storage failure" >&2
     exit 1
   fi;;
 "inspect --format") case "$*" in *Labels*) echo "$STUB_SHA";; *) echo sha256:running;; esac;;
esac
case "$1" in
 create) [ -z "$STUB_CREATE_FAIL" ] || exit 71; : >"$STUB_CONTAINER_STATE"; echo manifest-inspection-container;;
 cp) [ -z "$STUB_CP_FAIL" ] || exit 72; [ -z "$STUB_PAUSE_CP" ] || sleep 30; cp "$STUB_MANIFEST_FILE" "$3";;
 rm) n=$(grep -c '^docker rm -f manifest-inspection-container' "{calls}"); [ -z "$STUB_RM_FAIL_PERSISTENT" ] || exit 74; [ -z "$STUB_RM_FAIL_ONCE" ] || [ "$n" -gt 1 ] || exit 74; rm -f "$STUB_CONTAINER_STATE";;
 tag) [ -z "$STUB_TAG_FAIL" ] || exit 76;;
esac
exit 0
''')
    _write_exe(bin_dir / "mktemp", f'''#!/bin/sh
[ -z "$STUB_MKTEMP_FAIL" ] || exit 73
exec {shutil.which("mktemp")} "$@"
''')
    _write_exe(bin_dir / "sha256sum", f'''#!/bin/sh
[ -z "$STUB_HASH_FAIL" ] || [ "$1" = "$STUB_ARCHIVE_PATH" ] || exit 75
exec {shutil.which("sha256sum")} "$@"
''')
    _write_exe(bin_dir / "systemctl", f'''#!/bin/sh
echo "systemctl $*" >>"{calls}"
case "$*" in restart* ) n=$(grep -c '^systemctl restart' "{calls}"); [ -n "$STUB_ROLLBACK_FAIL" ] && [ "$n" -gt 1 ] && exit 1;; esac
[ -n "$STUB_FIRST_RESTART_FAIL" ] && [ "$1" = restart ] && [ "$(grep -c '^systemctl restart' "{calls}")" = 1 ] && exit 1
[ -n "$STUB_PAUSE_FIRST_RESTART" ] && [ "$1" = restart ] && [ "$(grep -c '^systemctl restart' "{calls}")" = 1 ] && sleep 30
exit 0
''')
    _write_exe(bin_dir / "curl", '''#!/bin/sh
out=/dev/null
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; *) shift;; esac; done
if [ -n "$STUB_CURL_FAIL" ]; then printf 503; exit 0; fi
case "$out" in /dev/null) :;; *) printf '%s\n' "$STUB_IDENTITY" >"$out";; esac
printf 200
''')
    local_tag = f"backbond-scanner-live:{SHA}"
    identity = json.dumps({"commit_sha":SHA,"current_version":SHA,"running_container_revision":SHA,"internal_version_sha":SHA,"image_digest":DIGEST,"configured_image":local_tag,"schema_version":"backbond-scanner-release-identity@1","environment":"production","host":"srv1749248","candidate_id":CANDIDATE,"status":"activated","recorded_at":"ignored"},sort_keys=True,separators=(",", ":"))
    e = os.environ.copy()
    e.update({"PATH":f"{bin_dir}:{e['PATH']}","TMPDIR":str(tmp_path),"SCANNER_EDGE_ENV":str(env_file),"SCANNER_ARCHIVE_DIR":str(tmp_path),"SCANNER_STATE_DIR":str(state),"SCANNER_UNIT_FILE":str(unit),"SCANNER_DOCKER":str(bin_dir/"docker"),"SCANNER_SYSTEMCTL":str(bin_dir/"systemctl"),"SCANNER_CURL":str(bin_dir/"curl"),"SCANNER_READINESS_TIMEOUT":"1","SCANNER_READINESS_INTERVAL":"1","SCANNER_EXPECTED_OWNER_UID":str(os.getuid()),"SCANNER_EXPECTED_OWNER_GID":str(os.getgid()),"SCANNER_FROZEN_SHA":SHA,"SCANNER_FROZEN_REPO_DIGEST":DIGEST,"SCANNER_FROZEN_ARCHIVE_SHA":archive_sha,"SCANNER_FROZEN_MANIFEST_SHA":manifest_sha,"SCANNER_FROZEN_CANDIDATE":CANDIDATE,"STUB_SHA":SHA,"STUB_ARCHIVE_PATH":str(archive),"STUB_MANIFEST_FILE":str(manifest_file),"STUB_CONTAINER_STATE":str(tmp_path/"container.exists"),"STUB_IDENTITY":identity,"STUB_CURL_FAIL":"1" if curl_fail else "","STUB_ROLLBACK_FAIL":"1" if rollback_fail else ""})
    return e, env_file, state, archive, archive_sha, calls


def run(e, archive, archive_sha, *, digest=DIGEST):
    return subprocess.run(["bash",str(HELPER),SHA,str(archive),digest,archive_sha,CANDIDATE],env=e,text=True,capture_output=True)


def test_shell_syntax_and_exact_read_only_mount():
    assert subprocess.run(["bash","-n",str(HELPER)]).returncode == 0
    assert "--volume /var/lib/backbond-deploy-agent:/var/lib/backbond-deploy-agent:ro" in UNIT.read_text()


def test_development_helper_takes_identity_from_five_validated_arguments():
    text = HELPER.read_text()
    assert "[ \"$#\" = 5 ]" in text
    assert "SCANNER_FROZEN_" not in text
    assert "repository@sha256" in text


def test_wp_s_diagnostics_reports_only_scoped_values_and_direct_ip(tmp_path):
    docker = tmp_path / "docker"
    calls = tmp_path / "calls"
    _write_exe(
        docker,
        f'''#!/bin/sh
printf '%s\\n' "$*" >>"{calls}"
case "$*" in
  *"printenv ARE_WALKER_CUSTOMER_AGENT") printf 'partner-agent\\n';;
  *"printenv ARE_SCAN_TIER") printf 'production\\n';;
  *"printenv ARE_PINNED_CORPUS_SNAPSHOT_HASH") printf 'secret-corpus-hash\\n';;
  *"printenv ARE_SCAN_WEBHOOK") printf 'ops@example.test\\n';;
  *"printenv BEAAA_RESI_PROXY_URL") printf 'https://secret-proxy.example\\n';;
  *"sh -c unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy; exec curl -fsS --max-time 10 https://api.ipify.org") printf '203.0.113.10\\n';;
  *) exit 2;;
esac
''',
    )
    result = subprocess.run(
        ["bash", str(HELPER), "diagnostics", "wp-s"],
        env={**os.environ, "SCANNER_DOCKER": str(docker)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "ARE_WALKER_CUSTOMER_AGENT=partner-agent",
        "ARE_SCAN_TIER=production",
        "ARE_PINNED_CORPUS_SNAPSHOT_HASH=set",
        "ARE_SCAN_WEBHOOK=ops@example.test",
        "BEAAA_RESI_PROXY_URL=set",
        "OUTBOUND_IP=203.0.113.10",
    ]
    assert "secret-corpus-hash" not in result.stdout
    assert "secret-proxy.example" not in result.stdout
    assert "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY" in calls.read_text()


def test_c4a58caa_accepts_only_the_canonical_ci_built_repository_digest():
    text = HELPER.read_text()
    assert "CANONICAL_C4_SHA=c4a58caac801d24e7efa2436dbc5c4086b05d5fe" in text
    assert (
        "CANONICAL_C4_REPO_DIGEST=ghcr.io/corwinfoundation/are-scanner@sha256:"
        "b9bd0e70943424331c67c07ad17682412871d4cddde69d97193f847cd0fccfdd"
        in text
    )
    assert 'if [ "$SHA" = "$CANONICAL_C4_SHA" ]' in text
    assert 'repository digest is not the canonical c4a58caa image' in text


def test_c4a58caa_wrong_repository_digest_fails_before_archive_access(tmp_path):
    sha = "c4a58caac801d24e7efa2436dbc5c4086b05d5fe"
    archive = tmp_path / f"backbond-scanner-{sha}.tar"
    archive.write_bytes(b"checksum-valid archive must remain uninspected")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    result = subprocess.run(
        [
            "bash",
            str(HELPER),
            sha,
            str(archive),
            "ghcr.io/corwinfoundation/are-scanner@sha256:"
            "f014212dca8f70b73eb70ec9c993e709f81a0d9f1675fcff92b7458f619a7a8d",
            archive_sha,
            "BEAAA-23948",
        ],
        env={**os.environ, "SCANNER_ARCHIVE_DIR": str(tmp_path)},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert result.stderr == (
        "[scanner-deploy] ERROR: repository digest is not the canonical "
        "c4a58caa image\n"
    )


def test_dev_deploy_emits_all_five_helper_arguments():
    text = DEV_DEPLOY.read_text()
    assert "BACKBOND_CANDIDATE_ID is required" in text
    assert "${Q_SHA} ${Q_REMOTE_ARCHIVE} ${Q_DIGEST} ${Q_ARCHIVE_SHA} ${Q_CANDIDATE}" in text


@pytest.mark.parametrize("field", ["scanner_version", "corpus_version", "build_sha", "release_tag"])
def test_valid_manifest_hash_with_wrong_embedded_identity_fails_before_transaction(tmp_path, field):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    manifest = Path(e["STUB_MANIFEST_FILE"])
    payload = json.loads(manifest.read_text())
    payload[field] = "wrong"
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    e["SCANNER_FROZEN_MANIFEST_SHA"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    before = env_file.read_bytes()
    result = run(e, archive, archive_sha)
    assert result.returncode != 0 and "embedded release identity mismatch" in result.stderr
    assert env_file.read_bytes() == before
    assert not (state / "transactions").exists()
    assert not list(tmp_path.glob("tmp.*"))
    assert "docker rm -f manifest-inspection-container" in calls.read_text()


@pytest.mark.parametrize(
    "failure,diagnostic,create_called,cleanup_called",
    [
        ("STUB_MKTEMP_FAIL", "cannot create embedded-manifest temporary file", False, False),
        ("STUB_CREATE_FAIL", "cannot create manifest inspection container", True, False),
        ("STUB_CP_FAIL", "cannot extract embedded release manifest", True, True),
        ("STUB_RM_FAIL_ONCE", "cannot remove manifest inspection container", True, True),
        ("STUB_HASH_FAIL", "cannot hash embedded release manifest", True, True),
    ],
)
def test_manifest_preflight_tool_failures_leave_no_residuals(
    tmp_path, failure, diagnostic, create_called, cleanup_called
):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    e[failure] = "1"
    before = env_file.read_bytes()
    result = run(e, archive, archive_sha)
    trace = calls.read_text()
    assert result.returncode != 0 and diagnostic in result.stderr
    assert env_file.read_bytes() == before
    assert not (state / "transactions").exists()
    assert not list(tmp_path.glob("tmp.*"))
    assert ("docker create --network none sha256:loaded" in trace) is create_called
    assert ("docker rm -f manifest-inspection-container" in trace) is cleanup_called
    if failure == "STUB_RM_FAIL_ONCE":
        assert trace.count("docker rm -f manifest-inspection-container") == 2
        assert not Path(e["STUB_CONTAINER_STATE"]).exists()


def test_persistent_manifest_container_removal_failure_is_explicit(tmp_path):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    e["STUB_RM_FAIL_PERSISTENT"] = "1"
    result = run(e, archive, archive_sha)
    assert result.returncode != 0
    assert "cannot remove manifest inspection container" in result.stderr
    assert "persistent manifest inspection container cleanup failure" in result.stderr
    assert calls.read_text().count("docker rm -f manifest-inspection-container") == 2
    assert Path(e["STUB_CONTAINER_STATE"]).exists()
    assert not list(tmp_path.glob("tmp.*"))
    assert not (state / "transactions").exists()


@pytest.mark.parametrize("sig,expected_rc", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_manifest_preflight_signal_exits_nonzero_and_cleans_resources(tmp_path, sig, expected_rc):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    e["STUB_PAUSE_CP"] = "1"
    proc = subprocess.Popen(
        ["bash", str(HELPER), SHA, str(archive), DIGEST, archive_sha, CANDIDATE],
        env=e,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    container_state = Path(e["STUB_CONTAINER_STATE"])
    deadline = time.monotonic() + 5
    while not container_state.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert container_state.exists(), "inspection container was not allocated before signal"
    os.killpg(proc.pid, sig)
    _, stderr = proc.communicate(timeout=5)
    assert proc.returncode == expected_rc, stderr
    assert not container_state.exists()
    assert not list(tmp_path.glob("tmp.*"))
    assert not (state / "transactions").exists()
    assert "docker rm -f manifest-inspection-container" in calls.read_text()


def test_invalid_embedded_json_cleans_resources_before_transaction(tmp_path):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    manifest = Path(e["STUB_MANIFEST_FILE"])
    manifest.write_text("{")
    e["SCANNER_FROZEN_MANIFEST_SHA"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    result = run(e, archive, archive_sha)
    assert result.returncode != 0 and "embedded release identity mismatch" in result.stderr
    assert not list(tmp_path.glob("tmp.*"))
    assert not (state / "transactions").exists()
    assert "docker rm -f manifest-inspection-container" in calls.read_text()


def test_preflight_rejects_repository_digest_before_mutation(tmp_path):
    e, env_file, _, archive, archive_sha, calls = harness(tmp_path)
    before=env_file.read_bytes(); result=run(e,archive,archive_sha,digest="example.invalid/x:latest")
    assert result.returncode != 0 and env_file.read_bytes()==before
    assert not calls.exists()


def test_duplicate_env_rejected_before_load(tmp_path):
    e, _, _, archive, archive_sha, calls = harness(tmp_path,duplicate=True)
    result=run(e,archive,archive_sha)
    assert result.returncode != 0 and "duplicate" in result.stderr.lower()
    assert not calls.exists()


def test_preload_cleanup_preserves_current_and_keep_last_then_removes_stale(tmp_path):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    rollback = state / "releases" / ("d" * 40 + ".json")
    rollback.parent.mkdir()
    rollback.write_text(json.dumps({"configured_image":"rollback:image","status":"activated","recorded_at":"2026-09-01T00:00:00Z"}))
    e["STUB_IMAGE_ROWS"] = "current:tag\\tsha256:current\nrollback:tag\\tsha256:rollback\nghcr.io/corwinfoundation/are-scanner:stale\\tsha256:stale"
    stale_archive = archive.parent / ("backbond-scanner-" + "e" * 40 + ".tar")
    stale_archive.write_bytes(b"stale")
    result = run(e, archive, archive_sha)
    assert result.returncode == 0, result.stderr
    trace = calls.read_text()
    assert "docker container prune -f" in trace
    assert "docker image rm ghcr.io/corwinfoundation/are-scanner:stale" in trace
    assert "docker image rm current:tag" not in trace
    assert "docker image rm rollback:tag" not in trace
    assert "docker image prune -f" in trace
    assert "docker builder prune -f" in trace
    assert not stale_archive.exists()


def test_preload_cleanup_removes_each_repository_reference_for_stale_image(tmp_path):
    e, _, _, archive, archive_sha, calls = harness(tmp_path)
    e["STUB_IMAGE_ROWS"] = (
        "current:tag\\tsha256:current\n"
        "ghcr.io/corwinfoundation/are-scanner:old\\tsha256:stale\n"
        "backbond-scanner-live:old\\tsha256:stale"
    )
    result = run(e, archive, archive_sha)
    assert result.returncode == 0, result.stderr
    trace = calls.read_text()
    assert "docker image rm ghcr.io/corwinfoundation/are-scanner:old" in trace
    assert "docker image rm backbond-scanner-live:old" in trace
    assert "docker image rm sha256:stale" not in trace
    assert "docker image rm current:tag" not in trace


def test_preload_cleanup_ignores_non_scanner_images_with_revision_labels(tmp_path):
    e, _, _, archive, archive_sha, calls = harness(tmp_path)
    e["STUB_IMAGE_ROWS"] = (
        "current:tag\\tsha256:current\n"
        "docker.openhands.dev/openhands/openhands:1.7\\tsha256:unrelated\n"
        "backbond-scanner-live:old\\tsha256:stale"
    )
    result = run(e, archive, archive_sha)
    assert result.returncode == 0, result.stderr
    trace = calls.read_text()
    assert "docker image rm backbond-scanner-live:old" in trace
    assert "docker image rm docker.openhands.dev/openhands/openhands:1.7" not in trace


def test_preload_cleanup_skips_stale_reference_used_by_running_container(tmp_path):
    e, _, _, archive, archive_sha, calls = harness(tmp_path)
    stale_ref = "ghcr.io/corwinfoundation/are-scanner:old"
    e["STUB_IMAGE_ROWS"] = f"current:tag\\tsha256:current\n{stale_ref}\\tsha256:stale"
    e["STUB_IMAGE_RM_CONFLICT_REF"] = stale_ref
    result = run(e, archive, archive_sha)
    assert result.returncode == 0, result.stderr
    assert f"docker image rm {stale_ref}" in calls.read_text()
    assert f"retained stale image reference used by a running container: {stale_ref}" in result.stderr


def test_preload_cleanup_still_fails_on_unexpected_image_removal_error(tmp_path):
    e, _, _, archive, archive_sha, _ = harness(tmp_path)
    stale_ref = "backbond-scanner-live:old"
    e["STUB_IMAGE_ROWS"] = f"current:tag\\tsha256:current\n{stale_ref}\\tsha256:stale"
    e["STUB_IMAGE_RM_FAIL_REF"] = stale_ref
    result = run(e, archive, archive_sha)
    assert result.returncode != 0
    assert "unexpected storage failure" in result.stderr


def test_exact_embedded_manifest_identity_success_writes_activated_receipt(tmp_path):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    result=run(e,archive,archive_sha)
    assert result.returncode == 0, result.stderr
    assert f"SCANNER_EDGE_IMAGE=backbond-scanner-live:{SHA}" in env_file.read_text()
    identity=state/"releases"/f"{SHA}.json"; receipt=state/"receipts"/f"{SHA}.json"
    assert json.loads(identity.read_text())["status"] == "activated"
    assert json.loads(receipt.read_text())["release_manifest_sha256"] == e["SCANNER_FROZEN_MANIFEST_SHA"]
    assert json.loads(receipt.read_text())["repository_digest"] == DIGEST
    assert stat.S_IMODE(identity.stat().st_mode) == 0o444
    assert "docker pull" not in calls.read_text()
    assert "docker create --network none sha256:loaded" in calls.read_text()


def test_errexit_survives_preflight_cleanup_and_first_transaction_failure_rolls_back(tmp_path):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    e["STUB_TAG_FAIL"] = "1"
    before = env_file.read_bytes()
    result = run(e, archive, archive_sha)
    trace = calls.read_text()
    assert result.returncode != 0
    assert "ROLLBACK: restoring image, environment, markers, and identity" in result.stderr
    assert env_file.read_bytes() == before
    assert "docker tag sha256:loaded" in trace
    assert "systemctl restart backbond-scanner-live.service" in trace
    assert not (state / "current_version").exists()
    assert not (state / "receipts" / f"{SHA}.json").exists()
    assert list((state / "transactions").iterdir())


def test_helper_asserts_errexit_before_transaction():
    text = HELPER.read_text()
    cleanup = text.index("cleanup_manifest_resources || die")
    assertion = text.index("errexit disabled after manifest preflight cleanup")
    transaction = text.index('LOCAL_TAG="backbond-scanner-live:$SHA"')
    assert cleanup < assertion < transaction


def test_200_identity_gate_rolls_back_absent_markers(tmp_path):
    e, env_file, state, archive, archive_sha, _ = harness(tmp_path,curl_fail=True)
    before=env_file.read_text(); result=run(e,archive,archive_sha)
    assert result.returncode != 0 and "rollback" in result.stderr.lower()
    assert env_file.read_text()==before
    assert not (state/"current_version").exists()
    assert not (state/"releases"/f"{SHA}.json").exists()


def test_rollback_failure_leaves_durable_evidence(tmp_path):
    e, _, state, archive, archive_sha, _ = harness(tmp_path,curl_fail=True,rollback_fail=True)
    result=run(e,archive,archive_sha)
    assert result.returncode == 90 and "ROLLBACK_FAILED" in result.stderr
    assert list((state/"transactions").glob("*/ROLLBACK_FAILED"))


def test_single_err_int_term_trap_and_no_secret_output_or_registry_pull():
    text=HELPER.read_text()
    assert "trap 'rollback $?' ERR INT TERM" in text
    assert "docker pull" not in text
    assert "cat \"$ENV_FILE\"" not in text


@pytest.mark.parametrize("field", ["archive", "revision", "manifest", "candidate"])
def test_each_identity_input_or_embedded_anchor_mismatch_fails_closed(tmp_path, field):
    e, env_file, _, archive, archive_sha, _ = harness(tmp_path)
    args=["bash",str(HELPER),SHA,str(archive),DIGEST,archive_sha,CANDIDATE]
    if field == "archive": archive.write_bytes(b"tampered")
    elif field == "revision": e["STUB_SHA"]="d"*40
    elif field == "manifest": Path(e["STUB_MANIFEST_FILE"]).write_text("tampered")
    else: args[-1]="invalid-candidate"
    before=env_file.read_bytes()
    result=subprocess.run(args,env=e,text=True,capture_output=True)
    assert result.returncode != 0 and env_file.read_bytes() == before


@pytest.mark.parametrize("target,mode", [("env",0o644),("unit",0o600),("state",0o700)])
def test_mode_rejection_precedes_image_load(tmp_path, target, mode):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    path={"env":env_file,"unit":Path(e["SCANNER_UNIT_FILE"]),"state":state}[target]
    path.chmod(mode)
    result=run(e,archive,archive_sha)
    assert result.returncode != 0 and "mode" in result.stderr.lower()
    assert not calls.exists()


def test_owner_rejection_precedes_image_load(tmp_path):
    e, _, _, archive, archive_sha, calls = harness(tmp_path)
    e["SCANNER_EXPECTED_OWNER_UID"]=str(os.getuid()+1)
    result=run(e,archive,archive_sha)
    assert result.returncode != 0 and "root-owned" in result.stderr
    assert not calls.exists()


def test_arbitrary_archive_path_rejected(tmp_path):
    e, env_file, _, archive, archive_sha, calls = harness(tmp_path)
    other=tmp_path/"caller-selected.tar"; other.write_bytes(archive.read_bytes())
    result=run(e,other,archive_sha)
    assert result.returncode != 0 and "fixed candidate path" in result.stderr
    assert not calls.exists() and "old:image" in env_file.read_text()


def _install_prior_tuple(e, env_file, state):
    prior_sha="9"*40; prior_image=f"backbond-scanner-live:{prior_sha}"
    prior=state/"releases"/f"{prior_sha}.json"; prior.parent.mkdir()
    record={"candidate_id":"BEAAA-OLD","commit_sha":prior_sha,"current_version":prior_sha,"configured_image":prior_image,"image_digest":"repo/x@sha256:"+"8"*64,"running_container_revision":prior_sha,"internal_version_sha":prior_sha,"status":"activated"}
    prior.write_text(json.dumps(record)); prior.chmod(0o444)
    (state/"current_version").write_text(prior_sha+"\n")
    env_file.write_text(f"KEEP=1\nSCANNER_EDGE_IMAGE={prior_image}\nBACKBOND_RELEASE_IDENTITY_PATH={prior}\n"); env_file.chmod(0o600)
    return record


def test_stale_http_200_during_rollback_is_durable_failure(tmp_path):
    e, env_file, state, archive, archive_sha, _ = harness(tmp_path)
    prior=_install_prior_tuple(e,env_file,state)
    e["STUB_FIRST_RESTART_FAIL"]="1"  # force rollback; curl returns contradictory new tuple with 200
    result=run(e,archive,archive_sha)
    assert result.returncode == 92 and "tuple mismatch" in result.stderr
    assert json.loads((state/"releases"/(prior["commit_sha"]+".json")).read_text()) == prior
    assert (state/"current_version").read_text().strip() == prior["commit_sha"]
    assert list((state/"transactions").glob("*/ROLLBACK_FAILED"))


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_real_signal_runs_transactional_rollback(tmp_path, sig):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    before=env_file.read_text(); e["STUB_PAUSE_FIRST_RESTART"]="1"
    proc=subprocess.Popen(["bash",str(HELPER),SHA,str(archive),DIGEST,archive_sha,CANDIDATE],env=e,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    deadline=time.time()+5
    while time.time()<deadline and "systemctl restart" not in (calls.read_text() if calls.exists() else ""): time.sleep(.02)
    os.killpg(proc.pid,sig)
    _,stderr=proc.communicate(timeout=10)
    assert proc.returncode != 0 and "ROLLBACK" in stderr
    assert env_file.read_text() == before and not (state/"current_version").exists()


def test_isolated_unit_cutover_and_rollback_evidence(tmp_path):
    e, _, _, archive, archive_sha, calls = harness(tmp_path,curl_fail=True)
    result=run(e,archive,archive_sha)
    assert result.returncode != 0
    trace=calls.read_text()
    assert trace.count("systemctl restart backbond-scanner-live.service") == 2
    assert "docker tag sha256:loaded backbond-scanner-live:" in trace
    unit=UNIT.read_text()
    assert ":/var/lib/backbond-deploy-agent:ro" in unit and "--read-only" in unit
