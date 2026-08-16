import importlib.util, io, os, socket, stat, contextlib, shutil
from pathlib import Path
from unittest import mock

SRC = "demo-real-agent/host/real_agent_demo_secret_preflight.py"
spec = importlib.util.spec_from_file_location("pf", SRC)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

ROOT = Path("/tmp/qa3/fakeroot")
UID, GID = os.getuid(), os.getgid()

def build(backend="proxy"):
    if ROOT.exists(): shutil.rmtree(ROOT)
    (ROOT/"etc").mkdir(parents=True); (ROOT/"run").mkdir(parents=True)
    env = ROOT/"etc"/"real-agent-demo.env"
    if backend == "proxy":
        env.write_text("DEMO_ACCESS_TOKEN=t\nARE_BUNDLE_SIGNING_KEY=k\nDEMO_JUDGE_BACKEND=claude-code-proxy\nCLAUDE_JUDGE_PROXY_TOKEN=p\n")
    else:
        env.write_text("DEMO_ACCESS_TOKEN=t\nARE_BUNDLE_SIGNING_KEY=k\nOPENROUTER_API_KEY=o\n")
    env.chmod(0o600)
    allow = ROOT/"etc"/"real-agent-demo-deploy.allow"
    allow.write_text(m.RELEASE_SHA); allow.chmod(0o600)
    (ROOT/"etc").chmod(0o755)
    sock = ROOT/"run"/"judge.sock"
    # rebind defaults (bound at def time)
    m.validate.__defaults__ = (env,)
    m._read_fixed_file.__defaults__ = (env,)
    m.judge_socket_readiness.__defaults__ = (sock,)
    m.validate_state_dir.__defaults__ = (ROOT/"etc",)
    m.validate_authorization.__defaults__ = (allow,)
    m.consume_authorization.__defaults__ = (allow, ROOT/"etc"/"real-agent-demo-deploy.consumed")
    m.EXPECTED_UID, m.EXPECTED_GID = UID, GID
    m.AUTH_UID, m.AUTH_GID = UID, GID
    return env, allow, sock

def make(kind, sock):
    if kind == "absent": return
    if kind == "regular": sock.write_text("x"); return
    if kind == "directory": sock.mkdir(); return
    if kind == "fifo": os.mkfifo(sock); return
    if kind == "symlink_to_socket":
        real = sock.parent/"real.sock"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.bind(str(real)); s.listen(1)
        sock.symlink_to(real); return s
    if kind == "socket":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.bind(str(sock)); s.listen(1); return s

print(f"{'BACKEND/INODE':<34}{'rc':<5}{'grant':<12}{'JUDGE_SOCKET emitted':<24}stderr")
print("-"*110)
for backend in ("proxy","api"):
    kinds = ("absent","regular","directory","fifo","symlink_to_socket","socket") if backend=="proxy" else ("absent","socket")
    for kind in kinds:
        env, allow, sock = build(backend)
        held = make(kind, sock)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(os, "geteuid", return_value=0):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = m.main([])
        emitted = [l for l in out.getvalue().splitlines() if l.startswith("JUDGE_SOCKET")]
        grant = "INTACT" if allow.exists() else "CONSUMED"
        print(f"{backend+'/'+kind:<34}{rc:<5}{grant:<12}{(emitted[0] if emitted else '-'):<24}{err.getvalue().strip()[:44]}")
        if held: held.close()

print()
print("=== ONE-SHOT REPLAY (proxy, ready socket): run helper twice ===")
env, allow, sock = build("proxy"); held = make("socket", sock)
for attempt in (1,2):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(os, "geteuid", return_value=0):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = m.main([])
    print(f"  attempt {attempt}: rc={rc} grant={'INTACT' if allow.exists() else 'CONSUMED'} "
          f"stdout_lines={len(out.getvalue().splitlines())} stderr={err.getvalue().strip()!r}")
    if attempt == 1: first_out = out.getvalue()
held.close()

print()
print("=== NAMES-ONLY: sentinel secret values in the env file ===")
env, allow, sock = build("proxy"); held = make("socket", sock)
env.write_text("DEMO_ACCESS_TOKEN=SENTINEL_AAA\nARE_BUNDLE_SIGNING_KEY=SENTINEL_BBB\n"
               "DEMO_JUDGE_BACKEND=claude-code-proxy\nCLAUDE_JUDGE_PROXY_TOKEN=SENTINEL_CCC\n")
env.chmod(0o600)
out, err = io.StringIO(), io.StringIO()
with mock.patch.object(os, "geteuid", return_value=0):
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = m.main([])
blob = out.getvalue() + err.getvalue()
print(blob.rstrip())
leaked = [s for s in ("SENTINEL_AAA","SENTINEL_BBB","SENTINEL_CCC") if s in blob]
print("leaked sentinels:", leaked if leaked else "NONE -- names only")
held.close()
