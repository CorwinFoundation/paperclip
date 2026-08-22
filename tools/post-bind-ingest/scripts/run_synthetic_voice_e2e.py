#!/usr/bin/env python3
"""BEAAA-20073 synthetic voice source -> Collector -> ingest -> Log Tracker E2E."""
from __future__ import annotations

import argparse, base64, copy, hashlib, json, re, subprocess, sys, tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT / "tools/post-bind-ingest/src", ROOT / "tools/log-tracker/src"):
    sys.path.insert(0, str(p))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from log_tracker.claim_events import SQLiteClaimEventSink, emit_claim_candidate_event
from post_bind_ingest.continuity_gate import compute_gap_state
from post_bind_ingest.invariants import ManifestIdempotencyStore
from post_bind_ingest.normalizer import normalize_receiver_events
from post_bind_ingest.receiver import ReceiverReplayError, ReceiverSchemaError
from post_bind_ingest.receiver.schema import compute_batch_sha256

PROHIBITED_STEMS = {"audio", "body", "call", "caller", "content", "dialogue", "message", "phone", "pii", "record", "speech", "spoken", "text", "transcript", "utterance"}
PII_PATTERNS = (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), re.compile(r"\b(?:\d[ -]?){13,19}\b"))
PINS = {"source_driver":"source-driver-v0.1", "mapping":"synthetic-voice-map-v1.0", "fixture":"synthetic-voice-fixture-v1.0"}
KEY_ID = "synthetic-voice-e2e"

def records(seq: int) -> list[dict[str, Any]]:
    commitment = "sha256:" + hashlib.sha256(f"synthetic-determination-{seq}".encode()).hexdigest()
    return [{"event_id":f"voice-{seq}", "observed_at":f"2026-07-31T00:0{seq}:00Z", "schema_event_type":"agent_invocation", "voice_event_class":"determination", "session_id":"session-synthetic", "agent_id":"voice-agent", "deployment_id":"voice-reference", "outcome_status":"ok", "producer_pins":PINS, "peril":"P2", "severity_class":"S1", "commitment_hash":commitment, "determination_count":1}]

def collector_events(rows: list[dict[str, Any]]) -> dict[str, Any]:
    proc = subprocess.run(["go", "run", "./cmd/synthetic-voice-source"], cwd=ROOT/"tools/are-collector", input=json.dumps(rows), text=True, capture_output=True)
    if proc.returncode: raise ValueError(proc.stderr.strip())
    return json.loads(proc.stdout)

def b64(v: bytes) -> str: return base64.urlsafe_b64encode(v).rstrip(b"=").decode()
def jwt(key: ed25519.Ed25519PrivateKey, claims: dict[str, Any]) -> str:
    head=b64(json.dumps({"alg":"EdDSA","kid":KEY_ID,"typ":"JWT"},sort_keys=True,separators=(",",":")).encode()); body=b64(json.dumps(claims,sort_keys=True,separators=(",",":")).encode()); raw=f"{head}.{body}"; return f"{raw}.{b64(key.sign(raw.encode()))}"

def signed_batch(seq: int, events: list[dict[str, Any]], previous: str|None, key: ed25519.Ed25519PrivateKey) -> dict[str, Any]:
    issued=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00","Z"); digest="sha256:"+compute_batch_sha256(events)
    claims={"iss":"are-collector", "sub":"synthetic-voice", "agent":"voice-agent", "customer_id":"synthetic-voice", "schema_version":"log-event-schema-v1.0", "batch_id":f"voice-batch-{seq}", "batch_sha256":digest, "event_count":len(events), "issued_at":issued, "exp":int(datetime.now(UTC).timestamp())+600, "sequence":seq, "prev_batch_hash":previous, "producer_pins":PINS}
    return {"customer_id":"synthetic-voice", "agent":"voice-agent", "schema_version":"log-event-schema-v1.0", "batch_id":claims["batch_id"], "batch_sha256":digest, "event_count":len(events), "issued_at":issued, "sequence":seq, "prev_batch_hash":previous, "producer_pins":PINS, "events":events, "jwt":jwt(key,claims)}

def inspect_no_content(value: Any, path: str="$", measurement: dict[str, int] | None = None) -> dict[str, int]:
    measured = measurement if measurement is not None else {"nodes_scanned": 0, "violations": 0}
    measured["nodes_scanned"] += 1
    if isinstance(value, dict):
        for k,v in value.items():
            if any(stem in k.lower() for stem in PROHIBITED_STEMS):
                measured["violations"] += 1
                raise AssertionError(f"prohibited voice content field at {path}.{k}")
            inspect_no_content(v, f"{path}.{k}", measured)
    elif isinstance(value, list):
        for i,v in enumerate(value): inspect_no_content(v, f"{path}[{i}]", measured)
    elif isinstance(value, str) and any(pattern.search(value) for pattern in PII_PATTERNS):
        measured["violations"] += 1
        raise AssertionError(f"synthetic PII value at {path}")
    return measured

