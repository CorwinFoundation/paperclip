"""Admin CLI for the license-validation stub (BEAAA-569).

Usage examples::

    # Generate the signing keypair (run once per environment)
    python -m infra.license_validator.cli init-keys \\
        --private-key infra/license_validator/keys/private.pem \\
        --public-key  infra/license_validator/keys/public.pem

    # Issue a license to a design partner
    python -m infra.license_validator.cli issue \\
        --registry infra/license_validator/registry.json \\
        --partner-id partner-acme \\
        --binary-build-id build-2026-05-19-linux-amd64-acme \\
        --expires-at 2026-06-30T23:59:59Z

    # Revoke a license
    python -m infra.license_validator.cli revoke \\
        --registry infra/license_validator/registry.json \\
        --license-key lk_... \\
        --reason "partner offboarded"

    # Blacklist a binary build
    python -m infra.license_validator.cli blacklist-build \\
        --registry infra/license_validator/registry.json \\
        --binary-build-id build-...

    # Run the FastAPI app under uvicorn
    python -m infra.license_validator.cli serve \\
        --registry  infra/license_validator/registry.json \\
        --audit     infra/license_validator/audit.log \\
        --private-key infra/license_validator/keys/private.pem \\
        --admin-token <secret> --host 127.0.0.1 --port 8731
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .attestation import (
    generate_keypair,
    write_private_key,
    write_public_key,
)
from .registry import DEFAULT_EXPIRY, Registry


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="license-validator-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init-keys", help="generate an Ed25519 signing keypair")
    init.add_argument("--private-key", required=True, type=Path)
    init.add_argument("--public-key", required=True, type=Path)

    issue = sub.add_parser("issue", help="issue a new license key")
    issue.add_argument("--registry", required=True, type=Path)
    issue.add_argument("--partner-id", required=True)
    issue.add_argument("--binary-build-id", required=True)
    issue.add_argument(
        "--expires-at",
        default=DEFAULT_EXPIRY.isoformat().replace("+00:00", "Z"),
        help="ISO-8601 expiry (default: %(default)s)",
    )
    issue.add_argument("--license-key", default=None, help="explicit key (test only)")

    revoke = sub.add_parser("revoke", help="revoke a license key")
    revoke.add_argument("--registry", required=True, type=Path)
    revoke.add_argument("--license-key", required=True)
    revoke.add_argument("--reason", required=True)

    bl = sub.add_parser("blacklist-build", help="blacklist a binary_build_id")
    bl.add_argument("--registry", required=True, type=Path)
    bl.add_argument("--binary-build-id", required=True)

    ls = sub.add_parser("list", help="list registry entries")
    ls.add_argument("--registry", required=True, type=Path)

    serve = sub.add_parser("serve", help="run the FastAPI app under uvicorn")
    serve.add_argument("--registry", required=True, type=Path)
    serve.add_argument("--audit", required=True, type=Path)
    serve.add_argument("--private-key", required=True, type=Path)
    serve.add_argument("--admin-token", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8731, type=int)

    args = parser.parse_args(argv)

    if args.cmd == "init-keys":
        priv, pub = generate_keypair()
        args.private_key.parent.mkdir(parents=True, exist_ok=True)
        args.public_key.parent.mkdir(parents=True, exist_ok=True)
        write_private_key(priv, args.private_key)
        write_public_key(pub, args.public_key)
        print(json.dumps({
            "private_key": str(args.private_key),
            "public_key": str(args.public_key),
        }, indent=2))
        return 0

    if args.cmd == "issue":
        reg = Registry(args.registry)
        record = reg.issue(
            partner_id=args.partner_id,
            binary_build_id=args.binary_build_id,
            expires_at=_parse_iso(args.expires_at),
            license_key=args.license_key,
        )
        print(json.dumps({
            "license_key": record.license_key,
            "partner_id": record.partner_id,
            "binary_build_id": record.binary_build_id,
            "expires_at": record.expires_at.isoformat().replace("+00:00", "Z"),
            "status": record.status,
        }, indent=2))
        return 0

    if args.cmd == "revoke":
        reg = Registry(args.registry)
        try:
            record = reg.revoke(license_key=args.license_key, reason=args.reason)
        except KeyError:
            print(f"license_key {args.license_key!r} not found", file=sys.stderr)
            return 2
        print(json.dumps({
            "license_key": record.license_key,
            "status": record.status,
            "revoked_at": record.revoked_at.isoformat().replace("+00:00", "Z")
            if record.revoked_at
            else None,
            "revocation_reason": record.revocation_reason,
        }, indent=2))
        return 0

    if args.cmd == "blacklist-build":
        reg = Registry(args.registry)
        reg.blacklist_build(args.binary_build_id)
        print(json.dumps({"blacklisted_build": args.binary_build_id}, indent=2))
        return 0

    if args.cmd == "list":
        reg = Registry(args.registry)
        out = []
        for r in reg.list_keys():
            out.append({
                "license_key": r.license_key,
                "partner_id": r.partner_id,
                "binary_build_id": r.binary_build_id,
                "status": r.status,
                "expires_at": r.expires_at.isoformat().replace("+00:00", "Z"),
                "revoked_at": r.revoked_at.isoformat().replace("+00:00", "Z")
                if r.revoked_at
                else None,
            })
        print(json.dumps(out, indent=2))
        return 0

    if args.cmd == "serve":
        os.environ["IFA_LICENSE_VALIDATOR_REGISTRY"] = str(args.registry)
        os.environ["IFA_LICENSE_VALIDATOR_AUDIT_LOG"] = str(args.audit)
        os.environ["IFA_LICENSE_VALIDATOR_PRIVATE_KEY"] = str(args.private_key)
        os.environ["IFA_LICENSE_VALIDATOR_ADMIN_TOKEN"] = args.admin_token
        try:
            import uvicorn
        except ImportError:
            print("uvicorn not installed; install or invoke via your ASGI server", file=sys.stderr)
            return 2
        from .app import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    parser.error(f"unknown command {args.cmd!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
