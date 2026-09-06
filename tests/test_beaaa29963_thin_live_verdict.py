import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from scripts.run_thin_live_verdict import main

def test_multiturn_family_executes_over_controlled_http(monkeypatch, tmp_path):
    seen = []
    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            body = json.dumps({"choices": [{"message": {"content": "I cannot help with that request."}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr("sys.argv", ["run", "--endpoint", f"http://127.0.0.1:{server.server_port}", "--evidence-dir", str(tmp_path), "--matrix", str(tmp_path / "matrix.md")])
    try: main()
    finally: server.shutdown(); thread.join()
    evidence = json.loads((tmp_path / "p3_5_multiturn_jailbreak-seed-0.json").read_text())
    assert evidence["verdict"] == "PASS"
    assert evidence["executed_case_count"] == 1
    assert len(seen) >= 2
    assert len(seen[-1]["messages"]) > len(seen[0]["messages"])
    assert "multi-turn / long-horizon" in (tmp_path / "matrix.md").read_text()
