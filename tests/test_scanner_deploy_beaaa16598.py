"""BEAAA-16598 — systemd-owned Scanner deploy path.

Covers the on-box helper ``scripts/deploy/scanner-deploy.sh`` (image selection by
exact revision label, local retag, env backup/repoint, systemd restart, bounded
``/openapi.json`` readiness, and automatic rollback on every post-backup
failure), the ``dev_deploy.sh`` operator emission (one SCP + helper command, no
executed SSH credential path), and the retirement of the obsolete deploy
machinery from the active deploy path (scripts + docs).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/deploy/scanner-deploy.sh"
DEV_DEPLOY = ROOT / "scripts/deploy/dev_deploy.sh"
DOCS_DEPLOY = ROOT / "docs/deploy"

SHA = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
IMAGE_ID = "sha256:" + "f" * 64
LIVE_TAG = f"backbond-scanner-live:{SHA}"
OLD_IMAGE = "ghcr.io/corwinfoundation/are-scanner@sha256:" + "b" * 64

# Retired mechanisms that must never appear in ANY active deploy script.
# - box_deploy / operator_push / operator-mediated : the retired ADR-0057
#   operator-push pipeline the systemd-owned path replaces.
# - /etc/are-scanner : the retired on-box layout root.
# - /version : the retired health/readiness surface (superseded by /openapi.json).
# - "docker pull" : direct docker-pull / live-swap assumption; the box now loads
#   the SCP-delivered tar (`docker load`) and never pulls a live container.
# A bare `ghcr` reference is a GHCR live-container pull/swap on the box and is
# ALSO forbidden on every active script EXCEPT dev_deploy.sh, whose only ghcr use
# is PUSHING the signed provenance image (build side, never a live-box swap).
RETIRED_MECHANISM_TOKENS = (
    "box_deploy",
    "operator_push",
    "operator-mediated",
    "/etc/are-scanner",
    "/version",
    "docker pull",
)


# --------------------------------------------------------------------------- #
# Test harness: stub docker / systemctl / curl / sleep / install                #
# --------------------------------------------------------------------------- #

def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    return path


def _make_env(
    tmp_path: Path,
    *,
    revision: str = SHA,
    systemctl_fail: bool = False,
    curl_fail: bool = False,
    install_fail: bool = False,
    with_image_line: bool = True,
):
    """Build a stubbed on-box environment and return (env, paths)."""
    binp = tmp_path / "bin"
    binp.mkdir()
    log = tmp_path / "calls.log"

    # docker stub: image ls emits IMAGE_ID only when the filtered revision matches
    # STUB_REVISION; image inspect echoes STUB_REVISION; load/tag are no-ops.
    _write(binp / "docker", f"""#!/usr/bin/env bash
echo "docker $*" >> "{log}"
sub="$1"; shift || true
case "$sub" in
  load) exit 0 ;;
  tag) exit 0 ;;
  image)
    verb="$1"; shift || true
    case "$verb" in
      ls)
        want=""
        while [ $# -gt 0 ]; do case "$1" in --filter) want="$2"; shift 2;; *) shift;; esac; done
        wsha="${{want##*=}}"
        [ "$wsha" = "$STUB_REVISION" ] && echo "{IMAGE_ID}"
        exit 0 ;;
      inspect) echo "$STUB_REVISION"; exit 0 ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac
""")

    _write(binp / "systemctl", f"""#!/usr/bin/env bash
echo "systemctl $*" >> "{log}"
[ -n "${{STUB_SYSTEMCTL_FAIL:-}}" ] && exit 1
exit 0
""")

    _write(binp / "curl", f"""#!/usr/bin/env bash
echo "curl $*" >> "{log}"
[ -n "${{STUB_CURL_FAIL:-}}" ] && exit 22
exit 0
""")

    # Make readiness loop instant.
    _write(binp / "sleep", """#!/usr/bin/env bash
exit 0
""")

    # install passthrough that can be forced to fail (repoint-stage failure).
    _write(binp / "install", """#!/usr/bin/env bash
