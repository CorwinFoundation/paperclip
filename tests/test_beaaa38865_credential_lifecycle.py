"""BEAAA-38865 - the scanner drops customer auth material when a scan ends.

The public QA intake POSTs ``credential_secret`` (the customer's endpoint auth
value) to ``POST /scanner/v1/scans`` and ``/scanner/v1/connectivity-check``.
These tests drive that secret through the real handler and runtime: the real
FastAPI edge, ``ScanRuntime.kickoff``, ``make_production_scan_runtime``'s
fork-spawned Prober and Scorer, the real customer HTTP runner, and on-disk
scan_log, context-envelope, control-ledger, fired-probe-ledger, bundle and
full-scan job stores, all rooted under ``tmp_path``. The only test double is
the customer agent: a loopback HTTP server that the runner factory points the
real runner at. Some of its routes echo the credential back.

After every terminal path (completed, failed, cancelled by timeout, the
connectivity check, and a SIGKILLed process followed by a restart) the canary
secret must be gone from:

* the in-memory credential vault (``harness.credentials._VAULT_STORE``);
* every file under the runtime's data dirs and ``TMPDIR``;
* captured log records and captured stdout/stderr;
* the HTTP response bodies, webhook outbox records and posted webhook bodies.

The exception is the agent itself, which must receive the credential. Each
test checks that it did, so a pass cannot come from a scan that never sent it.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover - fastapi is a test dependency
    pytest.skip(f"fastapi not installed: {exc}", allow_module_level=True)

from api import production_controls
from api import scan_runtime_factory
from api.app import create_app
from api.bundle import BundleStore, FileBundleStorage
from api.full_scan_executor import FullScanJobStore
from api.full_scan_job_persistence import FullScanJobPersistence
from api.scan_handlers import connectivity_check_handler, scan_handler, HandlerError
from api.scan_log import ScanLog
from api.scan_runtime_factory import (
    _production_score_envelope_factory,
    build_v0_corpus_tarball,
    make_production_scan_runtime,
)
from api.webhook_outbox import InMemoryOutboxStore, WebhookOutbox
from harness import credentials, egress
from harness.context_envelope import ContextEnvelopeStore
from harness.customer_runner import customer_agent_http_runner_factory
from scanners.canary_anti_gaming import AuditSpineFiredProbeLedger
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.score_runtime import ScoreRuntime
from scanners.starter_pack import STARTER_PACK_ID
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget
from harness.runner import MockRunner


REPO_ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "partner-38865"
AGENT_ID = "a" * 64
AGENT_URL = "https://fixture-agent-38865.example.test/chat"
HANG_SECONDS = 30.0


def _canary() -> str:
    return f"SECRET-38865-canary-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Loopback test seam (same as tests/integration/conftest.py): the real runner
# refuses loopback addresses in production; allow them in this test process
# only so the runner can reach the fake agent.
# ---------------------------------------------------------------------------


def _allow_loopback() -> None:
    import ipaddress

    original = egress._is_forbidden_resolved_ip
    if getattr(original, "_beaaa38865", False):
        return

    def _guard(address: str) -> bool:
        try:
            if ipaddress.ip_address(address).is_loopback:
                return False
        except ValueError:
            pass
        return original(address)

    _guard._beaaa38865 = True  # type: ignore[attr-defined]
    egress._is_forbidden_resolved_ip = _guard


@pytest.fixture(autouse=True)
def _loopback_and_tmpdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    original = egress._is_forbidden_resolved_ip
    _allow_loopback()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    # Anything that uses ``tempfile`` (parent or fork children) lands under
    # tmp_path so the byte walk below covers it.
    import tempfile

    monkeypatch.setenv("TMPDIR", str(tmp_dir))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_dir))
    try:
        yield
    finally:
        egress._is_forbidden_resolved_ip = original


# ---------------------------------------------------------------------------
# Fake customer agent.
# ---------------------------------------------------------------------------


class _Agent:
    def __init__(self) -> None:
        self.seen_auth: list[str] = []
        self.first_request = threading.Event()
        self.release = threading.Event()


def _make_handler(agent: _Agent) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            auth = self.headers.get("Authorization") or self.headers.get("X-Agent-Key") or ""
            agent.seen_auth.append(auth)
            agent.first_request.set()
            mode = self.path.strip("/")
            if mode == "hang":
                agent.release.wait(HANG_SECONDS)
            if mode == "fail401":
                body = json.dumps({"error": f"invalid credential {auth}"}).encode()
                self.send_response(401)
                # Body echo only. httpcore's DEBUG trace logs response
                # headers verbatim, so an agent that reflects the credential
                # in a response HEADER is logged when LOG_LEVEL=DEBUG. The
                # scanner runs uvicorn at its INFO default, where that trace
                # is not emitted; this is a known residual, not covered here.
                self.send_header("WWW-Authenticate", 'Bearer realm="x"')
            else:
                text = f"I will not repeat your key {auth}" if mode == "echo" else "safe reply"
                body = json.dumps({"response": text}).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args: Any) -> None:  # noqa: ARG002
            return

    return Handler


@pytest.fixture
def agent() -> Iterator[tuple[_Agent, str]]:
    state = _Agent()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()


def _loopback_factory(base_url: str):
    def factory(agent_endpoint: str, secret: str, agent_id: str | None = None,
                endpoint_config: dict[str, Any] | None = None):
        # The real runner verbatim, re-pointed from the edge-composed https
        # URL at the loopback fixture. ``agent_endpoint`` keeps its path so
        # the fake agent can pick a mode.
        path = "/" + agent_endpoint.split("://", 1)[1].split("/", 1)[1]
        return customer_agent_http_runner_factory(
            base_url + path, secret, agent_id, endpoint_config=endpoint_config
        )

    return factory


# ---------------------------------------------------------------------------
# Runtime on disk.
# ---------------------------------------------------------------------------


def build_runtime(root: Path, base_url: str):
    """Production runtime wiring with every durable store rooted at ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    corpus = build_v0_corpus_tarball(root / "corpus")
    registry: dict[str, dict[str, Any]] = {}
    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus,
        runner_factory=_loopback_factory(base_url),
        probe_run_fn=customer_agent_probe_run_fn,
        score_runtime=ScoreRuntime.from_rubric_path(REPO_ROOT / "rubric" / "rubric.yaml"),
        score_envelope_registry=registry,
        fired_probe_ledger=AuditSpineFiredProbeLedger(root / "fired-probe-audit-spine.jsonl"),
        production_control_ledger=production_controls.ProductionControlLedger(
            root / "production-control-ledger"
        ),
        context_envelope_store=ContextEnvelopeStore(str(root / "context_envelopes.sqlite3")),
    )
    runtime._scan_log = ScanLog(str(root / "scan_log.sqlite3"))
    runtime._score_envelope_factory = _production_score_envelope_factory([runtime], registry)
    runtime._full_scan_job_store = FullScanJobStore(
        persistence=FullScanJobPersistence(root / "full_scan_jobs.sqlite3")
    )
    return runtime


