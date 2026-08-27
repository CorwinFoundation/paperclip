from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .manifest_signature import verify_manifest_signature
from .normalizer import normalize_manifest, normalize_receiver_events
from .runtime_continuity import PersistentContinuityRuntime, RuntimeContinuityError

# Exit code for a continuity refusal, kept distinct from a usage error so a
# caller can tell "ineligible" from "invoked wrong" without parsing stderr.
CONTINUITY_REFUSED_EXIT = 3


def _read_payload(path: Path) -> dict[str, Any] | str:
    data = path.read_text(encoding="utf-8")
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def _require(args: argparse.Namespace, names: tuple[str, ...]) -> None:
    missing = [name for name in names if getattr(args, name.replace("-", "_")) in (None, "")]
    if missing:
        raise SystemExit("missing required options: " + ", ".join(f"--{name}" for name in missing))


def _run_continuity_ingest(args: argparse.Namespace, payload: Any, keyring: dict[str, str]) -> None:
    """Ingest one signed batch through the persistent continuity runtime.

    This is the shipped receiver path, not a harness: the runtime is opened
    against the vendor-local database, the batch either advances the chain or is
    refused. An accepted batch then continues through the receiver's normal
    normalization and sink pipeline. Restarting the service is running the
    command again against the same ``--continuity-db``.
    """
    _require(args, ("continuity-tenant", "continuity-deployment", "continuity-collector", "continuity-heartbeat-at"))
    if args.continuity_batch_seq is None:
        raise SystemExit("missing required options: --continuity-batch-seq")
    if not isinstance(payload, dict):
        raise SystemExit("continuity ingest requires a JSON receiver payload")
    runtime = PersistentContinuityRuntime(args.continuity_db, enabled=args.enable_continuity_runtime)
    try:
        result = runtime.ingest_signed_batch(
            payload,
            jwt_keyring=keyring,
            tenant_id=args.continuity_tenant,
            deployment_id=args.continuity_deployment,
            collector_id=args.continuity_collector,
            batch_seq=args.continuity_batch_seq,
            heartbeat_at=args.continuity_heartbeat_at,
        )
    except RuntimeContinuityError as exc:
        raise SystemExit(f"continuity_refused={exc}")
    finally:
        runtime.close()
    print(f"batch_accepted={result['batch_seq']} evidence_ref={result['evidence_ref']}")


def _run_continuity_decision(args: argparse.Namespace) -> None:
    """Evaluate attach/renew eligibility from persisted continuity state."""
    _require(args, ("continuity-db", "continuity-tenant", "continuity-agent",
                    "continuity-deployment", "continuity-collector"))
    runtime = PersistentContinuityRuntime(args.continuity_db, enabled=args.enable_continuity_runtime)
    try:
        decision = runtime.decide(
            action_class=args.continuity_decide,
            tenant_id=args.continuity_tenant,
            agent_id=args.continuity_agent,
            deployment_id=args.continuity_deployment,
            collector_id=args.continuity_collector,
            evaluated_at=args.continuity_evaluated_at or _utc_now(),
            heartbeat_max_age_seconds=args.continuity_heartbeat_max_age_seconds,
        )
    except RuntimeContinuityError as exc:
        raise SystemExit(f"continuity_refused={exc}")
    finally:
        runtime.close()
    print(json.dumps(decision, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    if not decision["permitted"]:
        raise SystemExit(CONTINUITY_REFUSED_EXIT)


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser(prog="post-bind-ingest")
    parser.add_argument(
        "payload_path",
        nargs="?",
        help="Path to manifest or receiver payload JSON. Omitted only with --continuity-decide.",
    )
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
    parser.add_argument(
        "--continuity-db",
        help="Vendor-local SQLite path holding persistent continuity state. Receiver mode only. "
             "Production receiver invocations must set this option and --enable-continuity-runtime; "
             "omission is retained only for local compatibility/testing.",
    )
    parser.add_argument(
        "--enable-continuity-runtime",
        action="store_true",
        default=False,
        help="Arm the persistent continuity runtime. Off by default; while off the runtime refuses "
             "both ingest and eligibility decisions.",
    )
    parser.add_argument("--continuity-tenant", help="Tenant id for the continuity stream identity")
    parser.add_argument("--continuity-agent", help="Agent id for --continuity-decide")
    parser.add_argument("--continuity-deployment", help="Deployment id for the continuity stream identity")
    parser.add_argument("--continuity-collector", help="Collector id for the continuity stream identity")
    parser.add_argument("--continuity-batch-seq", type=int, help="Monotonic batch sequence for this signed batch")
    parser.add_argument("--continuity-heartbeat-at", help="Heartbeat timestamp carried by this signed batch")
    parser.add_argument(
        "--continuity-decide",
        choices=("attach", "renew"),
        help="Evaluate attach/renew eligibility against persisted continuity state and exit "
             f"{CONTINUITY_REFUSED_EXIT} when refused. No payload is read.",
    )
    parser.add_argument("--continuity-evaluated-at", help="Evaluation timestamp for --continuity-decide")
    parser.add_argument(
        "--continuity-heartbeat-max-age-seconds",
        type=int,
        default=6 * 60 * 60,
        help="Heartbeat staleness threshold for --continuity-decide",
    )
    args = parser.parse_args()

    if args.continuity_decide:
        _run_continuity_decision(args)
        return

    if not args.payload_path:
        raise SystemExit("payload_path is required unless --continuity-decide is used")

    receiver_mode = args.mode == "receiver" or bool(args.jwt_keyring_json)
    if args.continuity_db and not receiver_mode:
        raise SystemExit("continuity-db requires receiver mode")
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
        if args.continuity_db:
            _run_continuity_ingest(args, payload, keyring)
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
