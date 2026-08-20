"""Drill producer: the customer-side signer, built from the sealed 0.1.0 tree.

Nothing here is part of the service. It exists so the drill delivers batches a
real appliance would emit -- signed by the sealed BatchBuilder, chained through
the sealed chain.state file -- rather than hand-rolled JSON.
"""
import json, sys, urllib.error, urllib.request
sys.path.insert(0, "/opt/backbond/appliance-0.1.0")
from appliance import pipeline
from appliance.batch import BatchBuilder, Signer

STATE = "/root/drill/state"
KEY = "/root/drill/appliance.key"
PINS = {"collector_version": "appliance-0.1.0", "evidence_profile_version": "1.0",
        "scanner_version": "na-appliance", "corpus_version": "na-appliance",
        "rule_pack_version": "0.1", "seed": 0}
signer = Signer(KEY, "appliance-key-1")


def emit(tag):
    builder = BatchBuilder("a1", signer, STATE, PINS)
    raw = [{"event_id": f"{tag}-{i}", "tenant_id": "tenantA", "agent_id": "growth-agent",
            "ts": "2026-08-19T00:00:00Z", "type": "social_post",
            "content": f"synthetic body {tag}-{i}", "billable": True} for i in range(2)]
    batches = builder.build([pipeline.redact(pipeline.normalize(r)) for r in raw],
                            [], {"start": "x", "end": "y"})
    assert len(batches) == 1
    return batches[0]


def deliver(batch, token="drill-token"):
    request = urllib.request.Request(
        "http://127.0.0.1:8787/ingest", data=json.dumps(batch).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "X-BackBond-Tenant": "tenantA",
                 "X-BackBond-Source": "enso-synthetic", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
    except Exception as exc:                      # connection refused, etc.
        return None, {"client_error": type(exc).__name__, "detail": str(exc)}


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "pubkey":
        print(signer.public_pem())
    elif command == "emit":
        batch = emit(sys.argv[2])
        json.dump(batch, open(sys.argv[3], "w"))
        print(f"built batch_seq={batch['batch_seq']} prev={batch['prev_batch_hash']} -> {sys.argv[3]}")
    elif command == "deliver":
        status, body = deliver(json.load(open(sys.argv[2])))
        print(json.dumps({"http_status": status, "response": body}, sort_keys=True))
        sys.exit(0 if status == 200 else 1)
