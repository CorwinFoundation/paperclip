"""Batch assembly + trust core: Ed25519 signing, hash-chaining, idempotency,
producer pins. Contract-v1.0 aligned (counts/classes/determinations/hashes only).

This is the reused BackBond Collector core; the appliance carries a local copy so
it can sign at the edge and ship only signed, chained batches home.
"""
from __future__ import annotations
import json, hashlib, os
from typing import List, Dict, Any, Optional
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha256(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


class Signer:
    def __init__(self, key_path: str, key_id: str):
        self.key_id = key_id
        if os.path.exists(key_path):
            with open(key_path, "rb") as fh:
                self._sk = serialization.load_pem_private_key(fh.read(), password=None)
        else:
            self._sk = Ed25519PrivateKey.generate()
            with open(key_path, "wb") as fh:
                fh.write(self._sk.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ))
            os.chmod(key_path, 0o600)

    def public_pem(self) -> str:
        return self._sk.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def sign(self, payload: bytes) -> str:
        import base64
        return base64.b64encode(self._sk.sign(payload)).decode()


class BatchBuilder:
    """Builds signed, hash-chained batches. Persists chain state + idempotency set."""

    def __init__(self, appliance_id: str, signer: Signer, state_dir: str, producer_pins: Dict[str, Any]):
        self.appliance_id = appliance_id
        self.signer = signer
        self.state_dir = state_dir
        self.producer_pins = producer_pins
        os.makedirs(state_dir, exist_ok=True)
        self._chain_path = os.path.join(state_dir, "chain.state")
        self._idem_path = os.path.join(state_dir, "idempotency.state")
        self._prev_hash = None
        self._seq = 0
        chain = self._load(self._chain_path)
        if chain:
            st = json.loads(chain)
            self._prev_hash = st.get("prev_hash")
            self._seq = int(st.get("seq", 0))
        self._seen = set()
        if os.path.exists(self._idem_path):
            with open(self._idem_path) as fh:
                self._seen = set(l.strip() for l in fh if l.strip())

    def _load(self, p: str) -> Optional[str]:
        return open(p).read().strip() if os.path.exists(p) else None

    def _persist_chain(self, h: str):
        with open(self._chain_path, "w") as fh:
            json.dump({"seq": self._seq, "prev_hash": h}, fh)

    def _persist_seen(self, ids):
        with open(self._idem_path, "a") as fh:
            for i in ids:
                fh.write(i + "\n")

    def build(self, events: List[Dict[str, Any]], determinations: List[Dict[str, Any]],
              window: Dict[str, str], max_events: int = 100_000) -> List[Dict[str, Any]]:
        """Build one or more signed, hash-chained batches. Events are split into
        windows of at most `max_events` (P0-1) so a huge run never lands in a single
        oversized batch; every window is independently signed and chained to the prior.
        Returns a list (possibly empty). Idempotency drops already-seen events, and
        determinations only ride with their fresh event."""
        # idempotency
        fresh, new_ids, fresh_event_ids = [], [], set()
        for e in events:
            eid = e.get("event_id") or _sha256(_canonical(e))
            if eid in self._seen:
                continue
            fresh.append(e); new_ids.append(eid); fresh_event_ids.add(e.get("event_id"))
        dets_by_event: Dict[Any, list] = {}
        for d in determinations:
            if d.get("event_id") in fresh_event_ids:
                dets_by_event.setdefault(d.get("event_id"), []).append(d)
        if not fresh and not any(dets_by_event.values()):
            return []

        # window the fresh events
        chunks = [fresh[i:i + max_events] for i in range(0, len(fresh), max_events)] or [[]]
        batches, persisted_ids = [], []
        for chunk in chunks:
            chunk_dets = [d for e in chunk for d in dets_by_event.get(e.get("event_id"), [])]
            batches.append(self._make_batch(chunk, chunk_dets, window))
            persisted_ids.extend(e.get("event_id") or _sha256(_canonical(e)) for e in chunk)

        self._persist_seen(new_ids)
        self._seen.update(new_ids)
        return batches

    def _make_batch(self, events: List[Dict[str, Any]], determinations: List[Dict[str, Any]],
                    window: Dict[str, str]) -> Dict[str, Any]:
        self._seq += 1
        by_class: Dict[str, int] = {}
        for e in events:
            by_class[e["action_class"]] = by_class.get(e["action_class"], 0) + 1
        # contract-v1.0 counts shape ({billable_actions, tokens}) + appliance extensions
        counts = {
            "billable_actions": sum(1 for e in events if e.get("billable")),
            "tokens": 0,                       # metadata appliance does not meter tokens
            "events": len(events),
            "determinations": len(determinations),
            "by_action_class": by_class,
        }
        # contract-v1.0 aligned body. NOTE: policy_number/agent_id are per-event here
        # (fleet-of-fleets: one batch spans many tenants/agents); single-policy batching
        # is a metering-contract grouping decision (P1 / contract approval gate).
        body = {
            "batch_id": f"{self.appliance_id}:{self._seq}",
            "appliance_id": self.appliance_id,
            "batch_seq": self._seq,
            "prev_batch_hash": self._prev_hash,
            "window": window,
            "events": events,
            "determinations": determinations,
            "counts": counts,
            "producer": self.producer_pins,
        }
        payload = _canonical(body)
        batch_hash = _sha256(payload)
        signed = dict(body)
        signed["batch_hash"] = batch_hash
        signed["signature"] = self.signer.sign(payload)
        signed["key_id"] = self.signer.key_id
        self._prev_hash = batch_hash
        self._persist_chain(batch_hash)
        return signed


def verify_batch(batch: Dict[str, Any], public_pem: str) -> bool:
    """Verify signature + recompute batch_hash over the canonical body."""
    import base64
    try:
        body = {k: batch[k] for k in (
            "batch_id", "appliance_id", "batch_seq", "prev_batch_hash", "window",
            "events", "determinations", "counts", "producer")}
        payload = _canonical(body)
        if _sha256(payload) != batch.get("batch_hash"):
            return False
        pk: Ed25519PublicKey = serialization.load_pem_public_key(public_pem.encode())
        pk.verify(base64.b64decode(batch["signature"]), payload)
        return True
    except Exception:
        # malformed/missing-field batch from an untrusted sender — reject, never crash.
        return False
