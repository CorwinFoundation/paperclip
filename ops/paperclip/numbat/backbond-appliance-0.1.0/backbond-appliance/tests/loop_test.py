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
import json, os, sys, tempfile, shutil, threading, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from appliance.batch import Signer
from appliance import main as appliance_main
from synthetic.gen_enso_events import main as gen_events
from server.ingest_server import Ledger, serve

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
        scopes = [{"customer_id": tenant, "agent_id": agent}
                  for tenant in ("smb-alpha", "smb-beta")
                  for agent in ("community", "newsletter", "ops", "sdr", "social")]
        hub_tokens = [{"customer_id": tenant, "agent_id": agent,
                       "token": f"hub-{tenant}-{agent}"} for tenant, agent in
                      ((s["customer_id"], s["agent_id"]) for s in scopes)]
        post(f"{base}/enroll", {"appliance_id": "loop-appliance", "public_key_pem": pub,
             "ingest_token": TOKEN, "scopes": scopes, "hub_tokens": hub_tokens}, TOKEN)
        print("  [1] enrolled appliance public key")

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
        print(f"  [3] findings by rule: {st['findings_by_rule']}")
        print(f"      findings by tenant: {st['findings_by_tenant']}")

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

        code, _ = delete(f"{base}/hub/tenant?customer_id=smb-beta&agent_id=ops",
                         "hub-smb-alpha-ops")
        if code != 403:
            fail(f"cross-tenant deletion returned {code}, expected 403")
        code, _ = delete(f"{base}/hub/tenant?customer_id=smb-alpha&agent_id=ops",
                         "hub-smb-alpha-ops")
        if code != 200:
            fail(f"same-tenant deletion returned {code}")
        deleted = get(f"{base}/hub/summary?customer_id=smb-alpha&agent_id=ops",
                      "hub-smb-alpha-ops")
        if deleted["findings_total"] != 0:
            fail("tenant deletion left findings behind")
        print("  [3b] scoped retention deletion PASS; cross-tenant deletion REJECTED")

        # 4. idempotent re-ship (resend the persisted batch directly)
        shipped = json.loads(open(os.path.join(work, "ledger.ndjson")).readline())
        code, resp = post(f"{base}/ingest", shipped, TOKEN)
        st2 = get(f"{base}/status", TOKEN)
        if st2["batches_accepted"] != 1:
            fail(f"re-ship not idempotent: accepted went to {st2['batches_accepted']}")
        print(f"  [4] re-ship idempotent ({resp['detail']})")

        # 5. tampered batch rejected
        tampered = json.loads(json.dumps(shipped)); tampered["counts"]["events"] = 999
        code, resp = post(f"{base}/ingest", tampered, TOKEN)
        if code == 200 and resp.get("accepted"):
            fail("tampered batch was accepted")
        print(f"  [5] tampered batch REJECTED ({resp['detail']})")

        # 6. VALIDLY-SIGNED content-laden batch rejected (independent server-side no-content
        #    enforcement — proves a compromised/old appliance can't sneak content past the server
        #    even with a good signature and correct chain linkage).
        from appliance.batch import _canonical, _sha256
        signer = Signer(key_path, "loop-key")
        body = {
            "batch_id": "loop-appliance:2",
            "appliance_id": "loop-appliance", "batch_seq": 2,
            "prev_batch_hash": shipped["batch_hash"],   # correct chain linkage
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

        print("\nLOOP: GREEN — appliance ships over HTTP; server verifies signature, chain, "
              "idempotency, and independently enforces the no-content boundary.")
        return 0
    finally:
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
