#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portal_v1"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from are_connector.runtime import ConnectorRuntime
from portal import PortalHandler, build_service


class MockAgentHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = {
            "response": "Mock staging response: request refused unless policy allows it.",
            "assertions": {
                "response_received": True,
                "schema_match": "pass",
                "refusal_signal": True,
                "hallucination_signal": None,
                "tool_called": None,
            },
            "request_len": len(body),
        }
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def run_flow(seed: int) -> dict:
    data_dir = Path(tempfile.mkdtemp(prefix=f"beaaa4721-smoke-{seed}-"))
    try:
        service = build_service(data_dir)
        PortalHandler.service = service
        portal = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
        agent = ThreadingHTTPServer(("127.0.0.1", 0), MockAgentHandler)
        serve(portal)
        serve(agent)
        portal_url = f"http://127.0.0.1:{portal.server_port}"
        agent_url = f"http://127.0.0.1:{agent.server_port}/invoke"

        invite = service.create_invite(f"BEAAA-4721 DP {seed}", f"dp{seed}@example.invalid", "call-center")
        config_path = data_dir / "connector.json"
        connector = ConnectorRuntime(portal_url, agent_url, config_path)
        connector.enroll(invite["invite_token"], f"BEAAA-4721-DP-{seed}", accept_endpoint_url=agent_url)
        dp = service.db.execute("SELECT * FROM design_partners ORDER BY created_at DESC LIMIT 1").fetchone()
        scan = service.trigger_scan(dp["id"])
        posted = connector.run_scan(scan["scan_id"])
        row = service.db.execute("SELECT * FROM scans WHERE id = ?", (scan["scan_id"],)).fetchone()
        score = json.loads(row["score_json"])
        envelopes = service.db.execute("SELECT COUNT(*) FROM envelopes WHERE scan_id = ?", (scan["scan_id"],)).fetchone()[0]
        return {
            "seed": seed,
            "posted_envelopes": posted,
            "stored_envelopes": envelopes,
            "score": score,
        }
    finally:
        try:
            portal.shutdown()
            portal.server_close()
            agent.shutdown()
            agent.server_close()
            service.close()
        except Exception:
            pass
        shutil.rmtree(data_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run_flow(args.seed)
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"seed": args.seed, "posted_envelopes": result["posted_envelopes"], "out": args.out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
