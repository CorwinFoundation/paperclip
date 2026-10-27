"""Bounded diagnostic-log quality challenges; no model calls or production writes.

The trusted adapter must address a scoped, sanitized diagnostic pipeline.
Immediate repeat reads do not establish retention, access control or clock sync.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import secrets

VERSION = "operational-log-quality@1"
CASE_METHODS = {
    "OPS-LOG-PAYLOAD": "Preserve typed nested diagnostic payloads, including Unicode and null values.",
    "OPS-LOG-TIME": "Preserve timezone-aware source event instants and event order metadata.",
    "OPS-LOG-BATCH": "Retrieve each of two independently identified diagnostic batches without mixing them.",
    "OPS-LOG-REPEAT": "Return the same diagnostic records on two immediate reads; not a retention test.",
    "OPS-ALERT-SELECTIVITY": "Deliver a failure alert to the configured recipient, with no delivered alert for a success-only batch.",
}
CASE_IDS = tuple(CASE_METHODS)
ALERT_CASE = "OPS-ALERT-SELECTIVITY"
LIMITATION = "Scoped synthetic diagnostic path only; production coverage, retention, access controls and clock synchronization are not evaluated."


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp absent")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _bounded(value):
    if (not isinstance(value, list) or len(value) > 20
            or any(not isinstance(row, dict) for row in value) or len(_canonical(value)) > 65536):
        raise ValueError("invalid or oversized diagnostic capture")
    return deepcopy(value)


def _scoped_capture(value, scope):
    # A broken adapter can return another tenant's data. Preserve the failed
    # correlation/count evidence without persisting that record's private bytes.
    return [row if row.get("scope") == scope else {
        "scope": {"diagnostic_capture": "outside requested scope; record redacted"},
        "status": "delivered" if row.get("status") == "delivered" else "not delivered",
    } for row in _bounded(value)]


def measure(case_id, capture, *, scope, recipient_id=None):
    """Reconcile captured bytes/metadata, independently of claimed verdict/metrics."""
    if case_id not in CASE_METHODS or capture.get("capture_version") != VERSION:
        raise ValueError("unsupported logging challenge")
    batches = capture.get("expected_batches")
    expected_count = 2 if case_id in {"OPS-LOG-BATCH", ALERT_CASE} else 1
    if not isinstance(batches, list) or len(batches) != expected_count:
        raise ValueError("missing expected batches")
    all_ids, batch_ids = set(), set()
    for batch in batches:
        if not isinstance(batch, list) or len(batch) != 5:
            raise ValueError("missing source events")
        batch_id = batch[0].get("batch_id")
        if not isinstance(batch_id, str) or not batch_id or batch_id in batch_ids:
            raise ValueError("invalid batch identity")
        batch_ids.add(batch_id)
        for i, event in enumerate(batch):
            eid = event.get("event_id")
            if (not isinstance(eid, str) or not eid or eid in all_ids or event.get("scope") != scope
                    or event.get("batch_id") != batch_id or type(event.get("sequence")) is not int
                    or event["sequence"] != i or not isinstance(event.get("payload"), dict)):
                raise ValueError("invalid source event binding")
            _instant(event.get("occurred_at"))
            all_ids.add(eid)
    wanted_kinds = ["completed", "denied", "escalated", "failed", "stopped"]
    for i, batch in enumerate(batches):
        expected_kinds = ["completed"] * 5 if case_id == ALERT_CASE and i == 0 else wanted_kinds
        if [e.get("kind") for e in batch] != expected_kinds:
            raise ValueError("invalid challenge event types")
    snapshots = capture.get("snapshots")
    expected_snapshots = [batches[0], batches[0]] if case_id == "OPS-LOG-REPEAT" else batches
    if not isinstance(snapshots, list) or len(snapshots) != len(expected_snapshots):
        raise ValueError("missing capture snapshots")
    metrics = dict(expected_event_count=sum(map(len, expected_snapshots)), captured_event_count=0,
                   identity_errors=0, payload_errors=0, timestamp_errors=0,
                   repeat_difference_count=0, false_alert_count=0, missing_alert_count=0)
    for expected, snapshot in zip(expected_snapshots, snapshots):
        observed = _bounded(snapshot)
        metrics["captured_event_count"] += len(observed)
        ids = [e.get("event_id") for e in observed]
        if any(not isinstance(eid, str) for eid in ids):
            metrics["identity_errors"] += 1
            continue
        indexed = {e["event_id"]: e for e in observed}
        metrics["identity_errors"] += (len(ids) - len(indexed)
            + len(set(indexed) ^ {e["event_id"] for e in expected}))
        for event in expected:
            actual = indexed.get(event["event_id"])
            if actual is None:
                continue
            metrics["identity_errors"] += int(any(actual.get(k) != event[k]
                or (k == "sequence" and type(actual.get(k)) is not int)
                for k in ("batch_id", "scope", "kind", "sequence")))
            if case_id == "OPS-LOG-PAYLOAD":
                metrics["payload_errors"] += int(_canonical(actual.get("payload")) != _canonical(event["payload"]))
            if case_id == "OPS-LOG-TIME":
                try:
                    metrics["timestamp_errors"] += int(_instant(actual.get("occurred_at")) != _instant(event["occurred_at"]))
                except (ValueError, TypeError):
                    metrics["timestamp_errors"] += 1
    if case_id == "OPS-LOG-REPEAT":
        # Delivery order is immaterial; the records themselves must stay stable.
        metrics["repeat_difference_count"] = int(sorted(map(_canonical, snapshots[0])) != sorted(map(_canonical, snapshots[1])))
    if case_id == ALERT_CASE:
        if not isinstance(recipient_id, str) or not recipient_id:
            raise ValueError("approved alert recipient required")
        deliveries = capture.get("deliveries")
        if not isinstance(deliveries, list) or len(deliveries) != 2:
            raise ValueError("missing alert captures")
        negative, positive = map(_bounded, deliveries)
        metrics["false_alert_count"] = sum(d.get("status") == "delivered" for d in negative)
        failed = next(e for e in batches[1] if e["kind"] == "failed")
        good = [d for d in positive if all(d.get(k) == value for k, value in {
            "event_id": failed["event_id"], "batch_id": failed["batch_id"], "scope": scope,
            "recipient_id": recipient_id, "status": "delivered"}.items())]
        metrics["false_alert_count"] += sum(d.get("status") == "delivered" and d not in good for d in positive)
        metrics["missing_alert_count"] = int(len(good) != 1)
    return metrics


def passed(metrics):
    return metrics["expected_event_count"] == metrics["captured_event_count"] and not any(
        value for key, value in metrics.items() if key not in {"expected_event_count", "captured_event_count"})


def run_case(case_id, adapter, *, scope, recipient_id=None):
    if case_id not in CASE_METHODS:
        raise ValueError("unknown logging challenge")
    if adapter is None or (case_id == ALERT_CASE and
            (not recipient_id or not callable(getattr(adapter, "read_deliveries", None)))):
        return {"verdict": "inconclusive", "reason": "scoped_diagnostic_adapter_not_configured"}
    capture = {"capture_version": VERSION, "method": CASE_METHODS[case_id],
               "expected_batches": [], "snapshots": [], "deliveries": [], "observation_scope": LIMITATION}
    try:
        count = 2 if case_id in {"OPS-LOG-BATCH", ALERT_CASE} else 1
        for b in range(count):
            batch_id = secrets.token_hex(16)
            kinds = ["completed"] * 5 if case_id == ALERT_CASE and b == 0 else ["completed", "denied", "escalated", "failed", "stopped"]
            batch = [{"event_id": secrets.token_hex(16), "batch_id": batch_id, "sequence": i,
                      "kind": kind, "scope": deepcopy(scope), "occurred_at": datetime.now(timezone.utc).isoformat(),
                      "payload": {"nonce": secrets.token_hex(8), "text": "diagnostic café Δ", "amount": 1.25,
                                  "active": False, "items": [None, 0, "synthetic"]}} for i, kind in enumerate(kinds)]
            capture["expected_batches"].append(batch)
            for event in batch:
                adapter.emit(deepcopy(event))
        for batch in capture["expected_batches"]:
            capture["snapshots"].append(_scoped_capture(adapter.read(batch[0]["batch_id"]), scope))
            if case_id == ALERT_CASE:
                capture["deliveries"].append(_scoped_capture(adapter.read_deliveries(batch[0]["batch_id"]), scope))
        if case_id == "OPS-LOG-REPEAT":
            capture["snapshots"].append(_scoped_capture(adapter.read(capture["expected_batches"][0][0]["batch_id"]), scope))
        metrics = measure(case_id, capture, scope=scope, recipient_id=recipient_id)
        capture.update(verdict="passed" if passed(metrics) else "failed", reason="diagnostic_quality_reconciled", measurements=metrics)
        return capture
    except Exception:
        # Adapter exceptions and potentially unscoped records never enter errors.
        return {"verdict": "inconclusive", "reason": "diagnostic_capture_invalid_or_unavailable"}
