"""BackBond-side test ingest server — the receiving end of the appliance's HTTP shipper.

Runs on YOUR machine so you can exercise the full loop end to end:
    appliance (run, ship.mode=http)  ->  network  ->  this server (verify + accept)

On each batch it INDEPENDENTLY enforces everything BackBond must not take on trust:
  - bearer-token auth
  - Ed25519 signature valid (against the appliance's enrolled public key)
  - batch_hash recomputed from the canonical body
  - hash-chain continuity (prev_batch_hash == last accepted batch_hash for that appliance)
  - idempotency (a re-shipped batch_hash is a no-op)
  - NO-CONTENT re-check (defense in depth: reject if any content field appears, even
    though the appliance already stripped it — a compromised/old appliance can't sneak
    content past the server)

Accepted batches append to an NDJSON ledger; GET /status shows a live summary.
Stdlib only — no dependencies beyond `cryptography` (already required by the appliance).

Usage:
    BACKBOND_INGEST_TOKEN=secret python3 -m server.ingest_server --port 8787
    # then in the appliance config: ship.mode: http, url: http://127.0.0.1:8787/ingest
    # and run:  BACKBOND_SHIP_TOKEN=secret python3 -m appliance.main run -c config.yaml
"""
from __future__ import annotations
import argparse, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from appliance.batch import verify_batch
from appliance.pipeline import CONTENT_FIELDS


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.keys: dict[str, str] = {}            # appliance_id -> public_key_pem
        self.chain_tip: dict[str, str] = {}       # appliance_id -> last accepted batch_hash
        self.seen: set[str] = set()               # batch_hash idempotency
        self.accepted = 0
        self.rejected: list[dict] = []
        self.findings: list[dict] = []
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def enroll(self, rec: dict):
        with self.lock:
            self.keys[rec["appliance_id"]] = rec["public_key_pem"]

    def _reject(self, reason: str, ctx: dict):
        self.rejected.append({"reason": reason, **ctx})
        return reason

    def ingest(self, batch: dict) -> tuple[bool, str]:
        with self.lock:
            aid = batch.get("appliance_id")
            bh = batch.get("batch_hash")
            ctx = {"appliance_id": aid, "batch_seq": batch.get("batch_seq"), "batch_hash": bh}

            if aid not in self.keys:
                return False, self._reject("unknown appliance (not enrolled)", ctx)

            # signature + batch_hash FIRST — a tampered body must never be treated as a
            # duplicate. verify_batch recomputes batch_hash from the canonical body, so any
            # mutation (even one that reuses the claimed batch_hash) fails here.
            if not verify_batch(batch, self.keys[aid]):
                return False, self._reject("signature/batch_hash invalid", ctx)

            # idempotency (only after the batch is proven authentic)
            if bh in self.seen:
                return True, "duplicate (idempotent no-op)"

            # hash-chain continuity
            expected_prev = self.chain_tip.get(aid)  # None for first batch
            if batch.get("prev_batch_hash") != expected_prev:
                return False, self._reject(
                    f"broken chain (prev_batch_hash={batch.get('prev_batch_hash')} expected={expected_prev})", ctx)

            # independent no-content re-check (defense in depth)
            leaked = self._scan_content(batch)
            if leaked:
                return False, self._reject(f"content boundary violated: {leaked}", ctx)

            # accept
            self.seen.add(bh)
            self.chain_tip[aid] = bh
            self.accepted += 1
            for d in batch.get("determinations", []):
                self.findings.append({k: d.get(k) for k in
                                      ("rule_id", "severity", "tenant_id", "agent_id", "event_id", "evidence_hash")})
            with open(self.path, "a") as fh:
                fh.write(json.dumps(batch) + "\n")
            return True, f"accepted (seq {batch.get('batch_seq')})"

    def _scan_content(self, batch: dict):
        # field-name check on every emitted event
        for e in batch.get("events", []):
            for f in CONTENT_FIELDS:
                if f in e:
                    return f"content field '{f}' present in event"
        return None

    def status(self) -> dict:
        with self.lock:
            by_rule, by_tenant, by_sev = {}, {}, {}
            for f in self.findings:
                by_rule[f["rule_id"]] = by_rule.get(f["rule_id"], 0) + 1
                by_tenant[f["tenant_id"]] = by_tenant.get(f["tenant_id"], 0) + 1
                by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
            return {
                "appliances_enrolled": sorted(self.keys),
                "batches_accepted": self.accepted,
                "batches_rejected": len(self.rejected),
                "rejections": self.rejected[-10:],
                "findings_total": len(self.findings),
                "findings_by_rule": by_rule,
                "findings_by_tenant": by_tenant,
                "findings_by_severity": by_sev,
            }


def make_handler(ledger: Ledger, token: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth_ok(self):
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def _read_json(self):
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n) or b"{}"
            if self.headers.get("Content-Encoding", "") == "gzip":
                import gzip
                data = gzip.decompress(data)
            return json.loads(data)

        def do_GET(self):
            if self.path == "/status":
                self._send(200, ledger.status())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/enroll":
                ledger.enroll(self._read_json())
                self._send(200, {"ok": True})
                return
            if self.path == "/ingest":
                if not self._auth_ok():
                    self._send(401, {"error": "bad token"}); return
                ok, msg = ledger.ingest(self._read_json())
                self._send(200 if ok else 422, {"accepted": ok, "detail": msg})
                return
            self._send(404, {"error": "not found"})

        def log_message(self, *a):  # quiet
            pass
    return Handler


def serve(port: int, ledger: Ledger, token: str) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ledger, token))
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backbond-ingest-server")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--ledger", default=os.path.expanduser("~/.backbond/server/ledger.ndjson"))
    args = ap.parse_args(argv)
    token = os.environ.get("BACKBOND_INGEST_TOKEN", "")
    if not token:
        print("WARNING: BACKBOND_INGEST_TOKEN unset — accepting any token (dev only)", file=sys.stderr)
    ledger = Ledger(args.ledger)
    httpd = serve(args.port, ledger, token)
    print(f"BackBond test ingest listening on http://127.0.0.1:{args.port}  (POST /enroll, POST /ingest, GET /status)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down"); httpd.shutdown()


if __name__ == "__main__":
    main()
