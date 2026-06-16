from __future__ import annotations

import argparse
import os
from pathlib import Path
from threading import Thread

from .observability import serve_local
from .runtime import ConnectorRuntime


def main() -> None:
    parser = argparse.ArgumentParser(prog="are-connector")
    parser.add_argument("--enroll", metavar="INVITE_TOKEN")
    parser.add_argument("--scan", metavar="SCAN_ID")
    parser.add_argument("--config", default=os.environ.get("CONNECTOR_CONFIG", "/var/lib/are-connector/config.json"))
    parser.add_argument("--portal-base-url", default=os.environ.get("PORTAL_BASE_URL"))
    parser.add_argument("--agent-endpoint", default=os.environ.get("DP_AGENT_ENDPOINT"))
    parser.add_argument("--dp-org-name", default=os.environ.get("DP_ORG_NAME", "dp-staging"))
    parser.add_argument("--accept-invite", action="store_true", default=os.environ.get("CONNECTOR_ACCEPT_INVITE", "").lower() in {"1", "true", "yes"})
    parser.add_argument("--cert-ref", default=os.environ.get("DP_CERT_REF", "connector-local-cert-ref"))
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--serve-local", action="store_true")
    parser.add_argument("--local-port", type=int, default=8765)
    args = parser.parse_args()

    if not args.portal_base_url:
        raise SystemExit("PORTAL_BASE_URL or --portal-base-url is required")
    if bool(args.enroll) == bool(args.scan):
        raise SystemExit("exactly one of --enroll or --scan is required")

    runtime = ConnectorRuntime(
        portal_base_url=args.portal_base_url,
        agent_endpoint=args.agent_endpoint,
        config_path=Path(args.config),
    )
    if args.serve_local:
        Thread(target=serve_local, args=(runtime.history, "127.0.0.1", args.local_port), daemon=True).start()

    if args.enroll:
        accept_endpoint = args.agent_endpoint if args.accept_invite else None
        config = runtime.enroll(args.enroll, args.dp_org_name, accept_endpoint_url=accept_endpoint, cert_ref=args.cert_ref)
        print(f"enrolled connector_id={config.connector_id} portal_version={config.portal_version}")
        return

    completed = runtime.run_scan(args.scan, max_tasks=args.max_tasks)
    print(f"scan_id={args.scan} envelopes_posted={completed}")


if __name__ == "__main__":
    main()
