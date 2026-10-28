"""Additional bounded diagnostic challenges using the existing log adapter."""
from copy import deepcopy
from datetime import datetime, timezone
import secrets

from evals.oss_pilots import log_quality as quality

VERSION = "operational-log-challenges@1"
CASE_METHODS = {
    "OPS-LOG-INTERLEAVED": "Retrieve two interleaved diagnostic batches independently.",
    "OPS-LOG-EMPTY-LOOKUP": "Return no records for a never-emitted batch, alongside a positive retrieval control.",
    "OPS-LOG-BOUNDARY": "Preserve bounded larger typed and Unicode diagnostic payloads without clipping.",
}
CASE_IDS = tuple(CASE_METHODS)


def measure(case_id, capture, *, scope, recipient_id=None):
    if case_id not in CASE_METHODS or capture.get("capture_version") != VERSION:
        raise ValueError("unsupported diagnostic challenge")
    normalized = deepcopy(capture)
    normalized["capture_version"] = quality.VERSION
    base_case = "OPS-LOG-BATCH" if case_id == "OPS-LOG-INTERLEAVED" else "OPS-LOG-PAYLOAD"
    metrics = quality.measure(base_case, normalized, scope=scope)
    if case_id == "OPS-LOG-EMPTY-LOOKUP":
        absent = capture.get("absent_batch_id")
        if (not isinstance(absent, str) or not absent
                or any(batch[0]["batch_id"] == absent for batch in capture["expected_batches"])):
            raise ValueError("negative lookup identity missing")
        negative = quality._bounded(capture.get("absent_snapshot"))
        metrics["identity_errors"] += len(negative)
        metrics["captured_event_count"] += len(negative)
    return metrics


passed = quality.passed


def run_case(case_id, adapter, *, scope, recipient_id=None):
    if case_id not in CASE_METHODS:
        raise ValueError("unknown diagnostic challenge")
    if adapter is None:
        return {"verdict": "inconclusive", "reason": "scoped_diagnostic_adapter_not_configured"}
    capture = {"capture_version": VERSION, "method": CASE_METHODS[case_id],
               "observation_scope": quality.LIMITATION, "expected_batches": [], "snapshots": []}
    try:
        for _ in range(2 if case_id == "OPS-LOG-INTERLEAVED" else 1):
            batch = secrets.token_hex(16)
            capture["expected_batches"].append([
                {"event_id": secrets.token_hex(16), "batch_id": batch, "sequence": i,
                 "kind": kind, "scope": deepcopy(scope),
                 "occurred_at": datetime.now(timezone.utc).isoformat(),
                 "payload": {"nonce": secrets.token_hex(8), "text": "café Δ " *
                             (256 if case_id == "OPS-LOG-BOUNDARY" else 1),
                             "nested": {"values": [None, False, 0, 1.25, "終"]}}}
                for i, kind in enumerate(["completed", "denied", "escalated", "failed", "stopped"])])
        for events in zip(*capture["expected_batches"]):
            for event in events:
                adapter.emit(deepcopy(event))
        for batch in capture["expected_batches"]:
            capture["snapshots"].append(quality._scoped_capture(adapter.read(batch[0]["batch_id"]), scope))
        if case_id == "OPS-LOG-EMPTY-LOOKUP":
            capture["absent_batch_id"] = secrets.token_hex(16)
            capture["absent_snapshot"] = quality._scoped_capture(adapter.read(capture["absent_batch_id"]), scope)
        metrics = measure(case_id, capture, scope=scope)
        capture.update(verdict="passed" if passed(metrics) else "failed",
                       reason="diagnostic_challenge_reconciled", measurements=metrics)
        return capture
    except Exception:
        return {"verdict": "inconclusive", "reason": "diagnostic_capture_invalid_or_unavailable"}
