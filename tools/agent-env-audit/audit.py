#!/usr/bin/env python3
"""Backbond CTO guardrail: detect any claude_local agent whose adapterConfig.env
overrides ANTHROPIC_API_KEY (which silently bypasses host Max OAuth and routes
traffic to Console PAYG). Also cross-checks /proc for currently-running claude
processes whose ANTHROPIC_API_KEY is actually set.

Origin: BEAAA-5323 (Founder chose option B + guardrails). Run from CTO heartbeat
or daily routine. Writes a markdown report to tools/agent-env-audit/reports/.

Exit codes:
  0  no leak (registry clean, no running claude process with env API key)
  1  leak detected (registry or /proc)
  2  cannot reach Paperclip API
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

API = os.environ.get("PAPERCLIP_API_URL", "http://127.0.0.1:3100")
COMPANY = os.environ.get("PAPERCLIP_COMPANY_ID", "59f8876e-e729-4dda-98f9-1317c2b50492")
REPORT_DIR = pathlib.Path(__file__).parent / "reports"
LEAK_KEY = "ANTHROPIC_API_KEY"
PROC_DIR = pathlib.Path(os.environ.get("PAPERCLIP_AGENT_ENV_AUDIT_PROC_DIR", "/proc"))


def _get(path: str) -> object:
    req = urllib.request.Request(f"{API}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def fetch_agents() -> list[dict]:
    d = _get(f"/api/companies/{COMPANY}/agents")
    return d if isinstance(d, list) else d.get("items") or d.get("agents") or []


def env_keys(a: dict) -> list[str]:
    env = (a.get("adapterConfig") or {}).get("env") or {}
    return sorted(env.keys())


def scan_registry(agents: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (claude_local_agents_with_env_summary, leaks)."""
    rows = []
    leaks = []
    for a in agents:
        if a.get("adapterType") != "claude_local":
            continue
        keys = env_keys(a)
        row = {
            "id": a["id"],
            "name": a.get("name") or a.get("role") or a.get("title") or "?",
            "env_keys": keys,
            "leak": LEAK_KEY in keys,
            "monthly_spent_cents": a.get("spentMonthlyCents"),
            "status": a.get("status"),
        }
        rows.append(row)
        if row["leak"]:
            leaks.append(row)
    return rows, leaks


def scan_proc() -> list[dict]:
    """Walk /proc for live `claude` processes and report which have
    ANTHROPIC_API_KEY set in env. Quiet if /proc isn't usable."""
    findings = []
    proc = PROC_DIR
    if not proc.is_dir():
        return findings
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = entry.name
        try:
            cmd = (entry / "comm").read_text().strip()
            if cmd != "claude":
                continue
            environ_raw = (entry / "environ").read_bytes()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        env = {}
        for chunk in environ_raw.split(b"\x00"):
            if not chunk or b"=" not in chunk:
                continue
            k, _, v = chunk.partition(b"=")
            try:
                env[k.decode()] = v.decode()
            except UnicodeDecodeError:
                continue
        has_key = bool(env.get(LEAK_KEY, "").strip())
        findings.append({
            "pid": int(pid),
            "leak": has_key,
            "agent_id_hint": env.get("PAPERCLIP_AGENT_ID", ""),
        })
    return findings


def render_markdown(rows: list[dict], leaks: list[dict], proc_findings: list[dict]) -> str:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = [f"# Agent ENV audit — {ts}", ""]
    out += [
        "Detects any `claude_local` agent whose `adapterConfig.env` carries",
        f"`{LEAK_KEY}` — that override silently bypasses host Max OAuth and routes",
        "traffic to Anthropic Console PAYG. Source: BEAAA-5323.", "",
        "## Registry scan",
        "",
        "| Agent | id (short) | env keys | leak? | MTD cents | status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        out.append(
            f"| {r['name']} | `{r['id'][:8]}` | `{', '.join(r['env_keys']) or '∅'}` | "
            f"{'**YES**' if r['leak'] else 'no'} | {r['monthly_spent_cents']} | {r['status']} |"
        )
    out += ["", "## /proc scan (live `claude` processes on this VM)", ""]
    if not proc_findings:
        out.append("_No live `claude` processes visible (or /proc not readable)._")
    else:
        out += ["| pid | agent (env hint) | leak? |", "| --- | --- | --- |"]
        for f in proc_findings:
            out.append(
                f"| {f['pid']} | `{f['agent_id_hint'][:8] or '∅'}` | "
                f"{'**YES**' if f['leak'] else 'no'} |"
            )
    out += ["", "## Verdict", ""]
    if leaks or any(f["leak"] for f in proc_findings):
        out.append("**LEAK DETECTED.** PATCH `/api/agents/{id}` adapterConfig.env minus "
                   f"`{LEAK_KEY}`. See `tools/agent-env-audit/README.md` for remediation.")
    else:
        out.append("Registry clean. No live `claude` process carries the override.")
    return "\n".join(out) + "\n"


def main() -> int:
    try:
        agents = fetch_agents()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"audit: cannot reach Paperclip API at {API}: {e}", file=sys.stderr)
        return 2
    rows, leaks = scan_registry(agents)
    proc_findings = scan_proc()
    md = render_markdown(rows, leaks, proc_findings)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_path = REPORT_DIR / f"{stamp}.md"
    out_path.write_text(md)
    print(md)
    print(f"# report: {out_path}", file=sys.stderr)
    if leaks or any(f["leak"] for f in proc_findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
