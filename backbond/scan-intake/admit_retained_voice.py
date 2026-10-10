#!/usr/bin/env python3
"""Idempotently admit one retained Kaltura record to starter-voice-50@1."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.request import Request, urlopen

SERVER = Path("/opt/backbond/scan-intake/server")
sys.path.insert(0, str(SERVER))
from qa_service import IntakeError, IntakeStore, scanner_payload  # noqa: E402

PACK = "starter-voice-50@1"
TOTAL = 60
TARGET = "https://learning.kaltura.com/"
TRANSPORT = "kaltura_genie_browser"


def _post_json(url: str, payload: dict, token: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        body = json.load(response)
    if not isinstance(body, dict):
        raise RuntimeError("scanner response was not a JSON object")
    return body


def _args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-intake-id", required=True)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--expected-total", required=True, type=int)
    parser.add_argument("--reuse-recipient", action="store_true")
    parser.add_argument("--reuse-consent", action="store_true")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("QA_DB_PATH", "/var/lib/backbond-scan-intake/scan-intake.sqlite")))
    parser.add_argument("--scanner-url", default=os.environ.get("QA_SCANNER_URL", "https://scanner.backbond.ai/scanner/v1"))
    return parser.parse_args(argv)


def _write_receipt(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")


def main(argv=None) -> int:
    args = _args(argv)
    if args.pack_id != PACK or args.expected_total != TOTAL or not args.reuse_recipient or not args.reuse_consent:
        raise SystemExit("refusing admission: exact voice pack, total, recipient and consent reuse are required")
    if args.receipt.exists():
        receipt = json.loads(args.receipt.read_text())
        if receipt.get("source_intake_id") == args.source_intake_id and receipt.get("pack_id") == PACK:
            print("already admitted")
            return 0
        raise SystemExit("refusing admission: receipt belongs to another admission")

    store = IntakeStore(str(args.db))
    source = store.scan(args.source_intake_id)
    consent = store.acceptance(args.source_intake_id)
    if not source or not consent or not consent.get("authorization_confirmed"):
        raise SystemExit("refusing admission: retained recipient or consent is absent")
    if source["endpoint_host"] != "learning.kaltura.com" or source["transport"] != TRANSPORT:
        raise SystemExit("refusing admission: retained target or transport is not approved")
    duplicate = store.db.execute(
        "SELECT id FROM scans WHERE source_intake_id=? OR (endpoint_host=? AND pack_version=?) LIMIT 1",
        (args.source_intake_id, source["endpoint_host"], PACK),
    ).fetchone()
    if duplicate:
        raise SystemExit("refusing admission: prior or active voice admission exists without this receipt")

    try:
        reservation = store.reserve(
            code=source["code"], email=source["email"], agent_label=source["agent_label"],
            endpoint_url=TARGET, transport=TRANSPORT, terms_version=consent["terms_version"],
            improvement_consent=bool(consent["improvement_consent"]), pack_version=PACK,
            source_intake_id=args.source_intake_id,
        )
    except (IntakeError, sqlite3.IntegrityError) as exc:
        raise SystemExit(f"refusing admission: {type(exc).__name__}") from None

    payload = scanner_payload({
        "email": source["email"], "agent_label": source["agent_label"], "endpoint_url": TARGET,
        "transport": TRANSPORT, "auth": {"type": "none"}, "partner": source["partner"],
        "pack_id": PACK, "expected_total": TOTAL,
    }, callback_url=os.environ.get("QA_PUBLIC_API", "https://api.backbond.ai") + "/scan/v1/scanner-webhook",
       callback_secret_ref="partner-qa-v1")
    token = os.environ.get("QA_SCANNER_TOKEN", "")
    try:
        response = _post_json(args.scanner_url.rstrip("/") + "/scans", payload, token)
        scanner_id = response.get("scan_id")
        if not isinstance(scanner_id, str) or not scanner_id:
            raise RuntimeError("scanner response omitted scan_id")
        store.bind_scanner(reservation["scan_id"], scanner_id)
        store.running(reservation["scan_id"])
        _write_receipt(args.receipt, {"source_intake_id": args.source_intake_id,
            "intake_id": reservation["scan_id"], "scanner_id": scanner_id,
            "pack_id": PACK, "expected_total": TOTAL})
    except Exception:
        store.dispatch_unknown(reservation["scan_id"])
        raise SystemExit("admission dispatch is uncertain; inspect the retained row before retrying") from None
    finally:
        payload.clear()
    print("admitted retained record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
