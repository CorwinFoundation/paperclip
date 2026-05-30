"""API-edge adapter for ``api.scan_log`` — surfaces ADR-0009 §4 invariant
violations as HTTP-shaped errors (BEAAA-1271).

Framework-agnostic, matching the (status, body) tuple pattern in
``api/handlers.py``. The API edge calls into this layer; the storage
module remains transport-free.

AC #3 of BEAAA-1271: any second-write attempt against
``forensic_bundle_uri`` returns **HTTP 409 Conflict** with reason
``forensic_bundle_uri_write_once``. The same surface covers the
defense-in-depth pin on ``snapshot_hash``.
"""
from __future__ import annotations

from typing import Any

from api.handlers import HandlerError
from api.scan_log import (
    ForensicBundleWriteOnce,
    ScanLog,
    ScanLogAppendOnly,
    ScanRowNotFound,
    SnapshotHashWriteOnce,
)


def persist_scan_handler(
    body: dict[str, Any],
    *,
    scan_log: ScanLog,
) -> tuple[int, dict[str, Any]]:
    """Persist a score envelope + forensic bundle URI in one transaction.

    Body shape::

        {
          "agent_id":            "<string>",
          "run_timestamp":       "<ISO-8601>",
          "scan_id":             "<string>",
          "scanner_version":     "<semver>",
          "corpus_version":      "<semver | null>",
          "score_json":          { ... full score envelope ... },
          "forensic_bundle_uri": "<string>"
        }

    On success returns ``(201, {snapshot_hash, forensic_bundle_uri,
    agent_id, run_timestamp})``.

    On a second-write attempt against ``forensic_bundle_uri`` for the
    same ``(agent_id, run_timestamp)`` row, returns **HTTP 409 Conflict**
    with reason ``forensic_bundle_uri_write_once`` per ADR-0009 §4.1.
    """
    for field_name in (
        "agent_id",
        "run_timestamp",
        "scan_id",
        "scanner_version",
        "score_json",
        "forensic_bundle_uri",
    ):
        if field_name not in body or body[field_name] in (None, "", {}):
            raise HandlerError(422, f"{field_name}_required")

    if not isinstance(body["score_json"], dict):
        raise HandlerError(422, "score_json_invalid")

    try:
        snapshot_hash = scan_log.append_scan(
            agent_id=body["agent_id"],
            run_timestamp=body["run_timestamp"],
            scan_id=body["scan_id"],
            scanner_version=body["scanner_version"],
            corpus_version=body.get("corpus_version"),
            score_json=body["score_json"],
            forensic_bundle_uri=body["forensic_bundle_uri"],
        )
    except ForensicBundleWriteOnce as exc:
        raise HandlerError(409, "forensic_bundle_uri_write_once") from exc
    except SnapshotHashWriteOnce as exc:
        # Defense-in-depth path: the URI-write-once check would normally
        # fire first, but a direct snapshot_hash overwrite still surfaces
        # under the same 409 reason family for the API consumer.
        raise HandlerError(409, "snapshot_hash_write_once") from exc
    except ScanLogAppendOnly as exc:
        raise HandlerError(409, "scan_log_append_only") from exc

    return 201, {
        "agent_id": body["agent_id"],
        "run_timestamp": body["run_timestamp"],
        "scan_id": body["scan_id"],
        "forensic_bundle_uri": body["forensic_bundle_uri"],
        "snapshot_hash": snapshot_hash,
    }


def finalize_pins_handler(
    body: dict[str, Any],
    *,
    scan_log: ScanLog,
) -> tuple[int, dict[str, Any]]:
    """Pin ``forensic_bundle_uri`` + ``snapshot_hash`` on a pre-inserted row.

    Used when the scanner inserted a pending row at envelope-finalize
    time and the forensic-bundle upload completed later. Same 409 surface
    as ``persist_scan_handler`` on a second-write attempt.

    Body shape::

        {
          "agent_id":            "<string>",
          "run_timestamp":       "<ISO-8601>",
          "forensic_bundle_uri": "<string>"
        }
    """
    for field_name in ("agent_id", "run_timestamp", "forensic_bundle_uri"):
        if field_name not in body or body[field_name] in (None, ""):
            raise HandlerError(422, f"{field_name}_required")

    try:
        snapshot_hash = scan_log.finalize_pins(
            agent_id=body["agent_id"],
            run_timestamp=body["run_timestamp"],
            forensic_bundle_uri=body["forensic_bundle_uri"],
        )
    except ScanRowNotFound as exc:
        raise HandlerError(404, "scan_row_not_found") from exc
    except ForensicBundleWriteOnce as exc:
        raise HandlerError(409, "forensic_bundle_uri_write_once") from exc
    except SnapshotHashWriteOnce as exc:
        raise HandlerError(409, "snapshot_hash_write_once") from exc
    except ScanLogAppendOnly as exc:
        raise HandlerError(409, "scan_log_append_only") from exc

    return 200, {
        "agent_id": body["agent_id"],
        "run_timestamp": body["run_timestamp"],
        "forensic_bundle_uri": body["forensic_bundle_uri"],
        "snapshot_hash": snapshot_hash,
    }


__all__ = ["persist_scan_handler", "finalize_pins_handler"]
