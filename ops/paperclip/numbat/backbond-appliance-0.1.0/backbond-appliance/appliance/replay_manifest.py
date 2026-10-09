"""Deterministic, no-content replay manifests for partner-retained batches."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable


class ReplayManifestError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def build_replay_manifest(batches: Iterable[dict[str, Any]], public_pem: str,
                          verify_batch: Callable[[dict[str, Any], str], bool]) -> dict[str, Any]:
    """Verify a contiguous signed chain and project only replay-safe fields."""
    ordered = sorted(batches, key=lambda batch: batch.get("batch_seq", -1))
    if not ordered:
        raise ReplayManifestError("no_batches_selected")
    appliance_id, previous, rows = ordered[0].get("appliance_id"), None, []
    expected_seq = ordered[0].get("batch_seq")
    for index, batch in enumerate(ordered):
        if batch.get("appliance_id") != appliance_id:
            raise ReplayManifestError("mixed_appliance_selection")
        if batch.get("batch_seq") != expected_seq:
            raise ReplayManifestError(f"non_contiguous_batch_sequence:{batch.get('batch_seq')}")
        if not verify_batch(batch, public_pem):
            raise ReplayManifestError(f"signature_or_batch_hash_invalid:{batch.get('batch_seq')}")
        if index and batch.get("prev_batch_hash") != previous:
            raise ReplayManifestError(f"broken_batch_chain:{batch.get('batch_seq')}")
        linked: dict[str, list[dict[str, Any]]] = {}
        for determination in batch["determinations"]:
            linked.setdefault(determination.get("event_id"), []).append({
                "rule_id": determination.get("rule_id"),
                "severity": determination.get("severity"),
                "outcome": determination.get("outcome", "matched"),
                "evidence_hash": determination.get("evidence_hash"),
            })
        rows.append({
            "batch_seq": batch["batch_seq"], "window": batch["window"],
            "producer": batch["producer"], "batch_hash": batch["batch_hash"],
            "prev_batch_hash": batch["prev_batch_hash"],
            "events": [{"event_id": event.get("event_id"), "timestamp": event.get("ts"),
                        "action_class": event.get("action_class"), "outcome": event.get("outcome"),
                        "evidence_hash": event.get("evidence_hash"),
                        "determinations": linked.get(event.get("event_id"), [])}
                       for event in batch["events"]],
        })
        previous = batch["batch_hash"]
        expected_seq += 1
    payload = {"schema": "backbond.replay-manifest.v1", "appliance_id": appliance_id,
               "batches": rows}
    # Commits to every other canonical manifest byte; a file cannot contain its own byte hash.
    payload["artifact_sha256"] = "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()
    return payload


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return _canonical(manifest) + b"\n"
