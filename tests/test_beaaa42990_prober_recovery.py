"""Real forked prober recovery with a journal mutex held by startup."""
import multiprocessing
import threading
import time

from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from api.accepted_scan_store import AcceptedScanStore, request_to_record
from api import scan_runtime_factory as factory
from harness.customer_runner import customer_agent_http_runner_factory
from scanners.live_probe_orchestrator import ScanRequest


def progress_probe(runner):
    runner.case_progress_callback(case_id="local-1", family="local", state="executing")
    runner.complete("local fixture")
    runner.case_progress_callback(case_id="local-1", family="local", state="finished")
    yield "P1.1", {"raw": 3.0, "evidence_uri": "https://evidence.local/local-1",
                    "evidence_sha256": "sha256:" + "a" * 64}


@pytest.mark.parametrize("resumed", [False, True])
def test_real_prober_progress_never_uses_inherited_journal(tmp_path, monkeypatch, resumed):
    from harness import egress
    original_guard = egress._is_forbidden_resolved_ip
    monkeypatch.setattr(egress, "_is_forbidden_resolved_ip",
                        lambda address: False if address == "127.0.0.1" else original_guard(address))
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"response":"fixture response"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    corpus = factory.build_v0_corpus_tarball(tmp_path / "corpus")
    path = tmp_path / "accepted.sqlite3"
    request = ScanRequest(
        scan_id="recovery-local", tenant_id="local", agent_id="fixture",
        agent_endpoint=f"http://127.0.0.1:{server.server_port}/agent",
        credential_ref="fixture-ref", endpoint_config={"auth": {"type": "none"}},
        scanner_version="0.4.2", corpus_version="0.1.0", seed=42,
        k_replicates=5, scan_tier="dev",
        corpus_snapshot_hash=factory.v0_corpus_snapshot_hash(corpus),
    )
    store = AcceptedScanStore(path)
    if resumed:
        store.accept(request, agent_id="fixture", invocation_context=None, webhook_cfg=None)
        store.mark_running(request.scan_id)
        store.close()
        store = AcceptedScanStore(path)
        assert request_to_record(store.get(request.scan_id)["request"]) == request_to_record(request)
    runtime = factory.make_production_scan_runtime(
        corpus_tarball_path=corpus, runner_factory=customer_agent_http_runner_factory,
        probe_run_fn=progress_probe, parent_join_timeout_seconds=1,
    )
    runtime._accepted_scan_store = store
    monkeypatch.setattr(factory, "_orchestrator_join_timeout", lambda _: 4)
    # Startup's journal reader owns this mutex at fork. Release it in the
    # parent once the child starts: an inherited child mutex stays locked.
    forked = multiprocessing.get_context("fork").Event()
    original_target = factory._prober_target

    def target(**kwargs):
        forked.set()
        original_target(**kwargs)

    monkeypatch.setattr(factory, "_prober_target", target)
    original_run = runtime._run_scan_fn
    locked = threading.Event()

    def startup_reader():
        with store._lock:
            locked.set()
            assert forked.wait(6)

    def run(request, secret):
        reader = threading.Thread(target=startup_reader)
        reader.start()
        assert locked.wait(2)
        try:
            return original_run(request, secret)
        finally:
            reader.join(7)

    runtime._run_scan_fn = run
    try:
        if resumed:
            assert runtime.resume_accepted_scans(start_webhook_retry=False)["resumed"] == [request.scan_id]
        else:
            store.accept(request, agent_id="fixture", invocation_context=None, webhook_cfg=None)
            runtime._start_background_scan(runtime._run_accepted_scan_background,
                scan_request=request, secret="fixture-no-auth", agent_id="fixture", on_terminal=None)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = store.get(request.scan_id)
            if row["state"] == "terminal":
                break
            time.sleep(0.02)
        assert row["state"] == "terminal"
        assert row["result"].status == "complete", row["result"]
        assert requests == [None] * 5
        assert store.case_progress(request.scan_id, tenant_id="local")["processed_count"] == 1
    finally:
        server.shutdown()
        server.server_close()
        serving.join(2)
        store.close()
