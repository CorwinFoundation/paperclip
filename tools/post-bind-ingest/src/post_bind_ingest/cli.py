from __future__ import annotations

import argparse
import json
from typing import Any
import os
from pathlib import Path

from .normalizer import normalize_receiver_events


def _read_payload(path: Path) -> dict[str, Any] | str:
    data = path.read_text(encoding="utf-8")
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def main() -> None:
    parser = argparse.ArgumentParser(prog="post-bind-ingest")
    parser.add_argument("receiver_payload_path", help="Path to Log Event Schema v1.0 receiver payload JSON")
    parser.add_argument(
        "--jwt-public-key",
        required=True,
        help="ed25519 JWT public key, as PEM text or unpadded base64url raw key bytes",
    )
    parser.add_argument(
        "--clock-skew-seconds",
        type=int,
        default=300,
        help="JWT clock-skew window (seconds)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and print normalized event count only",
    )
    args = parser.parse_args()

    if os.path.getsize(args.receiver_payload_path) <= 0:
        raise SystemExit("receiver payload is empty")

    payload = _read_payload(Path(args.receiver_payload_path))
    normalized = normalize_receiver_events(
        payload,
        jwt_public_key=args.jwt_public_key,
        clock_skew_seconds=args.clock_skew_seconds,
    )
    if args.dry_run:
        print(f"events_normalized={len(normalized)}")
        return
    for event in normalized:
        print(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
