"""Full-loop test (Rung 3): appliance --HTTP--> BackBond ingest server --> verify.

Proves the complete round trip on your own machine:
  1. server starts; appliance enrolls its public key
  2. appliance ships a real signed batch over HTTP -> server ACCEPTS it
  3. /status shows the findings arrived (by rule / tenant / severity)
  4. a re-ship is idempotent (no double count)
  5. a TAMPERED batch is REJECTED server-side
  6. a CONTENT-LADEN batch is REJECTED server-side (independent no-content enforcement)

Exit 0 = green.
"""
from __future__ import annotations
import copy, json, os, sqlite3, sys, tempfile, shutil, threading, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from appliance.batch import Signer
from appliance import main as appliance_main
from synthetic.gen_enso_events import main as gen_events
from server.ingest_server import Ledger, Scope, serve

TOKEN = "loop-test-token"


def post(url, obj, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def get(url, token=None):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def delete(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def fail(m):
    print(f"  FAIL: {m}"); sys.exit(1)


def main():
    work = tempfile.mkdtemp(prefix="bb-loop-")
    port = 8799
    ledger = Ledger(os.path.join(work, "ledger.ndjson"))
    httpd = serve(port, ledger, TOKEN)
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    base = f"http://127.0.0.1:{port}"
    try:
        time.sleep(0.3)
        events_dir = os.path.join(work, "events"); gen_events(events_dir)
        key_path = os.path.join(work, "key.pem")
        cfg = {
            "appliance_id": "loop-appliance",
            "reader": {"name": "enso_engine", "path": events_dir},
            "rules": {"pack": "growth_agent_pack.yaml"},
            "signing": {"key_path": key_path, "key_id": "loop-key"},
            "state_dir": os.path.join(work, "state"),
            "ship": {"mode": "http", "url": f"{base}/ingest"},
        }
        cfg_path = os.path.join(work, "config.yaml")
        import yaml; yaml.safe_dump(cfg, open(cfg_path, "w"))

        # 1. enroll the appliance public key with the server
        pub = Signer(key_path, "loop-key").public_pem()
        scopes = [{"customer_id": tenant, "agent_id": agent,
                   "encryption_key_id": f"key-{tenant}-{agent}"}
                  for tenant in ("smb-alpha", "smb-beta")
                  for agent in ("community", "newsletter", "ops", "sdr", "social")]
        hub_tokens = [{"customer_id": tenant, "agent_id": agent,
                       "token": f"hub-{tenant}-{agent}"} for tenant, agent in
                      ((s["customer_id"], s["agent_id"]) for s in scopes)]
        post(f"{base}/enroll", {"appliance_id": "loop-appliance", "public_key_pem": pub,
             "ingest_token": TOKEN, "scopes": scopes, "hub_tokens": hub_tokens}, TOKEN)
        print("  [1] enrolled appliance public key")

        code, _ = post(f"{base}/enroll", {"appliance_id": "missing-key", "public_key_pem": pub,
                       "ingest_token": TOKEN,
                       "scopes": [{"customer_id": "smb-alpha", "agent_id": "ops"}]}, TOKEN)
        if code != 422:
            fail(f"missing per-scope encryption key returned {code}, expected 422")
        code, _ = post(f"{base}/enroll", {"appliance_id": "mismatched-key", "public_key_pem": pub,
                       "ingest_token": TOKEN,
                       "scopes": [{"customer_id": "smb-alpha", "agent_id": "ops",
                                   "encryption_key_id": "key-a"},
                                  {"customer_id": "smb-alpha", "agent_id": "ops",
                                   "encryption_key_id": "key-b"}]}, TOKEN)
        if code != 422:
            fail(f"mismatched per-scope encryption key returned {code}, expected 422")
        print("  [1a] missing/mismatched tenant key bindings REJECTED")

        # 2. ship over HTTP
        os.environ["BACKBOND_SHIP_TOKEN"] = TOKEN
        rc = appliance_main.main(["run", "-c", cfg_path])
        if rc != 0:
            fail(f"appliance run exited {rc}")
        st = get(f"{base}/status", TOKEN)
        if st["batches_accepted"] != 1:
            fail(f"expected 1 accepted batch, got {st['batches_accepted']} (rejections: {st['rejections']})")
        print(f"  [2] batch shipped over HTTP and ACCEPTED (findings: {st['findings_total']})")

        # 3. findings visible server-side
        if not st["findings_by_rule"] or "growth.crm_data_misuse" not in st["findings_by_rule"]:
            fail(f"expected findings by rule, got {st['findings_by_rule']}")
        shipped = json.loads(open(os.path.join(work, "ledger.ndjson")).readline())
        print(f"  [3] findings by rule: {st['findings_by_rule']}")
        print(f"      findings by tenant: {st['findings_by_tenant']}")

        # Server-selected keys must remain distinct in their tenant partitions.
        persisted_scopes = {(f["tenant_id"], f["agent_id"]) for f in ledger.findings}
        key_ids_seen = set()
        for tenant, agent in persisted_scopes:
            with sqlite3.connect(ledger.store._path(Scope(tenant, agent))) as db:
                key_ids = {row[0] for row in db.execute("SELECT DISTINCT encryption_key_id FROM findings")}
            expected_key = f"key-{tenant}-{agent}"
            if key_ids != {expected_key}:
                fail(f"tenant partition has wrong key attribution: {key_ids}, expected {expected_key}")
            key_ids_seen.update(key_ids)
        if len(key_ids_seen) < 2:
            fail(f"expected distinct tenant key attribution, got {key_ids_seen}")
        print("  [3c] distinct tenant encryption-key attribution PASS")

        # Hub principals are bound to exactly one (customer, agent) scope.
        alpha = get(f"{base}/hub/summary?customer_id=smb-alpha&agent_id=ops", "hub-smb-alpha-ops")
        if alpha["customer_id"] != "smb-alpha" or alpha["agent_id"] != "ops":
            fail(f"tenant hub returned wrong scope: {alpha}")
        try:
            get(f"{base}/hub/summary?customer_id=smb-beta&agent_id=ops", "hub-smb-alpha-ops")
            fail("cross-tenant hub read was accepted")
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                fail(f"cross-tenant hub read returned {exc.code}, expected 403")
        print("  [3a] tenant-scoped hub read PASS; cross-tenant read REJECTED")

        deleted_finding = next(
            finding for finding in ledger.findings
            if (finding["tenant_id"], finding["agent_id"]) != ("smb-beta", "ops")
        )
        deleted_customer = deleted_finding["tenant_id"]
        deleted_agent = deleted_finding["agent_id"]
        deleted_count = sum(
            (f["tenant_id"], f["agent_id"]) == (deleted_customer, deleted_agent)
            for f in ledger.findings
        )
        code, _ = delete(
            f"{base}/hub/tenant?customer_id={deleted_customer}&agent_id={deleted_agent}",
            "hub-smb-alpha-ops" if (deleted_customer, deleted_agent) != ("smb-alpha", "ops")
            else "hub-smb-beta-ops")
        if code != 403:
            fail(f"cross-tenant deletion returned {code}, expected 403")
        ledger_bytes_before_delete = open(os.path.join(work, "ledger.ndjson"), "rb").read()
        deleted_scope = Scope(deleted_customer, deleted_agent)
        deleted_partition = ledger.store._path(deleted_scope)
        deleted_epoch = ledger.scope_epochs[deleted_scope]
        old_hub_token = f"hub-{deleted_customer}-{deleted_agent}"

        # Inject a tombstone persistence failure before the successful delete.
        # Every operational surface, including query access and retry/quota state,
        # must remain exactly as it was before the attempt.
        state_before_failed_delete = {
            "partition_exists": os.path.exists(deleted_partition),
            "summary": ledger.store.summary(deleted_scope),
            "findings": copy.deepcopy(ledger.findings),
            "bindings": copy.deepcopy(ledger.bindings),
            "hub_tokens": dict(ledger.hub_tokens),
            "scope_chain_tip": dict(ledger.scope_chain_tip),
            "scope_epochs": dict(ledger.scope_epochs),
            "seen": set(ledger.seen),
            "chain_tip": dict(ledger.chain_tip),
            "accepted": ledger.accepted,
            "deleted_total": ledger.deleted_scope_tombstones_total,
            "quota_findings": ledger.store.quota_findings,
            "retention_seconds": ledger.store.retention_seconds,
        }
        os.mkdir(ledger.tombstone_path)
        code, response = delete(
            f"{base}/hub/tenant?customer_id={deleted_customer}&agent_id={deleted_agent}",
            old_hub_token)
        state_after_failed_delete = {
            "partition_exists": os.path.exists(deleted_partition),
            "summary": ledger.store.summary(deleted_scope),
            "findings": copy.deepcopy(ledger.findings),
            "bindings": copy.deepcopy(ledger.bindings),
            "hub_tokens": dict(ledger.hub_tokens),
            "scope_chain_tip": dict(ledger.scope_chain_tip),
            "scope_epochs": dict(ledger.scope_epochs),
            "seen": set(ledger.seen),
            "chain_tip": dict(ledger.chain_tip),
            "accepted": ledger.accepted,
            "deleted_total": ledger.deleted_scope_tombstones_total,
            "quota_findings": ledger.store.quota_findings,
            "retention_seconds": ledger.store.retention_seconds,
        }
        os.rmdir(ledger.tombstone_path)
        if code != 503 or "persistence failed" not in response.get("error", ""):
            fail(f"tombstone persistence failure was not controlled: {code}, {response}")
        if state_after_failed_delete != state_before_failed_delete:
            fail("tombstone persistence failure mutated pre-delete tenant state")
        print("  [3b-failure] tombstone failure preserves all tenant state PASS")

        code, _ = delete(
            f"{base}/hub/tenant?customer_id={deleted_customer}&agent_id={deleted_agent}",
            old_hub_token)
        if code != 200:
            fail(f"same-tenant deletion returned {code}")
        try:
            get(f"{base}/hub/summary?customer_id={deleted_customer}&agent_id={deleted_agent}",
                old_hub_token)
            fail("deleted tenant hub access remained authorized")
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                fail(f"deleted tenant hub returned {exc.code}, expected 403")
        reconciled = ledger.status()
        if reconciled["findings_total"] != st["findings_total"] - deleted_count:
            fail(f"tenant deletion left contradictory global status: {reconciled}")
        if any((f["tenant_id"], f["agent_id"]) == (deleted_customer, deleted_agent)
               for f in ledger.findings):
            fail("tenant deletion left scoped in-memory findings")
        if os.path.exists(deleted_partition):
            fail("tenant deletion left SQLite partition behind")
        if ledger_bytes_before_delete != open(os.path.join(work, "ledger.ndjson"), "rb").read():
            fail("tenant deletion mutated immutable signed ledger bytes")
        tombstones = [json.loads(line) for line in open(ledger.tombstone_path, encoding="utf-8")]
        if len(tombstones) != 1 or not ledger.verify_deletion_tombstone(tombstones[0]):
            fail(f"expected one valid deletion tombstone, got {tombstones}")
        forbidden = (deleted_customer, deleted_agent, deleted_finding["evidence_hash"])
        if any(value in json.dumps(tombstones[0]) for value in forbidden):
            fail("deletion tombstone retained raw tenant or evidence data")
        if reconciled["ledger_records_total"] != 1 or reconciled["deleted_scope_tombstones_total"] != 1:
            fail(f"audit-only counters are not truthful: {reconciled}")
        print("  [3b] operational erasure + immutable ledger + signed tombstone PASS")

        fresh_token = f"hub-fresh-{deleted_customer}-{deleted_agent}"
        code, _ = post(f"{base}/enroll", {
            "appliance_id": "fresh-appliance", "public_key_pem": pub,
            "ingest_token": "fresh-ingest",
            "scopes": [{"customer_id": deleted_customer, "agent_id": deleted_agent,
                        "encryption_key_id": f"fresh-key-{deleted_customer}-{deleted_agent}"}],
            "hub_tokens": [{"customer_id": deleted_customer, "agent_id": deleted_agent,
                            "token": fresh_token}],
        }, TOKEN)
        if code != 200 or ledger.scope_epochs[deleted_scope] != deleted_epoch + 1:
            fail("tenant re-enrollment did not start a fresh epoch")
        if get(f"{base}/hub/summary?customer_id={deleted_customer}&agent_id={deleted_agent}",
               fresh_token)["findings_total"] != 0:
            fail("tenant re-enrollment restored deleted findings")
        print("  [3d] tenant re-enrollment starts fresh empty epoch PASS")

        # 4. A deleted scope cannot be revived by replaying its retained signed batch.
        code, resp = post(f"{base}/ingest", shipped, TOKEN)
        st2 = get(f"{base}/status", TOKEN)
        if code == 200 or st2["batches_accepted"] != 1:
            fail(f"deleted-scope replay was not rejected cleanly: {code}, {resp}")
        print(f"  [4] deleted-scope signed replay REJECTED ({resp['detail']})")

        # Quota rejection must leave every authoritative state surface unchanged and retryable.
        from appliance.batch import _canonical, _sha256
        signer = Signer(key_path, "loop-key")
        retry_body = {
            "batch_id": "loop-appliance:2", "appliance_id": "loop-appliance", "batch_seq": 2,
            "prev_batch_hash": shipped["batch_hash"], "window": {"start": "x", "end": "y"},
            "events": [{"tenant_id": "smb-beta", "agent_id": "ops", "event_id": "quota-event",
                        "action_class": "tool_call"}],
            "determinations": [{"tenant_id": "smb-beta", "agent_id": "ops", "event_id": "quota-event",
                                "rule_id": "quota.test", "severity": "low", "evidence_hash": "sha256:test"}],
            "counts": {"events": 1}, "producer": {"collector_version": "x"},
        }
        payload = _canonical(retry_body)
        retry_batch = dict(retry_body); retry_batch["batch_hash"] = _sha256(payload)
        retry_batch["signature"] = signer.sign(payload); retry_batch["key_id"] = "loop-key"
        before = (ledger.accepted, set(ledger.seen), dict(ledger.chain_tip), list(ledger.findings))
        target_path = ledger.store._path(Scope("smb-beta", "ops"))
        with sqlite3.connect(target_path) as db:
            existing = db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        ledger.store.quota_findings = existing
        code, resp = post(f"{base}/ingest", retry_batch, TOKEN)
        after = (ledger.accepted, set(ledger.seen), dict(ledger.chain_tip), list(ledger.findings))
        if code == 200 or before != after:
            fail(f"quota rejection was not atomic: code={code}, detail={resp}, before={before}, after={after}")
        ledger.store.quota_findings = 100_000
        code, resp = post(f"{base}/ingest", retry_batch, TOKEN)
        if code != 200 or not resp.get("accepted"):
            fail(f"quota-remediated retry failed: code={code}, response={resp}")
        code, resp = post(f"{base}/ingest", retry_batch, TOKEN)
        if code != 200 or "duplicate" not in resp.get("detail", ""):
            fail(f"accepted batch replay was not idempotent: code={code}, response={resp}")
        print("  [4a] quota rejection atomicity and remediation retry PASS")

        # 5. tampered batch rejected
        tampered = json.loads(json.dumps(retry_batch)); tampered["counts"]["events"] = 999
        code, resp = post(f"{base}/ingest", tampered, TOKEN)
        if code == 200 and resp.get("accepted"):
            fail("tampered batch was accepted")
        if "signature" not in resp.get("detail", ""):
            fail(f"tampered batch did not reach signature rejection: {resp}")
        print(f"  [5] tampered batch REJECTED ({resp['detail']})")

        # 6. VALIDLY-SIGNED content-laden batch rejected (independent server-side no-content
        #    enforcement — proves a compromised/old appliance can't sneak content past the server
        #    even with a good signature and correct chain linkage).
        body = {
            "batch_id": "loop-appliance:3",
            "appliance_id": "loop-appliance", "batch_seq": 3,
            "prev_batch_hash": retry_batch["batch_hash"],   # correct chain linkage
            "window": {"start": "x", "end": "y"},
            "events": [{"tenant_id": "smb-alpha", "agent_id": "ops", "event_id": "poison",
                        "action_class": "message", "text": "SECRET 078-05-1120"}],  # content field!
            "determinations": [], "counts": {"events": 1},
            "producer": {"collector_version": "x"},
        }
        payload = _canonical(body)
        poison = dict(body); poison["batch_hash"] = _sha256(payload)
        poison["signature"] = signer.sign(payload); poison["key_id"] = "loop-key"
        code, resp = post(f"{base}/ingest", poison, TOKEN)
        if code == 200 and resp.get("accepted"):
            fail("validly-signed content-laden batch was accepted")
        if "content" not in resp["detail"]:
            fail(f"expected content-boundary rejection, got: {resp['detail']}")
        print(f"  [6] validly-signed content-laden batch REJECTED ({resp['detail']})")

        # Retention expiry removes stale rows, while containment blocks future writes.
        retained_scope = Scope("smb-beta", "ops")
        with sqlite3.connect(ledger.store._path(retained_scope)) as db:
            db.execute("UPDATE findings SET accepted_at = 0")
        ledger.store.retention_seconds = 1
        if ledger.store.summary(retained_scope)["findings_total"] != 0:
            fail("retention expiry left stale tenant findings")
        state_before_contain = (ledger.accepted, set(ledger.seen), dict(ledger.chain_tip))
        ledger.contain("loop-appliance")
        code, _ = post(f"{base}/ingest", poison, TOKEN)
        if code == 200 or state_before_contain != (ledger.accepted, set(ledger.seen), dict(ledger.chain_tip)):
            fail("containment accepted or mutated a subsequent write")
        print("  [7] retention expiry and containment rejection PASS")

        print("\nLOOP: GREEN — appliance ships over HTTP; server verifies signature, chain, "
              "idempotency, and independently enforces the no-content boundary.")
        return 0
    finally:
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
