#!/usr/bin/env python3
"""Apply or remove the Handoff Capsule v1 instruction block company-wide."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


API = "http://127.0.0.1:3100"
BEGIN = "<!-- PAPERCLIP-HANDOFF-CAPSULE-V1:BEGIN -->"
END = "<!-- PAPERCLIP-HANDOFF-CAPSULE-V1:END -->"


def api(
    base: str,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    *,
    api_key: str = "",
    run_id: str = "",
) -> object:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if run_id:
        headers["X-Paperclip-Run-Id"] = run_id
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def remove_block(content: str) -> str:
    start = content.find(BEGIN)
    if start < 0:
        return content
    finish = content.find(END, start)
    if finish < 0:
        raise RuntimeError("Handoff Capsule instruction block has a begin marker but no end marker")
    finish += len(END)
    return (content[:start].rstrip() + "\n\n" + content[finish:].lstrip()).lstrip("\n")


def managed_agents(base: str, company_id: str, *, api_key: str = "", run_id: str = "") -> list[dict[str, object]]:
    result = api(
        base,
        "GET",
        f"/api/companies/{company_id}/agents",
        api_key=api_key,
        run_id=run_id,
    )
    if not isinstance(result, list):
        raise RuntimeError("agent API did not return a list")
    return [
        agent for agent in result
        if isinstance(agent, dict)
        and agent.get("status") != "terminated"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amendment", required=True, type=Path)
    parser.add_argument("--api-url", default=os.environ.get("PAPERCLIP_API_URL", API))
    parser.add_argument("--company-id", default=os.environ.get("PAPERCLIP_COMPANY_ID"))
    parser.add_argument("--api-key", default=os.environ.get("PAPERCLIP_API_KEY", ""))
    parser.add_argument("--run-id", default=os.environ.get("PAPERCLIP_RUN_ID", ""))
    parser.add_argument("--backup-dir", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="persist instruction changes (default is dry run)")
    mode.add_argument("--dry-run", action="store_true", help="report changes without persisting them (default)")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)
    company_id = str(args.company_id or "").strip()
    if not company_id:
        raise SystemExit("--company-id or PAPERCLIP_COMPANY_ID is required")

    amendment = args.amendment.read_text(encoding="utf-8").strip() + "\n"
    if BEGIN not in amendment or END not in amendment:
        raise SystemExit("amendment is missing begin/end markers")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = args.backup_dir or args.amendment.parent / "backups" / stamp
    results = []
    for agent in sorted(
        managed_agents(args.api_url, company_id, api_key=args.api_key, run_id=args.run_id),
        key=lambda item: str(item.get("name") or ""),
    ):
        agent_id = str(agent["id"])
        query = urllib.parse.urlencode({"path": "AGENTS.md"})
        try:
            bundle = api(
                args.api_url,
                "GET",
                f"/api/agents/{agent_id}/instructions-bundle/file?{query}",
                api_key=args.api_key,
                run_id=args.run_id,
            )
        except urllib.error.HTTPError as exc:
            results.append({"name": agent.get("name"), "agent_id": agent_id, "result": f"skip:{exc.code}"})
            continue
        if not isinstance(bundle, dict):
            results.append({"name": agent.get("name"), "agent_id": agent_id, "result": "skip:invalid-bundle"})
            continue
        current = str(bundle.get("content") or "")
        without = remove_block(current)
        updated = without if args.remove else amendment.rstrip() + "\n\n" + without.lstrip()
        if updated == current:
            result = "present" if not args.remove else "absent"
        elif not args.apply:
            result = "would-remove" if args.remove else "would-update"
        else:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f"{agent_id}.AGENTS.md").write_text(current, encoding="utf-8", newline="\n")
            api(
                args.api_url,
                "PUT",
                f"/api/agents/{agent_id}/instructions-bundle/file",
                {"path": "AGENTS.md", "content": updated},
                api_key=args.api_key,
                run_id=args.run_id,
            )
            result = "removed" if args.remove else "updated"
        results.append({"name": agent.get("name"), "agent_id": agent_id, "result": result})
    print(json.dumps({"backup_dir": str(backup_dir), "agents": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
