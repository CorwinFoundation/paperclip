"""Focused acceptance for the BEAAA-20076 atomic activation helper."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/deploy/scanner-deploy.sh"
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
    _write_exe(bin_dir / "docker", f'''#!/bin/sh
echo "docker $*" >>"{calls}"
case "$1 $2" in
 "image ls") echo sha256:loaded;;
 "image inspect") case "$*" in *manifest-sha256*) echo "$STUB_MANIFEST";; *) echo "$STUB_SHA";; esac;;
 "inspect --format") case "$*" in *Labels*) echo "$STUB_SHA";; *) echo sha256:running;; esac;;
esac
exit 0
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
    e.update({"PATH":f"{bin_dir}:{e['PATH']}","SCANNER_EDGE_ENV":str(env_file),"SCANNER_ARCHIVE_DIR":str(tmp_path),"SCANNER_STATE_DIR":str(state),"SCANNER_UNIT_FILE":str(unit),"SCANNER_DOCKER":str(bin_dir/"docker"),"SCANNER_SYSTEMCTL":str(bin_dir/"systemctl"),"SCANNER_CURL":str(bin_dir/"curl"),"SCANNER_READINESS_TIMEOUT":"1","SCANNER_READINESS_INTERVAL":"1","SCANNER_EXPECTED_OWNER_UID":str(os.getuid()),"SCANNER_EXPECTED_OWNER_GID":str(os.getgid()),"SCANNER_FROZEN_SHA":SHA,"SCANNER_FROZEN_REPO_DIGEST":DIGEST,"SCANNER_FROZEN_ARCHIVE_SHA":archive_sha,"SCANNER_FROZEN_MANIFEST_SHA":MANIFEST,"SCANNER_FROZEN_CANDIDATE":CANDIDATE,"STUB_SHA":SHA,"STUB_MANIFEST":MANIFEST,"STUB_IDENTITY":identity,"STUB_CURL_FAIL":"1" if curl_fail else "","STUB_ROLLBACK_FAIL":"1" if rollback_fail else ""})
    return e, env_file, state, archive, archive_sha, calls


def run(e, archive, archive_sha, *, digest=DIGEST):
    return subprocess.run(["bash",str(HELPER),SHA,str(archive),digest,archive_sha,CANDIDATE],env=e,text=True,capture_output=True)


def test_shell_syntax_and_exact_read_only_mount():
    assert subprocess.run(["bash","-n",str(HELPER)]).returncode == 0
    assert "--volume /var/lib/backbond-deploy-agent:/var/lib/backbond-deploy-agent:ro" in UNIT.read_text()


def test_preflight_rejects_repository_digest_before_mutation(tmp_path):
    e, env_file, _, archive, archive_sha, calls = harness(tmp_path)
    before=env_file.read_bytes(); result=run(e,archive,archive_sha,digest="example.invalid/x@sha256:"+"d"*64)
    assert result.returncode != 0 and env_file.read_bytes()==before
    assert not calls.exists()


def test_duplicate_env_rejected_before_load(tmp_path):
    e, _, _, archive, archive_sha, calls = harness(tmp_path,duplicate=True)
    result=run(e,archive,archive_sha)
    assert result.returncode != 0 and "duplicate" in result.stderr.lower()
    assert not calls.exists()


def test_exact_two_anchor_success_writes_activated_receipt(tmp_path):
    e, env_file, state, archive, archive_sha, calls = harness(tmp_path)
    result=run(e,archive,archive_sha)
    assert result.returncode == 0, result.stderr
    assert f"SCANNER_EDGE_IMAGE=backbond-scanner-live:{SHA}" in env_file.read_text()
    identity=state/"releases"/f"{SHA}.json"; receipt=state/"receipts"/f"{SHA}.json"
    assert json.loads(identity.read_text())["status"] == "activated"
    assert json.loads(receipt.read_text())["release_manifest_sha256"] == MANIFEST
    assert json.loads(receipt.read_text())["repository_digest"] == DIGEST
    assert stat.S_IMODE(identity.stat().st_mode) == 0o444
    assert "docker pull" not in calls.read_text()


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
def test_each_frozen_anchor_mismatch_fails_closed(tmp_path, field):
    e, env_file, _, archive, archive_sha, _ = harness(tmp_path)
    args=["bash",str(HELPER),SHA,str(archive),DIGEST,archive_sha,CANDIDATE]
    if field == "archive": archive.write_bytes(b"tampered")
    elif field == "revision": e["STUB_SHA"]="d"*40
    elif field == "manifest": e["STUB_MANIFEST"]="e"*64
    else: args[-1]="BEAAA-1"
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