class _Outbox:
    def __init__(self) -> None:
        self.posted: list[bytes] = []
        self.store = InMemoryOutboxStore()

        def poster(url: str, headers: Any, body: bytes) -> None:  # noqa: ARG001
            self.posted.append(bytes(body))

        self.outbox = WebhookOutbox(
            http_poster=poster,
            secret_resolver=lambda ref: b"webhook-signing-key-38865",  # noqa: ARG005
            store=self.store,
        )

    def blobs(self) -> list[str]:
        out = [b.decode("utf-8", "replace") for b in self.posted]
        out += [repr(r) for r in self.store._records.values()]
        return out


def build_app(root: Path, base_url: str):
    runtime = build_runtime(root, base_url)
    outbox = _Outbox()
    app = create_app(
        scan_runtime=runtime,
        bundles=BundleStore(FileBundleStorage(root / "bundles")),
        webhook_outbox=outbox.outbox,
    )
    return runtime, TestClient(app, raise_server_exceptions=False), outbox


def intake_payload(secret: str, *, mode: str = "ok", auth: str = "bearer") -> dict[str, Any]:
    """Mirror /opt/backbond/qa-review/server/qa_service.py::scanner_payload."""
    endpoint_auth: dict[str, Any] = {"type": "custom_header" if auth == "header" else auth}
    if auth == "header":
        endpoint_auth["header_name"] = "X-Agent-Key"
    return {
        "tenant_id": TENANT_ID,
        "agent_id": AGENT_ID,
        "agent_endpoint": {
            "url": AGENT_URL.rsplit("/", 1)[0] + "/" + mode,
            "transport": "rest",
            "auth": endpoint_auth,
        },
        "credential_ref": "qa-" + AGENT_ID,
        "credential_secret": secret,
        "scanner_version": "partner-qa-v1",
        "scan_mode": "live_probe",
        "scan_tier": "smoke",
        "pack_id": STARTER_PACK_ID,
        "include_result_rows": True,
        "seed": 42,
        "webhook": {"url": "https://intake.example.test/hook", "secret_ref": "qa-intake"},
    }


