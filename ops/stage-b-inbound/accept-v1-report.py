#!/usr/bin/env python3
"""Exercise the local Stage B v1 compatibility path with an existing grant."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINT = "http://127.0.0.1:8788/instinct/report"
SAFE_KEYS = {
    "http_status", "result_state", "one_new_report", "report_count_before",
    "report_count_after", "schedule_count_before", "schedule_count_after",
    "original_request_count", "original_rows_unchanged", "observed_channel",
    "history_sources_included", "history_sources_unavailable",
    "shared_context_status", "real_history_enabled", "evidence_origin",
}


def _snapshot(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _read_protected_token(path: Path) -> str:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError("grant_token_file_must_be_private")
    token = path.read_text().strip()
    if not 16 <= len(token) <= 128 or any(character.isspace() for character in token):
        raise RuntimeError("grant_token_invalid")
    return token


def _original_rows(connection: sqlite3.Connection, request_ids: list[str]) -> dict[str, tuple]:
    rows: dict[str, tuple] = {}
    for request_id in request_ids:
        row = connection.execute(
            "SELECT * FROM instinct_followup_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("original_request_not_found")
        rows[request_id] = tuple(row)
    return rows


def run(db_path: Path, token_path: Path, expected_grant_digest: str,
        acceptance_run_key: str, original_request_ids: list[str], config_path: Path) -> dict:
    settings = dict(line.split("=", 1) for line in config_path.read_text().splitlines()
                    if line and not line.startswith("#") and "=" in line)
    if settings.get("BB_HISTORY_EXTERNAL_ENABLED") != "0":
        raise RuntimeError("real_history_must_remain_disabled")
    token = _read_protected_token(token_path)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if digest != expected_grant_digest:
        raise RuntimeError("grant_selector_mismatch")
    if not 8 <= len(acceptance_run_key) <= 128:
        raise RuntimeError("acceptance_run_key_invalid")
    if not original_request_ids or len(set(original_request_ids)) != len(original_request_ids):
        raise RuntimeError("original_request_ids_invalid")

    connection = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        grant = connection.execute(
            "SELECT * FROM instinct_standing_grants WHERE grant_digest=? "
            "AND revoked_at IS NULL AND expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (digest,),
        ).fetchone()
        if grant is None:
            raise RuntimeError("selected_grant_not_active")
        reports_before = _snapshot(connection, "instinct_standing_reports")
        schedule_before = _snapshot(connection, "instinct_risk_check_schedule")
        originals_before = _original_rows(connection, original_request_ids)
        if any(row[1] != digest for row in originals_before.values()):
            raise RuntimeError("original_request_grant_mismatch")
        unknown = "unknown"
        event = {
            "occurred_at": unknown, "category": "synthetic_acceptance",
            "proposed_external_action": unknown, "actual_external_action": unknown,
            "approval_source": unknown, "approval_status": unknown,
            "money_at_risk": unknown, "outcome": unknown,
            "evidence_origin": "agent_reported", "gaps": unknown, "synthetic": True,
        }
        previous = connection.execute(
            "SELECT report_json FROM instinct_standing_reports WHERE grant_digest=? "
            "ORDER BY rowid DESC LIMIT 1", (digest,)).fetchone()
        if previous is not None:
            previous_event = json.loads(previous["report_json"])["event"]
            for field in ("approval_source", "approval_status", "money_at_risk"):
                event[field] = previous_event[field]
        body = json.dumps({"schema_version": "instinct-standing-report/v1",
                           "deduplication_key": acceptance_run_key,
                           "trigger": "onboarding_history", "event": event}).encode()
        request = Request(ENDPOINT + "/" + token, data=body, method="POST",
                          headers={"Content-Type": "application/json",
                                   "X-Instinct-Sender": grant["sender_email"]})
        with urlopen(request, timeout=10) as response:
            status = response.status
            result = json.load(response)
        reports_after = _snapshot(connection, "instinct_standing_reports")
        schedule_after = _snapshot(connection, "instinct_risk_check_schedule")
        originals_after = _original_rows(connection, original_request_ids)
        inserted = connection.execute(
            "SELECT report_json FROM instinct_standing_reports WHERE grant_digest=? "
            "AND deduplication_key=?", (digest, acceptance_run_key)).fetchone()
        if status != 200 or result.get("state") not in {"received", "duplicate"} or inserted is None:
            raise RuntimeError("report_acceptance_failed")
        normalized = json.loads(inserted["report_json"])["event"]
        expected_delta = 1 if result["state"] == "received" else 0
        evidence = {
            "http_status": status, "result_state": result["state"],
            "one_new_report": len(reports_after) == len(reports_before) + 1,
            "report_count_before": len(reports_before), "report_count_after": len(reports_after),
            "schedule_count_before": len(schedule_before), "schedule_count_after": len(schedule_after),
            "original_request_count": len(original_request_ids),
            "original_rows_unchanged": originals_after == originals_before,
            "observed_channel": normalized["observed_channel"],
            "history_sources_included": normalized["history_sources_included"],
            "history_sources_unavailable": normalized["history_sources_unavailable"],
            "shared_context_status": normalized["shared_context_status"],
            "real_history_enabled": False, "evidence_origin": "direct_http_fixture",
        }
        if (set(evidence) != SAFE_KEYS or not evidence["original_rows_unchanged"]
                or len(reports_after) != len(reports_before) + expected_delta):
            raise RuntimeError("acceptance_invariant_failed")
        return evidence
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/var/lib/backbond-stage-b/instinct-standing.sqlite3")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--grant-digest", required=True)
    parser.add_argument("--acceptance-run-key", required=True)
    parser.add_argument("--original-request-id", action="append", required=True)
    parser.add_argument("--config", default="/etc/backbond/stage-b-inbound.env")
    args = parser.parse_args()
    try:
        print(json.dumps(run(Path(args.db), Path(args.token_file), args.grant_digest,
                             args.acceptance_run_key, args.original_request_id,
                             Path(args.config)), sort_keys=True))
        return 0
    except (OSError, sqlite3.Error, RuntimeError, HTTPError, URLError,
            ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"state": "failed", "error": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
