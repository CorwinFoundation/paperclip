#!/usr/bin/env python3
"""Emit redacted, count-and-hash Instinct acceptance evidence (BEAAA-38060)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def _hash_rows(rows: list[sqlite3.Row]) -> str:
    encoded = json.dumps([list(row) for row in rows], separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def capture(db_path: Path, grant_digest: str) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        grant = connection.execute(
            "SELECT created_at,expires_at,revoked_at,followup_enabled "
            "FROM instinct_standing_grants WHERE grant_digest=?", (grant_digest,)
        ).fetchone()
        if grant is None:
            raise RuntimeError("grant_not_found")
        schedules = connection.execute(
            "SELECT manifest_version,case_id,trigger_key,request_id,scheduled_at "
            "FROM instinct_risk_check_schedule WHERE grant_digest=? "
            "ORDER BY rowid", (grant_digest,)
        ).fetchall()
        followups = connection.execute(
            "SELECT request_id,question,created_at,expires_at,replied_at,reply_json "
            "FROM instinct_followup_requests WHERE grant_digest=? ORDER BY rowid",
            (grant_digest,),
        ).fetchall()
        reports = connection.execute(
            "SELECT deduplication_key,received_at,report_json,provenance "
            "FROM instinct_standing_reports WHERE grant_digest=? ORDER BY rowid",
            (grant_digest,),
        ).fetchall()
        case_ids = [row["case_id"] for row in schedules]
        report_keys = []
        report_channels = []
        for row in reports:
            payload = json.loads(row["report_json"])
            report_keys.append(payload["deduplication_key"])
            report_channels.append(payload["event"].get("observed_channel", "unknown"))
        return {
            "grant_selector": grant_digest[:12],
            "grant_created_at": grant["created_at"],
            "grant_expires_at": grant["expires_at"],
            "grant_revoked": grant["revoked_at"] is not None,
            "followup_enabled": bool(grant["followup_enabled"]),
            "schedule_count": len(schedules),
            "schedule_case_ids": case_ids,
            "schedule_rows_sha256": _hash_rows(schedules),
            "followup_count": len(followups),
            "followup_rows_sha256": _hash_rows(followups),
            "report_count": len(reports),
            "report_task_ids": report_keys,
            "report_channels": report_channels,
            "report_rows_sha256": _hash_rows(reports),
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/var/lib/backbond-stage-b/instinct-standing.sqlite3")
    parser.add_argument("--grant-digest", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(capture(Path(args.db), args.grant_digest), sort_keys=True))
        return 0
    except (OSError, sqlite3.Error, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
