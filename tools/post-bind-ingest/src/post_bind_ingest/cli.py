from __future__ import annotations

import argparse
import json
import os
import sys
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
        "--jwt-keyring-json",
        help="Receiver keyring as JSON object mapping kid to PEM text or unpadded base64url raw Ed25519 public key bytes",
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
    parser.add_argument(
        "--output-path",
        help="Optional local JSONL output path for receiver mode. Parent directory must already exist.",
    )
    parser.add_argument(
        "--egress-url",
        help="Optional outbound sink URL for receiver mode. Rejected unless --enable-egress is set.",
    )
    parser.add_argument(
        "--enable-egress",
        action="store_true",
        default=False,
        help="Explicitly allow a configured outbound sink after approval.",
    )
    args = parser.parse_args()

    receiver_mode = args.mode == "receiver" or bool(args.jwt_keyring_json)
    if os.path.getsize(args.payload_path) <= 0:
        raise SystemExit("receiver payload is empty" if receiver_mode else "manifest payload is empty")
    if args.egress_url and not args.enable_egress:
        raise SystemExit("egress-url requires --enable-egress")
    if args.enable_egress and not args.egress_url:
        raise SystemExit("enable-egress requires --egress-url")
    if args.output_path and args.egress_url:
        raise SystemExit("output-path and egress-url are mutually exclusive")

    payload = _read_payload(Path(args.payload_path))
    if receiver_mode:
        if not args.jwt_keyring_json:
            raise SystemExit("receiver mode requires --jwt-keyring-json")
        try:
            keyring = json.loads(args.jwt_keyring_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"jwt-keyring-json must be valid JSON: {exc}") from exc
        if not isinstance(keyring, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in keyring.items()):
            raise SystemExit("jwt-keyring-json must be an object mapping kid strings to public-key strings")
        normalized = normalize_receiver_events(
            payload,
            jwt_keyring=keyring,
            clock_skew_seconds=args.clock_skew_seconds,
        )
        if args.dry_run:
            print(f"events_normalized={len(normalized)}")
            return
        if args.output_path:
            output_path = Path(args.output_path)
            if not output_path.parent.exists():
                raise SystemExit(f"output parent does not exist: {output_path.parent}")
            lines = [
                json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                for event in normalized
            ]
            output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            print(f"events_written={len(normalized)} output_path={output_path}")
            return
        if args.egress_url:
            raise SystemExit("egress transport is gated and not implemented in this local-only build")
        for event in normalized:
            print(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False), file=sys.stdout)
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
