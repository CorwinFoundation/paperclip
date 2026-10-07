#!/usr/bin/env python3
"""Local scanner/test-development access to consented improvement samples."""
import argparse
import json
import os
from qa_service import IntakeStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('QA_DB_PATH', '/var/lib/backbond-scan-intake/scan-intake.sqlite'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    get = commands.add_parser('get')
    get.add_argument('scan_id')
    withdraw = commands.add_parser('withdraw')
    withdraw.add_argument('scan_id')
    args = parser.parse_args()
    store = IntakeStore(args.db)
    if args.command == 'list':
        value = store.improvement_list()
    elif args.command == 'get':
        value = store.improvement_get(args.scan_id)
    else:
        value = {'withdrawn': store.improvement_withdraw(args.scan_id)}
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
