#!/usr/bin/env python3
"""Validate BackBond Scanner v1 release evidence reachability and pins."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.release_gate import (
    ReleaseGateError,
    build_release_manifest,
    validate_release,
)

DEFAULT_MANIFEST_NAMES = ("release_manifest.json", "v1-release-manifest.json")


def _discover_manifest(packet_paths: list[Path]) -> Path | None:
    if not packet_paths:
        return None
    packet_dirs = {path.resolve().parent for path in packet_paths}
    if len(packet_dirs) != 1:
        return None
    packet_dir = next(iter(packet_dirs))
    for name in DEFAULT_MANIFEST_NAMES:
        candidate = packet_dir / name
        if candidate.exists():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packets", nargs="+", type=Path, help="candidate score-envelope JSON packet(s)")
    parser.add_argument("--manifest", type=Path, help="release manifest JSON with pin and evidence URI index")
    parser.add_argument("--write-manifest", type=Path, help="write an aggregate release manifest skeleton and exit")
    parser.add_argument("--repo-root", type=Path, default=REPO)
    args = parser.parse_args(argv)

    if args.write_manifest:
        manifest = build_release_manifest(args.packets)
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "wrote_manifest", "path": str(args.write_manifest)}, sort_keys=True))
        return 0

    manifest_path = args.manifest or _discover_manifest(args.packets)

    try:
        report = validate_release(args.packets, manifest_path=manifest_path, repo_root=args.repo_root)
    except ReleaseGateError as exc:
        print(json.dumps({"status": "fail", "findings": [f.to_dict() for f in exc.findings]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