# ---------------------------------------------------------------------------
# Leak assertions.
# ---------------------------------------------------------------------------


def _vault_holders(secret: str) -> list[str]:
    return [
        token
        for token, bucket in credentials._VAULT_STORE.items()
        if any(secret in value for value in bucket.values())
    ]


def _files_containing(root: Path, secret: str) -> list[str]:
    needle = secret.encode()
    hits = []
    for path in root.rglob("*"):
        if path.is_file():
            try:
                if needle in path.read_bytes():
                    hits.append(str(path.relative_to(root)))
            except OSError:
                pass
    return sorted(hits)


def assert_secret_gone(
    secret: str,
    root: Path,
    *,
    caplog: pytest.LogCaptureFixture | None = None,
    capfd: pytest.CaptureFixture[str] | None = None,
    blobs: list[str] = (),
) -> None:
    assert _vault_holders(secret) == [], "credential vault still holds the secret"
    assert _files_containing(root, secret) == [], "secret bytes persisted on disk"
    if caplog is not None:
        logged = [
            r.name for r in caplog.records
            if secret in r.getMessage() or (r.exc_text and secret in r.exc_text)
            or (r.exc_info and secret in logging.Formatter().formatException(r.exc_info))
        ]
        assert logged == [], f"secret in log records from {logged}"
    if capfd is not None:
        out, err = capfd.readouterr()
        assert secret not in out, "secret on stdout"
        assert secret not in err, "secret on stderr"
    leaked = [i for i, blob in enumerate(blobs) if secret in blob]
    assert leaked == [], f"secret in response/outbox/result blobs {leaked}"


def _runtime_blobs(runtime: Any) -> list[str]:
    return [repr(v) for v in runtime._results.values()] + [
        repr(v) for v in runtime._scan_requests.values()
    ]


# ---------------------------------------------------------------------------
# (a) completed scan.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("auth", ["bearer", "header"])
@pytest.mark.parametrize("mode", ["ok", "echo"])
def test_completed_scan_drops_credential(
    tmp_path: Path, agent, caplog, capfd, auth: str, mode: str
) -> None:
    caplog.set_level(logging.DEBUG)
    state, base_url = agent
    secret = _canary()
    runtime, client, outbox = build_app(tmp_path / "data", base_url)

    response = client.post(
        "/scanner/v1/scans",
        headers={"X-Tenant-Id": TENANT_ID},
        json=intake_payload(secret, mode=mode, auth=auth),
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] in {"complete", "partial"}
    assert len(state.seen_auth) == 100
    assert all(secret in value for value in state.seen_auth)
    assert outbox.posted, "terminal webhook was not delivered"
    assert_secret_gone(
        secret, tmp_path, caplog=caplog, capfd=capfd,
        blobs=[response.text, *outbox.blobs(), *_runtime_blobs(runtime)],
    )