[ -n "${STUB_INSTALL_FAIL:-}" ] && exit 1
exec /usr/bin/install "$@"
""")

    env_file = tmp_path / "scanner-edge.env"
    lines = ["# retained comment\n", "OTHER_SETTING=keep-me\n"]
    if with_image_line:
        lines.append(f"SCANNER_EDGE_IMAGE={OLD_IMAGE}\n")
    env_file.write_text("".join(lines))

    tar = tmp_path / f"backbond-scanner-{SHA}.tar"
    tar.write_text("fake-oci-archive")

    env = dict(os.environ)
    env["PATH"] = f"{binp}:{env['PATH']}"
    env["STUB_REVISION"] = revision
    env["SCANNER_DOCKER"] = str(binp / "docker")
    env["SCANNER_SYSTEMCTL"] = str(binp / "systemctl")
    env["SCANNER_CURL"] = str(binp / "curl")
    env["SCANNER_EDGE_ENV"] = str(env_file)
    env["SCANNER_LIVE_SERVICE"] = "backbond-scanner-live.service"
    env["SCANNER_READINESS_URL"] = "http://127.0.0.1:8077/openapi.json"
    env["SCANNER_READINESS_TIMEOUT"] = "2"
    env["SCANNER_READINESS_INTERVAL"] = "1"
    if systemctl_fail:
        env["STUB_SYSTEMCTL_FAIL"] = "1"
    if curl_fail:
        env["STUB_CURL_FAIL"] = "1"
    if install_fail:
        env["STUB_INSTALL_FAIL"] = "1"

    return env, env_file, tar, log


def _run(env, *args):
    return subprocess.run(
        ["bash", str(HELPER), *args],
        env=env,
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------- #
# scanner-deploy.sh — syntax & interface                                       #
# --------------------------------------------------------------------------- #

def test_helper_shell_syntax_passes():
    assert subprocess.run(["bash", "-n", str(HELPER)]).returncode == 0


def test_helper_requires_git_sha(tmp_path):
    env, *_ = _make_env(tmp_path)
    res = _run(env)  # no sha
    assert res.returncode != 0
    assert "usage" in (res.stderr + res.stdout).lower()


def test_helper_rejects_non_hex_sha(tmp_path):
    env, *_ = _make_env(tmp_path)
    res = _run(env, "not-a-hex-sha")
    assert res.returncode != 0
    assert "hex" in res.stderr.lower()


# --------------------------------------------------------------------------- #
# Success path: label selection, retag, backup, repoint, restart, readiness     #
# --------------------------------------------------------------------------- #

def test_helper_success_selects_by_revision_retags_repoints_and_probes(tmp_path):
    env, env_file, tar, log = _make_env(tmp_path)
    res = _run(env, SHA, str(tar))
    assert res.returncode == 0, res.stderr

    calls = log.read_text()
    # image loaded from the SCP-delivered tar (no pull anywhere)
    assert f"docker load -i {tar}" in calls
    assert "docker pull" not in calls
    # selected by EXACT revision label, then retagged to the LOCAL live tag
    assert f"label=org.opencontainers.image.revision={SHA}" in calls
    assert f"docker tag {IMAGE_ID} {LIVE_TAG}" in calls
    # systemd owns the container: helper only restarts the unit
    assert "systemctl restart backbond-scanner-live.service" in calls
    # bounded readiness on the openapi surface
    assert "curl -fsS -o /dev/null http://127.0.0.1:8077/openapi.json" in calls

    # env repointed at the local live tag; other lines preserved
    final = env_file.read_text()
    assert f"SCANNER_EDGE_IMAGE={LIVE_TAG}" in final
    assert "OTHER_SETTING=keep-me" in final
    assert OLD_IMAGE not in final
    # backup captured the pre-change env
    backup = Path(str(env_file) + f".pre-{SHA}")
    assert backup.exists()
    assert f"SCANNER_EDGE_IMAGE={OLD_IMAGE}" in backup.read_text()
    # backup written 0600
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_helper_appends_image_var_when_absent(tmp_path):
    env, env_file, tar, log = _make_env(tmp_path, with_image_line=False)
    res = _run(env, SHA, str(tar))
    assert res.returncode == 0, res.stderr
    assert f"SCANNER_EDGE_IMAGE={LIVE_TAG}" in env_file.read_text()


# --------------------------------------------------------------------------- #
# Image selection is by EXACT revision label (never a registry ref)             #
# --------------------------------------------------------------------------- #

def test_helper_fails_closed_without_env_mutation_when_no_matching_revision(tmp_path):
    # box holds an image whose revision label is NOT the requested sha
    env, env_file, tar, log = _make_env(tmp_path, revision="0" * 40)
    before = env_file.read_text()
    res = _run(env, SHA, str(tar))
    assert res.returncode != 0
    assert "no locally loaded image" in res.stderr.lower()
    # nothing mutated, no backup created (failure is pre-backup)
    assert env_file.read_text() == before
    assert not Path(str(env_file) + f".pre-{SHA}").exists()


# --------------------------------------------------------------------------- #
# Rollback on EACH post-backup failure                                          #
# --------------------------------------------------------------------------- #

def _assert_rolled_back(res, env_file):
    assert res.returncode != 0
    assert "rollback" in res.stderr.lower()
    # env restored to the pre-change contents
    assert f"SCANNER_EDGE_IMAGE={OLD_IMAGE}" in env_file.read_text()
    assert LIVE_TAG not in env_file.read_text()


def test_helper_rolls_back_when_repoint_install_fails(tmp_path):
    env, env_file, tar, log = _make_env(tmp_path, install_fail=True)
    res = _run(env, SHA, str(tar))
    _assert_rolled_back(res, env_file)


def test_helper_rolls_back_when_systemd_restart_fails(tmp_path):
    env, env_file, tar, log = _make_env(tmp_path, systemctl_fail=True)
    res = _run(env, SHA, str(tar))
    _assert_rolled_back(res, env_file)


def test_helper_rolls_back_on_readiness_timeout(tmp_path):
    env, env_file, tar, log = _make_env(tmp_path, curl_fail=True)
    res = _run(env, SHA, str(tar))
    _assert_rolled_back(res, env_file)
    assert "readiness timeout" in res.stderr.lower()
    # readiness was actually probed before giving up
    assert "http://127.0.0.1:8077/openapi.json" in log.read_text()


# --------------------------------------------------------------------------- #
# dev_deploy.sh — one emitted SCP + helper command, no executed SSH path        #
# --------------------------------------------------------------------------- #

def test_dev_deploy_emits_scp_then_helper_and_never_runs_ssh():
    script = DEV_DEPLOY.read_text()
    # still builds/signs/stages the provenance image
    assert "docker build" in script
    assert "docker push" in script
    assert "cosign sign --yes" in script
    assert "docker save" in script
    assert "git rev-parse origin/master" in script
    # emits exactly one operator command: scp the tar, then invoke the helper
    assert 'COMMAND="scp ' in script
    assert "scanner-deploy" in script
    assert "box credential required" in script
    # the emitted command SCPs then ssh-invokes the helper — but SCP/SSH are only
    # ever inside the emitted string, never executed on the agent side.
    executable = [
        ln.strip()
        for ln in script.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not any(ln.startswith("scp ") for ln in executable)
    assert not any(ln.startswith("ssh ") for ln in executable)
    assert "BACKBOND_DEPLOY_IDENTITY" not in script


def test_dev_deploy_operator_command_drops_old_mechanism():
    """The emitted operator command must not carry any retired mechanism."""
    script = DEV_DEPLOY.read_text()
    command_line = next(ln for ln in script.splitlines() if ln.startswith("COMMAND="))
    for forbidden in ("box_deploy", "/version", "ghcr", "/etc/are-scanner"):
        assert forbidden not in command_line, f"emitted command still references {forbidden}"


# --------------------------------------------------------------------------- #
# Scoped forbidden-reference search across the ACTIVE deploy path               #
# --------------------------------------------------------------------------- #

def _active_docs():
    return [p for p in DOCS_DEPLOY.glob("*.md")]  # top-level only; history/ excluded


def test_helper_has_no_retired_mechanism_references():
    text = HELPER.read_text()
    # The box helper additionally may never reference ghcr at all: it loads the
    # SCP-delivered tar locally and must never pull/swap a live container.
    for forbidden in (*RETIRED_MECHANISM_TOKENS, "ghcr"):
        assert forbidden not in text, f"scanner-deploy.sh still references {forbidden}"
    # positively: the live tag is a LOCAL tag and readiness is /openapi.json
    assert "backbond-scanner-live:" in text
    assert "/openapi.json" in text


def test_active_deploy_docs_have_no_retired_mechanism_references():
    for doc in _active_docs():
        text = doc.read_text()
        for forbidden in ("box_deploy", "operator_push", "/etc/are-scanner", "/version"):
            assert forbidden not in text, f"{doc.name} references retired {forbidden}"


# GHCR is pushed to (provenance, build side) only by dev_deploy.sh. Every other
# active deploy script — and any box-side path — must never reference ghcr,
# because a ghcr reference there is a live-container pull/swap.
_GHCR_PUSH_EXEMPT = {"dev_deploy.sh"}


def _active_scripts():
    """Every executable deploy artifact in scripts/deploy/ at top level.

    Covers ``*.sh`` and ``*.py`` plus any other file carrying the executable bit
    (extensionless helpers included); ``history/`` is a subdirectory and is
    excluded by using ``iterdir`` (non-recursive).
    """
    scripts_deploy = ROOT / "scripts/deploy"
    out = []
    for p in scripts_deploy.iterdir():
        if not p.is_file():
            continue
        if p.suffix in (".sh", ".py") or (p.stat().st_mode & stat.S_IXUSR):
            out.append(p)
    return out


def test_active_deploy_scripts_have_no_retired_mechanism_references():
    """No active deploy script may reference any retired ADR-0057 / live-swap mechanism.

    Sweeps every active executable deploy artifact for the retired operator-push
    pipeline (box_deploy/operator_push/operator-mediated), the retired on-box
    layout (/etc/are-scanner) and health surface (/version), and direct
    docker-pull / live-swap assumptions ("docker pull"). GHCR live-container
    pulls/swaps are forbidden on every active script except the provenance PUSH
    in dev_deploy.sh.
    """
    swept = _active_scripts()
    assert swept, "active deploy sweep found no scripts — glob is mis-scoped"
    for script in swept:
        text = script.read_text()
        for forbidden in RETIRED_MECHANISM_TOKENS:
            assert forbidden not in text, (
                f"{script.name} references retired mechanism: {forbidden}"
            )
        if script.name not in _GHCR_PUSH_EXEMPT:
            assert "ghcr" not in text, (
                f"{script.name} references ghcr — a live-container pull/swap is "
                f"forbidden outside the dev_deploy.sh provenance push"
            )


def test_retired_scripts_preserved_under_history():
    # acceptance_checks.sh is NOT retired — it is a live shared utility invoked by
    # deploy_agent_acceptance.py. Only the three genuine ADR-0057 entrypoints are
    # retired: operator_push.sh, box_deploy.sh, post_deploy_record.py.
    history = ROOT / "scripts/deploy/history"
    assert history.is_dir()
    preserved = {p.name for p in history.iterdir() if p.is_file()}
    for name in ("box_deploy.sh", "operator_push.sh", "post_deploy_record.py"):
        assert name in preserved, f"retired script {name} not preserved under scripts/deploy/history/"
    # they must NOT remain in the active scripts/deploy/ directory
    active = {p.name for p in _active_scripts()}
    for name in ("box_deploy.sh", "operator_push.sh", "post_deploy_record.py"):
        assert name not in active, f"retired script {name} still active in scripts/deploy/"
    # acceptance_checks.sh MUST be active (live consumer: deploy_agent_acceptance.py)
    assert "acceptance_checks.sh" not in preserved, \
        "acceptance_checks.sh must not be in history/ — it is a live shared utility"
    assert any(p.name == "acceptance_checks.sh" for p in _active_scripts()), \
        "acceptance_checks.sh must be present in active scripts/deploy/ path"


# --------------------------------------------------------------------------- #
# Exactly one active deploy runbook; history preserved                          #
# --------------------------------------------------------------------------- #

def test_exactly_one_active_deploy_mechanism_runbook_matches_topology():
    runbook = DOCS_DEPLOY / "scanner-deploy-runbook.md"
    assert runbook.exists()
    text = runbook.read_text()
    # matches the verified srv1749248 topology
    for token in (
        "backbond-scanner-live.service",
        "/etc/backbond/scanner-edge.env",
        "SCANNER_EDGE_IMAGE",
        "http://127.0.0.1:8077/openapi.json",
        "org.opencontainers.image.revision",
    ):
        assert token in text, f"runbook missing topology token {token}"
    # it is the single runbook that documents scanner-deploy.sh
    mechanism_runbooks = [
        p for p in _active_docs() if "scanner-deploy.sh" in p.read_text()
    ]
    assert mechanism_runbooks == [runbook]


def test_retired_docs_preserved_under_history():
    history = DOCS_DEPLOY / "history"
    assert history.is_dir()
    preserved = {p.name for p in history.glob("*.md")}
    for name in (
        "cd-pipeline.md",
        "first-container-cutover.md",
        "dry-run-acceptance.md",
        "first-two-production-deploy-observation.md",
    ):
        assert name in preserved, f"historical evidence {name} not preserved"
    # they must NOT remain in the active deploy directory
    active = {p.name for p in _active_docs()}
    assert "cd-pipeline.md" not in active
    assert "first-container-cutover.md" not in active


# --------------------------------------------------------------------------- #
# The image bakes the revision label the helper selects on                      #
# --------------------------------------------------------------------------- #

def test_image_bakes_revision_label_used_for_selection():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "ARG GIT_SHA" in dockerfile
    assert 'org.opencontainers.image.revision="${GIT_SHA}"' in dockerfile
