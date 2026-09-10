"""Export the P5 untrusted-content marker matches as a readable, redacted record.

The rerun recorded in ``artifacts/BEAAA-31515`` reported a match count and pointed at
a 55 MB JSON dump that was never retained. This module produces the small artifact
that count should have come with: one row per matching source event, carrying the
pattern that fired, the surrounding output, the originating tool call, and the
source reference, with credential-shaped text redacted.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .codex_history_adapter import iter_sqlite_events
from .p5_detector import _INJECTION_PATTERNS

CONTEXT_CHARS = 200

_REDACTIONS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, flags=re.IGNORECASE))
    for label, pattern in (
        ("openai_key", r"\bsk-[A-Za-z0-9_\-]{16,}"),
        ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
        ("aws_key", r"\bAKIA[0-9A-Z]{12,}"),
        ("bearer", r"\bBearer\s+[A-Za-z0-9._\-]{12,}"),
        ("assigned_secret", r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[=:]\s*\S{8,}"),
        ("jwt", r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
        ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    )
)


def redact(text: str) -> str:
    for label, pattern in _REDACTIONS:
        text = pattern.sub(f"[REDACTED:{label}]", text)
    return text


def _window(text: str, start: int, end: int) -> str:
    half = CONTEXT_CHARS // 2
    left = max(0, start - half)
    right = min(len(text), end + half)
    snippet = text[left:right]
    if left:
        snippet = "..." + snippet
    if right < len(text):
        snippet = snippet + "..."
    return snippet


def match_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per matching ``tool_source_reference`` event, joined to its tool call."""
    calls = {event["event_id"]: event for event in events if event["event_type"] == "tool_call"}
    rows: list[dict[str, Any]] = []
    for event in events:
        if event["event_type"] != "tool_source_reference":
            continue
        content = event["payload"].get("content")
        if not isinstance(content, str):
            continue
        hits = []
        for label, pattern in _INJECTION_PATTERNS:
            for found in pattern.finditer(content):
                hits.append(
                    {
                        "pattern": label,
                        "matched_text": redact(found.group(0)),
                        "offset": found.start(),
                        "context": redact(_window(content, found.start(), found.end())),
                    }
                )
        if not hits:
            continue
        call = calls.get(event["payload"].get("tool_call_event_id"), {})
        arguments = call.get("payload", {}).get("arguments", {})
        rows.append(
            {
                "source_event_id": event["event_id"],
                "tool_call_event_id": event["payload"].get("tool_call_event_id"),
                "logged_at": event["logged_at"],
                "trust": event["payload"].get("trust"),
                "source_type": event["payload"].get("source_type"),
                "patterns": sorted({hit["pattern"] for hit in hits}),
                "output_chars": len(content),
                "command": redact(str(arguments.get("command", ""))),
                "cwd": arguments.get("cwd"),
                "status": call.get("payload", {}).get("status"),
                "hits": hits,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--output", required=True, help="JSONL destination for the match rows")
    args = parser.parse_args()

    rows = match_rows(list(iter_sqlite_events(args.database)))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    counts: dict[str, int] = {}
    for row in rows:
        for pattern in row["patterns"]:
            counts[pattern] = counts.get(pattern, 0) + 1
    print(json.dumps({"matched_source_events": len(rows), "by_pattern": counts}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
