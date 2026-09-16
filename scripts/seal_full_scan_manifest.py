#!/usr/bin/env python3
"""Re-emit a full-scan manifest with integrity fields bound to exact content."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.bundle import sign_manifest
from api.full_scan_executor import manifest_content_hash, seal_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source = json.loads(args.source.read_text(encoding="utf-8"))
    sealed = seal_manifest(source)
    unsigned = {
        key: value
        for key, value in sealed.items()
        if key not in {"signed_manifest", "manifest_hash"}
    }
    if sealed["manifest_hash"] != manifest_content_hash(sealed):
        raise SystemExit("manifest hash self-check failed")
    if sealed["signed_manifest"] != sign_manifest(unsigned):
        raise SystemExit("manifest signature self-check failed")

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(
        json.dumps(sealed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(sealed["manifest_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
