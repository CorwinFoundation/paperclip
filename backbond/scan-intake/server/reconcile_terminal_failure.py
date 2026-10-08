#!/usr/bin/env python3
"""Reconcile one intake to an independently verified scanner terminal failure."""
import argparse
import os

from qa_service import IntakeStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intake-id", required=True)
    parser.add_argument("--scanner-id", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    path = os.environ.get("QA_DB_PATH", "/var/lib/backbond-scan-intake/scan-intake.sqlite")
    store = IntakeStore(path)
    store.reconcile_failed(args.intake_id, args.scanner_id, args.reason)
    status = store.scan(args.intake_id)
    print(f"reconciled intake={args.intake_id} status={status['status']} outcome={status['outcome']} reason={status['incomplete_reason']}")


if __name__ == "__main__":
    main()
