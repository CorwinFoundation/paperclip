from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .manifest_signature import verify_manifest_signature
from .normalizer import normalize_manifest, normalize_receiver_events


def _read_payload(path: Path) -> dict[str, Any] | str:
    data = path.read_text(encoding="utf-8")
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def main() -> None:
    parser = argparse.ArgumentParser(prog="post-bind-ingest")
    parser.add_argument("payload_path", help="Path to manifest or receiver payload JSON")
    parser.add_argument(
        "--mode",
        choices=("manifest", "receiver"),
        default="manifest",
        help="Ingest payload mode. Receiver mode verifies the ed25519 JWT envelope.",
    )
    parser.add_argument(
        "--secret",
        help="Optional signature secret; if set, validate manifest signature",
    )
    parser.add_argument(
        "--jwt-public-key",
        help="ed25519 JWT public key for receiver mode, as PEM text or unpadded base64url raw key bytes",
    )
    parser.add_argument(
        "--clock-skew-seconds",
        type=int,
        default=300,
        help="Receiver JWT clock-skew window in seconds",
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

    receiver_mode = args.mode == "receiver" or bool(args.jwt_public_key)
    if os.path.getsize(args.payload_path) <= 0:
        raise SystemExit("receiver payload is empty" if receiver_mode else "manifest payload is empty")

    payload = _read_payload(Path(args.payload_path))
    if receiver_mode:
        if not args.jwt_public_key:
            raise SystemExit("receiver mode requires --jwt-public-key")
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
        return

    if args.verify_signature:
        if not args.secret:
            raise SystemExit("verification requires --secret")
        verify_manifest_signature(payload if isinstance(payload, dict) else json.loads(payload), args.secret)

    normalized = normalize_manifest(payload)
    if args.dry_run:
        print(f"events_normalized={len(normalized)}")
        return
    for event in normalized:
        print(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
