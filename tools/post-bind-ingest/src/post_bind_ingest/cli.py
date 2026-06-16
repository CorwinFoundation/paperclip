from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .manifest_signature import verify_manifest_signature
from .normalizer import normalize_manifest


def _read_manifest(path: Path) -> dict[str, Any] | str:
    data = path.read_text(encoding="utf-8")
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def main() -> None:
    parser = argparse.ArgumentParser(prog="post-bind-ingest")
    parser.add_argument("manifest_path", help="Path to manifest JSON")
    parser.add_argument(
        "--secret",
        help="Optional signature secret; if set, validate manifest signature",
    )
    parser.add_argument(
        "--verify-signature",
        action="store_true",
        default=False,
        help="Reject payloads where manifest signature is invalid",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and print normalized event count only",
    )
    args = parser.parse_args()

    manifest = _read_manifest(Path(args.manifest_path))
    if args.verify_signature:
        if not args.secret:
            raise SystemExit("verification requires --secret")
        verify_manifest_signature(manifest if isinstance(manifest, dict) else json.loads(manifest), args.secret)

    normalized = normalize_manifest(manifest)
    if args.dry_run:
        print(f"events_normalized={len(normalized)}")
        return
    for event in normalized:
        print(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
