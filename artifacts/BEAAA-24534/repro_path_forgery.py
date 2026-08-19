"""BEAAA-24534 item 1: independent reproduction of the content-boundary path forgery.

Drives the real sealed 0.1.0 appliance (BatchBuilder + Ed25519 Signer) and the
real ApplianceIngestStore. Run from a checkout root:

    PYTHONPATH=tools/log-tracker/src python3 artifacts/BEAAA-24534/repro_path_forgery.py

Exit 0 means the boundary held on every decoy; exit 1 means at least one decoy
was accepted.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEALED = REPO / "ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance"
MIGRATION = REPO / "tools/log-tracker/migrations/0002_appliance_ingest.sql"
sys.path.insert(0, str(REPO / "tools/log-tracker/src"))

from log_tracker.appliance_ingest import ApplianceIngestStore, IngestRejected  # noqa: E402
from log_tracker.appliance_ingest_server import load_sealed_contract  # noqa: E402

verify_batch, CONTENT_FIELDS = load_sealed_contract(SEALED)
sys.path.insert(0, str(SEALED))
from appliance import pipeline  # noqa: E402
from appliance.batch import BatchBuilder, Signer  # noqa: E402

PRODUCER_PINS = {
    "collector_version": "appliance-0.1.0", "evidence_profile_version": "1.0",
    "scanner_version": "na-appliance", "corpus_version": "na-appliance",
    "rule_pack_version": "0.1", "seed": 0,
}
PAYLOAD = {"message": "LEAKED CUSTOMER TEXT", "recipient": "victim@example.invalid"}

# Every decoy is the same prohibited payload; only its address differs.
DECOYS = {
    "counts.by_action_class": "top-level key forging the exempt dotted path",
    "$.counts.by_action_class": "top-level key forging the exempt path including the root",
    "extra": "control: an ordinary key, must always be caught",
}


def main() -> int:
    root = Path(tempfile.mkdtemp())
    signer = Signer(str(root / "appliance.key"), "appliance-key-1")
    builder = BatchBuilder("a1", signer, str(root / "state"), PRODUCER_PINS)
    raw = [{"event_id": "e1", "tenant_id": "t1", "agent_id": "growth-agent",
            "ts": "2026-08-17T00:00:00Z", "type": "social_post",
            "content": "synthetic body", "to": "synthetic@example.invalid",
            "billable": True}]
    genuine = builder.build([pipeline.redact(pipeline.normalize(r)) for r in raw],
                            [], {"start": "x", "end": "y"})[0]

    store = ApplianceIngestStore(root / "tracker.sqlite3", MIGRATION,
                                 verify_batch=verify_batch,
                                 prohibited_fields=CONTENT_FIELDS)
    store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                 token="secret", public_key_pem=signer.public_pem())

    print(f"A. sealed CONTENT_FIELDS: {len(CONTENT_FIELDS)} fields, "
          f"prohibited payload keys {sorted(PAYLOAD)} all members: "
          f"{set(PAYLOAD) <= set(CONTENT_FIELDS)}")
    print(f"B. genuine verify_batch: {verify_batch(genuine, signer.public_pem())}")

    leaks = 0
    for key, why in DECOYS.items():
        decoy = dict(genuine)
        decoy[key] = dict(PAYLOAD)
        verified = verify_batch(decoy, signer.public_pem())
        try:
            store.ingest(decoy, token="secret", tenant_id="t1", source_id="enso-synthetic")
            verdict = "PASSED -- LEAK"
            leaks += 1
        except IngestRejected as exc:
            verdict = f"rejected: {exc}"
        print(f"C. decoy {key!r} ({why})\n   unsigned key still verifies: {verified}"
              f"\n   content boundary: {verdict}")

    print(f"\nD. leaks: {leaks}")
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
