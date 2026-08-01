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
import argparse, json, os, sqlite3, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from appliance.batch import verify_batch
from appliance.pipeline import CONTENT_FIELDS
from server.tenant_store import Scope, TenantStore


class Ledger:
    def __init__(self, path: str, tenant_root: str | None = None):
        self.path = path
        self.lock = threading.RLock()
        self.keys: dict[str, str] = {}            # appliance_id -> public_key_pem
        self.chain_tip: dict[str, str] = {}       # appliance_id -> last accepted batch_hash
        self.seen: set[str] = set()               # batch_hash idempotency
        self.accepted = 0
        self.rejected: list[dict] = []
        self.findings: list[dict] = []
        self.bindings: dict[str, dict] = {}
        self.ingest_tokens: dict[str, str] = {}
        self.hub_tokens: dict[str, Scope] = {}
        self.store = TenantStore(tenant_root or path + ".tenants")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def enroll(self, rec: dict):
        scopes = rec.get("scopes") or []
        scope_keys: dict[tuple[str, str], str] = {}
        for item in scopes:
            scope = (item.get("customer_id"), item.get("agent_id"))
            key_id = item.get("encryption_key_id")
            if not all(scope) or not key_id:
                raise ValueError("each server-side scope requires an encryption_key_id")
            if scope in scope_keys and scope_keys[scope] != key_id:
                raise ValueError("mismatched encryption key binding for tenant scope")
            scope_keys[scope] = key_id
        allowed = frozenset(scope_keys)
        if not allowed or not rec.get("ingest_token"):
            raise ValueError("server-side scopes and ingest_token required")
        with self.lock:
            self.keys[rec["appliance_id"]] = rec["public_key_pem"]
            self.bindings[rec["appliance_id"]] = {
                "scopes": allowed,
                "encryption_key_ids": scope_keys}
            self.ingest_tokens[rec["appliance_id"]] = rec["ingest_token"]
            for item in rec.get("hub_tokens", []):
                scope = Scope(item.get("customer_id", ""), item.get("agent_id", ""))
                if (scope.customer_id, scope.agent_id) not in allowed or not item.get("token"):
                    raise ValueError("hub token outside enrolled scope")
                self.hub_tokens[item["token"]] = scope

    def _reject(self, reason: str, ctx: dict):
        self.rejected.append({"reason": reason, **ctx})
        return reason

    def ingest(self, batch: dict, token: str | None = None) -> tuple[bool, str]:
        with self.lock:
            aid = batch.get("appliance_id")
            bh = batch.get("batch_hash")
            ctx = {"appliance_id": aid, "batch_seq": batch.get("batch_seq"), "batch_hash": bh}

            if aid not in self.keys:
                return False, self._reject("unknown appliance (not enrolled)", ctx)
            binding = self.bindings[aid]
            scopes = {(e.get("tenant_id"), e.get("agent_id")) for e in batch.get("events", [])}
            scopes.update((d.get("tenant_id"), d.get("agent_id")) for d in batch.get("determinations", []))
            expected = binding["scopes"]
            if not scopes or not scopes.issubset(expected):
                return False, self._reject("tenant identity outside enrolled scope", ctx)
            if token is None or token != self.ingest_tokens.get(aid):
                return False, self._reject("token not authorized for tenant scope", ctx)

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

            pending_findings = [{k: d.get(k) for k in
                                 ("rule_id", "severity", "tenant_id", "agent_id", "event_id", "evidence_hash")}
                                for d in batch.get("determinations", [])]
            writes = []
            for customer_id, agent_id in scopes:
                tenant_findings = [d for d in batch.get("determinations", [])
                                   if d.get("tenant_id") == customer_id and d.get("agent_id") == agent_id]
                key_id = binding["encryption_key_ids"].get((customer_id, agent_id))
                writes.append((Scope(customer_id, agent_id), tenant_findings, key_id))

            # Validate every tenant before creating partitions or mutating acceptance state.
            try:
                for scope, tenant_findings, key_id in writes:
                    self.store.validate_append(scope, tenant_findings, key_id)
                for scope, tenant_findings, key_id in writes:
                    self.store.append(scope, bh, tenant_findings, key_id)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(batch) + "\n")
            except (OSError, sqlite3.Error, ValueError) as exc:
                return False, self._reject(f"persistence rejected: {exc}", ctx)

            self.seen.add(bh)
            self.chain_tip[aid] = bh
            self.accepted += 1
            self.findings.extend(pending_findings)
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

    def hub_summary(self, token: str, customer_id: str, agent_id: str) -> dict:
        scope = Scope(customer_id, agent_id)
        if self.hub_tokens.get(token) != scope:
            self.store.audit("hub_denied", scope, {})
            raise PermissionError("not authorized for tenant scope")
        return {"customer_id": customer_id, "agent_id": agent_id, **self.store.summary(scope)}

    def delete_tenant(self, token: str, customer_id: str, agent_id: str) -> None:
        scope = Scope(customer_id, agent_id)
        if self.hub_tokens.get(token) != scope:
            self.store.audit("delete_denied", scope, {})
            raise PermissionError("not authorized for tenant scope")
        # Deletion is a data-erasure control, not merely a partition cleanup.  The
        # server ledger historically retained full signed batches, so reconcile all
        # reporting and persistence surfaces while holding the ingest lock.  A batch
        # that touched the deleted scope is reduced to a non-attributable receipt;
        # the original hash remains for idempotency/audit continuity, but no tenant
        # identifier, rule, severity, event id, or evidence hash survives.
        with self.lock:
            self.store.delete(scope)
            self.findings = [
                finding for finding in self.findings
                if (finding.get("tenant_id"), finding.get("agent_id"))
                != (customer_id, agent_id)
            ]
            self._erase_tenant_from_ledger(scope)

    def _erase_tenant_from_ledger(self, scope: Scope) -> None:
        if not os.path.exists(self.path):
            return
        replacement = self.path + ".rewrite"
        try:
            with open(self.path, encoding="utf-8") as source, \
                    open(replacement, "w", encoding="utf-8") as target:
                for line in source:
                    record = json.loads(line)
                    scoped_rows = record.get("events", []) + record.get("determinations", [])
                    touched = any(
                        row.get("tenant_id") == scope.customer_id
                        and row.get("agent_id") == scope.agent_id
                        for row in scoped_rows
                    )
                    if touched:
                        retained_events = [
                            row for row in record.get("events", [])
                            if (row.get("tenant_id"), row.get("agent_id"))
                            != (scope.customer_id, scope.agent_id)
                        ]
                        retained_determinations = [
                            row for row in record.get("determinations", [])
                            if (row.get("tenant_id"), row.get("agent_id"))
                            != (scope.customer_id, scope.agent_id)
                        ]
                        record = {
                            "record_type": "tenant_erased_batch_receipt",
                            "batch_hash": record.get("batch_hash"),
                            "appliance_id": record.get("appliance_id"),
                            "batch_seq": record.get("batch_seq"),
                            "events": retained_events,
                            "determinations": retained_determinations,
                        }
                    target.write(json.dumps(record, sort_keys=True) + "\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(replacement, self.path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                os.remove(replacement)
            except FileNotFoundError:
                pass
            raise

    def contain(self, appliance_id: str) -> None:
        """Incident kill switch: reject further writes without deleting evidence."""
        with self.lock:
            self.ingest_tokens.pop(appliance_id, None)
            binding = self.bindings.get(appliance_id, {})
            for scope in binding.get("scopes", []):
                self.store.audit("appliance_contained", Scope(*scope), {"appliance_id": appliance_id})


def make_handler(ledger: Ledger, token: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bearer(self):
            value = self.headers.get("Authorization", "")
            return value[7:] if value.startswith("Bearer ") else ""

        def _read_json(self):
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n) or b"{}"
            if self.headers.get("Content-Encoding", "") == "gzip":
                import gzip
                data = gzip.decompress(data)
            return json.loads(data)

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            if parsed.path == "/hub/summary":
                q = parse_qs(parsed.query)
                try:
                    out = ledger.hub_summary(self._bearer(), q.get("customer_id", [""])[0],
                                             q.get("agent_id", [""])[0])
                    self._send(200, out)
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
            elif self.path == "/status" and self._bearer() == token:
                self._send(200, ledger.status())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/enroll":
                if self._bearer() != token:
                    self._send(401, {"error": "admin authorization required"}); return
                try:
                    ledger.enroll(self._read_json())
                    self._send(200, {"ok": True})
                except ValueError as exc:
                    self._send(422, {"error": str(exc)})
                return
            if self.path == "/ingest":
                bearer = self._bearer()
                if not bearer:
                    self._send(401, {"error": "missing token"}); return
                ok, msg = ledger.ingest(self._read_json(), bearer)
                self._send(200 if ok else 422, {"accepted": ok, "detail": msg})
                return
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            if parsed.path != "/hub/tenant":
                self._send(404, {"error": "not found"}); return
            q = parse_qs(parsed.query)
            try:
                ledger.delete_tenant(self._bearer(), q.get("customer_id", [""])[0],
                                     q.get("agent_id", [""])[0])
                self._send(200, {"deleted": True})
            except PermissionError as exc:
                self._send(403, {"error": str(exc)})

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
