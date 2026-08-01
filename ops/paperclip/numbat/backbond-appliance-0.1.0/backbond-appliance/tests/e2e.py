"""End-to-end appliance gate (the Enso-appliance equivalent of the voice E2E).

Runs: synthetic Enso events (with content+PII) -> appliance run -> then asserts:
  1. NO-CONTENT INVARIANT   no content field/value in ANY emitted batch (headline gate)
  2. SIGNED + CHAINED       every batch verifies; prev_batch_hash links correctly
  3. IDEMPOTENT             a second run over the same input emits no duplicate events
  4. TAMPER-REJECTED        mutating a shipped batch fails verification
  5. DETERMINATIONS PRESENT the expected perils fired (so we know it's not empty-passing)
  6. TENANCY                every emitted event/determination carries a tenant_id

Exit 0 = green.
"""
from __future__ import annotations
import json, os, sys, glob, shutil, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from appliance.config import load_config
from appliance.batch import verify_batch, Signer
from appliance import main as appliance_main
from synthetic.gen_enso_events import main as gen_events

CONTENT_NEEDLES = ["SECRET", "victim@example.com", "078-05-1120", "555-12-3456",
                   "Risk-free", "I'm human", "Buy now", "Weekly roundup", "ssn"]
CONTENT_FIELDS = ["text", "body", "recipient", "customer_data", "email", "consent",
                  "has_unsubscribe", "record_count", "amount", "channel_authorized"]


def fail(msg):
    print(f"  FAIL: {msg}"); sys.exit(1)


def main():
    work = tempfile.mkdtemp(prefix="bb-e2e-")
    try:
        events_dir = os.path.join(work, "events")
        gen_events(events_dir)

        cfg_path = os.path.join(work, "config.yaml")
        cfg = {
            "appliance_id": "e2e-appliance",
            "reader": {"name": "enso_engine", "path": events_dir},
            "rules": {"pack": "growth_agent_pack.yaml"},
            "signing": {"key_path": os.path.join(work, "key.pem"), "key_id": "e2e-key"},
            "state_dir": os.path.join(work, "state"),
            "ship": {"mode": "outbox", "outbox_dir": os.path.join(work, "outbox")},
        }
        import yaml
        with open(cfg_path, "w") as fh:
            yaml.safe_dump(cfg, fh)

        print("[1] first run")
        rc = appliance_main.main(["run", "-c", cfg_path])
        if rc != 0:
            fail(f"run exited {rc}")

        batches = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(work, "outbox", "*.json")))]
        if not batches:
            fail("no batches emitted")

        pub = Signer(os.path.join(work, "key.pem"), "e2e-key").public_pem()

        # 1. no-content invariant
        allblob = json.dumps(batches)
        for n in CONTENT_NEEDLES:
            if n in allblob:
                fail(f"[no-content] content value '{n}' found in emitted batches")
        for b in batches:
            for e in b["events"]:
                for f in CONTENT_FIELDS:
                    if f in e:
                        fail(f"[no-content] content field '{f}' in emitted event")
        print("  [1] no-content invariant: PASS")

        # 2. signed + chained
        prev = None
        for b in batches:
            if not verify_batch(b, pub):
                fail(f"[sign] batch {b['batch_seq']} failed verification")
            if b["prev_batch_hash"] != prev:
                fail(f"[chain] batch {b['batch_seq']} prev_batch_hash mismatch")
            prev = b["batch_hash"]
        print(f"  [2] signed + chained: PASS ({len(batches)} batch(es))")

        # 5. determinations present (checked before idempotency re-run)
        dets = [d for b in batches for d in b["determinations"]]
        fired = {d["rule_id"] for d in dets}
        expected = {"growth.brand_safety", "growth.unauthorized_spend",
                    "growth.outreach_compliance", "growth.crm_data_misuse",
                    "growth.impersonation"}
        missing = expected - fired
        if missing:
            fail(f"[determinations] expected perils did not fire: {missing}")
        print(f"  [5] determinations present: PASS ({len(dets)} findings, rules: {sorted(fired)})")

        # 6. tenancy
        for b in batches:
            for e in b["events"]:
                if not e.get("tenant_id") or e["tenant_id"] == "unknown":
                    fail("[tenancy] emitted event missing tenant_id")
            for d in b["determinations"]:
                if not d.get("tenant_id"):
                    fail("[tenancy] determination missing tenant_id")
        print("  [6] tenancy attribution: PASS")

        # 3. idempotency — second run should emit no new events
        print("[3] second run (idempotency)")
        appliance_main.main(["run", "-c", cfg_path])
        batches2 = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(work, "outbox", "*.json")))]
        new_events = sum(len(b["events"]) for b in batches2) - sum(len(b["events"]) for b in batches)
        if new_events != 0:
            fail(f"[idempotency] second run emitted {new_events} duplicate events")
        print("  [3] idempotency: PASS (no duplicate events on re-run)")

        # 4. tamper rejection
        tampered = json.loads(json.dumps(batches[0]))
        tampered["counts"]["events"] = 999
        if verify_batch(tampered, pub):
            fail("[tamper] mutated batch passed verification")
        print("  [4] tamper rejection: PASS")

        print("\nE2E: GREEN — appliance reads, analyzes locally, and ships signed no-content batches.")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