# ---------------------------------------------------------------------------
# (b) failed scan: the agent rejects the credential and echoes it back; and
# a credential the HTTP stack refuses to send (pasted with a trailing newline)
# which makes the transport error carry the header value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["agent_401_echo", "illegal_header_value"])
def test_failed_scan_drops_credential(
    tmp_path: Path, agent, caplog, capfd, variant: str
) -> None:
    caplog.set_level(logging.DEBUG)
    state, base_url = agent
    canary = _canary()
    if variant == "agent_401_echo":
        secret, mode = canary, "fail401"
    else:
        secret, mode = canary + "\n", "ok"
    runtime, client, outbox = build_app(tmp_path / "data", base_url)

    response = client.post(
        "/scanner/v1/scans",
        headers={"X-Tenant-Id": TENANT_ID},
        json=intake_payload(secret, mode=mode),
    )

    assert response.status_code >= 400, response.text
    if variant == "agent_401_echo":
        assert state.seen_auth and canary in state.seen_auth[0]

    # The same request through the handler directly, so the raised exception
    # chain (what any caller or log formatter would see) can be inspected.
    with pytest.raises(HandlerError) as raised:
        scan_handler(intake_payload(secret, mode=mode), tenant_header=TENANT_ID, runtime=runtime)
    chain: list[str] = []
    exc: BaseException | None = raised.value
    while exc is not None:
        chain.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    assert_secret_gone(
        canary, tmp_path, caplog=caplog, capfd=capfd,
        blobs=[response.text, *outbox.blobs(), *_runtime_blobs(runtime), *chain],
    )