def persist_determinations(events: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    sink=SQLiteClaimEventSink(path)
    try:
        sink.seed_scanner_run(scan_id="voice-bind",agent_id="voice-agent",run_timestamp="2026-07-31T00:00:00Z")
        sink.seed_scanner_run(scan_id="voice-runtime",agent_id="voice-agent",run_timestamp="2026-07-31T00:03:30Z")
        sink.seed_policy_event(policy_id="voice-policy",agent_id="voice-agent",customer_id="synthetic-voice",event_kind="bind",scan_id_priced="voice-bind",event_t="2026-07-31T00:00:30Z",policy_event_id="voice-bind-event")
        for event in events:
            attrs=event["payload"]["attributes"]
            emit_claim_candidate_event(sink=sink,claim_id=f"voice-claim-{event['event_id']}",claim_event_id=f"voice-claim-event-{event['event_id']}",policy_id="voice-policy",agent_id="voice-agent",customer_id="synthetic-voice",peril=attrs["peril"],severity_class=attrs["severity_class"],event_t=event["logged_at"],evidence_refs_uri=attrs["commitment_hash"],metadata={"determination_count":attrs["determination_count"],"commitment_hash":attrs["commitment_hash"],"bracket_status":"complete"},redaction_class={"claim_id":"hash","policy_id":"hash","agent_id":"hash","customer_id":"hash","peril":"clear","severity_class":"clear","metadata":"redact-on-receipt"})
        rows=sink.list_claim_events("voice-policy")
        if sink.count_missing_brackets() != 0: raise AssertionError("Log Tracker bracket failure")
        return rows
    finally: sink.close()

def run(output: Path|None=None) -> dict[str, Any]:
    out=output or Path(tempfile.mkdtemp(prefix="voice-e2e-")); out.mkdir(parents=True,exist_ok=True)
    key=ed25519.Ed25519PrivateKey.generate(); pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw); keyring={KEY_ID:b64(pub)}; store=ManifestIdempotencyStore(out/"idempotency.state")
    batches=[]; normalized=[]; previous=None
    for seq in (1,2,3):
        product=collector_events(records(seq)); batch=signed_batch(seq,product["events"],previous,key); inspect_no_content(batch); normalized.extend(normalize_receiver_events(batch,jwt_keyring=keyring,idempotency_store=store)); batches.append(batch); previous=batch["batch_sha256"]
    replay=False
    try: normalize_receiver_events(batches[0],jwt_keyring=keyring,idempotency_store=store)
    except ReceiverReplayError: replay=True
    tampered=copy.deepcopy(batches[1]); tampered["events"][0]["payload"]["outcome_status"]="tampered"
    tamper=False
    try: normalize_receiver_events(tampered,jwt_keyring=keyring)
    except ReceiverSchemaError: tamper=True
    injections=[]
    for key,value in (("transcript_url","https://example.invalid/transcript"),("recording_uri","file:///recording.wav"),("caller_phone_number","555-010-9999"),("speech_text","synthetic SSN 123-45-6789 and card 4111 1111 1111 1111")):
        injected=copy.deepcopy(records(1)); injected[0]["producer_pins"][key]=value
        try: collector_events(injected); injections.append(False)
        except ValueError: injections.append(True)
    bad_pin=copy.deepcopy(records(1)); bad_pin[0]["producer_pins"]["fixture"]="synthetic-voice-fixture-v1.0 https://example.invalid/transcript"
    try: collector_events(bad_pin); injections.append(False)
    except ValueError: injections.append(True)
    for value in ("555-010-9999", "+15550109999", "cdn.example.com:transcript.abc123", "caller.jane.doe-at-example.com"):
        injected=copy.deepcopy(records(1)); injected[0]["session_id"]=value
        try: collector_events(injected); injections.append(False)
        except ValueError: injections.append(True)
    stored=persist_determinations(normalized,out/"log-tracker.sqlite3")
    no_content=inspect_no_content({"batches":batches,"normalized":normalized,"stored":stored})
    chain=all(batches[i]["prev_batch_hash"]==batches[i-1]["batch_sha256"] for i in range(1,len(batches))) and batches[0]["prev_batch_hash"] is None
    pins=all(b["producer_pins"]==PINS for b in batches)
    continuity_events=[{"event_type":"agent_invocation","logged_at":event["logged_at"],"payload":{"tenant_id":"synthetic-voice","agent_id":"voice-agent","deployment_id":"voice-reference","collector_id":"synthetic-voice-e2e","batch_seq":batch["sequence"]}} for batch,event in zip(batches,normalized)]
    continuity=compute_gap_state(continuity_events,tenant_id="synthetic-voice",agent_id="voice-agent",deployment_id="voice-reference",collector_id="synthetic-voice-e2e",evaluated_at="2026-07-31T00:04:00Z",heartbeat_max_age_seconds=3600)
    tracker_ok=len(stored)==len(normalized)==3 and all(row["evidence_refs_uri"]==event["payload"]["attributes"]["commitment_hash"] for row,event in zip(stored,normalized))
    assertions={"signed_sequential_batches":len(batches)==3,"prev_batch_hash_chain":chain,"continuity_gate_healthy":continuity.to_dict()["continuity_status"]=="healthy","idempotency_replay_rejected":replay,"tamper_rejected":tamper,"producer_pins":pins,"no_content_recursive":no_content["nodes_scanned"]>0 and no_content["violations"]==0,"content_injection_rejected":all(injections),"log_tracker_determinations_persisted":tracker_ok}
    report={"status":"PASS" if all(assertions.values()) else "FAIL","assertions":assertions,"counts":{"batches":3,"normalized_events":len(normalized),"log_tracker_rows":len(stored),"no_content_nodes_scanned":no_content["nodes_scanned"],"no_content_violations":no_content["violations"]},"continuity":continuity.to_dict(),"producer_pins":PINS,"platform":"synthetic-generic-reference"}
    (out/"batches.json").write_text(json.dumps(batches,indent=2,sort_keys=True)); (out/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)); return report

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--output-dir",type=Path); a=p.parse_args(); report=run(a.output_dir); print(json.dumps(report,indent=2,sort_keys=True)); return 0 if report["status"]=="PASS" else 1
if __name__=="__main__": raise SystemExit(main())
