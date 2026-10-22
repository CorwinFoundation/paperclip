#!/usr/bin/env python3
"""Local administrative candidate; one-time arm and durable disable; never deletes or resets a permit."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.controlled_permit_loader import AdmissionClosed, arm_once, disable, provision_disabled

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('provision-disabled', 'arm-once', 'disable'))
    parser.add_argument('--db', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'provision-disabled':
            provision_disabled(args.db, json.load(sys.stdin))
        elif args.action == 'arm-once':
            arm_once(args.db, json.load(sys.stdin))
        else:
            disable(args.db)
    except (AdmissionClosed, OSError, ValueError) as exc:
        parser.exit(1, 'Controlled state rejected; inspect locally without exporting protected values.\n')
    print('Local state operation complete; no dispatch performed.')

if __name__ == '__main__':
    main()