def test_runtime_raising_mid_scan_drops_credential(
    tmp_path: Path, agent, caplog, capfd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The score-envelope step raises after the Prober finished."""
    caplog.set_level(logging.DEBUG)
    state, base_url = agent
    secret = _canary()
    runtime, client, outbox = build_app(tmp_path / "data", base_url)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("envelope projection exploded")

    runtime._score_envelope_factory = boom
    response = client.post(
        "/scanner/v1/scans",
        headers={"X-Tenant-Id": TENANT_ID},
        json=intake_payload(secret),
    )

    assert response.status_code == 500
    assert state.seen_auth and secret in state.seen_auth[0]
    assert_secret_gone(
        secret, tmp_path, caplog=caplog, capfd=capfd,
        blobs=[response.text, *outbox.blobs(), *_runtime_blobs(runtime)],
    )


# ---------------------------------------------------------------------------
# (c) cancelled scan. The scanner has no cancel route; a scan in flight ends
# early only when the parent gives up on the Prober (result-queue timeout)
# and terminates it, which is what this drives.
# ---------------------------------------------------------------------------


def test_cancelled_scan_drops_credential(
    tmp_path: Path, agent, caplog, capfd, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)
    state, base_url = agent
    secret = _canary()
    runtime, client, outbox = build_app(tmp_path / "data", base_url)
    monkeypatch.setattr(scan_runtime_factory, "_orchestrator_join_timeout", lambda slack: 3)

    response = client.post(
        "/scanner/v1/scans",
        headers={"X-Tenant-Id": TENANT_ID},
        json=intake_payload(secret, mode="hang"),
    )
    state.release.set()

    assert response.status_code >= 500, response.text
    assert state.first_request.is_set() and secret in state.seen_auth[0]
    assert_secret_gone(
        secret, tmp_path, caplog=caplog, capfd=capfd,
        blobs=[response.text, *outbox.blobs(), *_runtime_blobs(runtime)],
    )


# ---------------------------------------------------------------------------
# (d) connectivity check.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["ok", "fail401", "illegal_header_value"])
def test_connectivity_check_drops_credential(
    tmp_path: Path, agent, caplog, capfd, mode: str
) -> None:
    caplog.set_level(logging.DEBUG)
    state, base_url = agent
    canary = _canary()
    secret = canary + "\n" if mode == "illegal_header_value" else canary
    body = intake_payload(secret, mode="ok" if mode == "illegal_header_value" else mode)
    status, payload = connectivity_check_handler(
        body, runner_factory=_loopback_factory(base_url)
    )

    assert status == 200
    assert payload["success"] is (mode == "ok")
    assert_secret_gone(
        canary, tmp_path, caplog=caplog, capfd=capfd, blobs=[json.dumps(payload)]
    )


# ---------------------------------------------------------------------------
# (e) crash: kill -9 the scanner process group mid-scan, then restart on the
# same data dirs.
# ---------------------------------------------------------------------------

_CHILD = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["REPO_ROOT"])
import tests.test_beaaa38865_credential_lifecycle as t
t._allow_loopback()
root = Path(os.environ["DATA_ROOT"])
secret = sys.stdin.readline().rstrip("\n")
runtime = t.build_runtime(root, os.environ["AGENT_BASE"])
print("ready", flush=True)
from api.scan_handlers import scan_handler
scan_handler(t.intake_payload(secret, mode="hang"), tenant_header=t.TENANT_ID, runtime=runtime)
print("finished", flush=True)
"""


def test_sigkill_mid_scan_then_restart_leaves_no_credential(
    tmp_path: Path, agent, caplog
) -> None:
    state, base_url = agent
    secret = _canary()
    data_root = tmp_path / "data"
    env = {
        **os.environ,
        "REPO_ROOT": str(REPO_ROOT),
        "DATA_ROOT": str(data_root),
        "AGENT_BASE": base_url,
        "TMPDIR": str(tmp_path / "tmp"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write((secret + "\n").encode())
        proc.stdin.close()
        assert state.first_request.wait(120), proc.stderr.read().decode()[-2000:] if proc.poll() is not None else "no request"
        assert secret in state.seen_auth[0]
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(30)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        state.release.set()
    out = proc.stdout.read().decode() + proc.stderr.read().decode()
    assert "finished" not in out
    assert secret not in out

    # Restart on the same data dirs: production wiring boots, recovers the job
    # store and drains the outbox.
    caplog.set_level(logging.DEBUG)
    runtime, client, outbox = build_app(data_root, base_url)
    assert client.get("/version").status_code == 200
    assert_secret_gone(
        secret, tmp_path, caplog=caplog,
        blobs=[*outbox.blobs(), *_runtime_blobs(runtime)],
    )


# ---------------------------------------------------------------------------
# (f) full-tier registered-case evidence: per-case evidence JSON is written to
# disk even when the target call fails.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["agent_401_echo", "illegal_header_value"])
def test_full_tier_case_evidence_drops_credential(
    tmp_path: Path, agent, caplog, variant: str
) -> None:
    caplog.set_level(logging.DEBUG)
    state, base_url = agent
    canary = _canary()
    secret = canary + "\n" if variant == "illegal_header_value" else canary
    mode = "ok" if variant == "illegal_header_value" else "fail401"
    subject = customer_agent_http_runner_factory(f"{base_url}/{mode}", secret, None)
    executor = RegisteredCaseExecutor(
        subject=subject,
        judge=MockRunner("judge", lambda _p: "ANSWER_MATCH: NO"),
        evidence_store=EvidenceStore(tmp_path / "evidence"),
        budget=ScanBudget(max_estimated_judge_cost_usd=100.0),
    )
    row = {
        "test_id": "p1_1_factuality_golden",
        "case_id": "P1.1-FAC-001",
        "status": "active",
        "pillar": "OPS",
        "category": "factuality",
        "risk_or_rubric_id": "P1.1",
        "source_pack": "rubric/probe_mappings/p1_1_factuality_golden.yaml",
        "runner": "bench_wrapper",
        "version_pin": "p1_1_factuality_golden@0.1.0",
        "production_runner_executable": True,
    }
    result = executor(row, SimpleNamespace(job_id="job-38865", agent_id="agent-1", seed=42))

    assert result.status == "failed"
    assert_secret_gone(
        canary, tmp_path, caplog=caplog,
        blobs=[repr(result)],
    )
