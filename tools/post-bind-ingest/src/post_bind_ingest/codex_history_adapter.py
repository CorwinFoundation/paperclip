from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def _logged_at(created_at_ms: int) -> str:
    return datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def adapt_codex_item(item: dict[str, Any], *, created_at_ms: int) -> dict[str, Any] | None:
    """Map only semantically equivalent Codex history items to runtime events.

    User messages (including probe prompts and pasted request bodies) are deliberately
    excluded: they are agent inputs, not downstream tool calls or tool-source records.
    """
    item_type = item.get("type")
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None

    if item_type == "commandExecution":
        command = item.get("command")
        if not isinstance(command, str):
            return None
        payload = {
            "tool": "commandExecution",
            "arguments": {"command": command, "cwd": item.get("cwd")},
            "status": item.get("status"),
        }
        event_type = "tool_call"
    elif item_type == "agentMessage":
        text = item.get("text")
        if not isinstance(text, str):
            return None
        payload = {"content": text, "phase": item.get("phase")}
        event_type = "llm_completion"
    else:
        return None

    return {
        "event_id": f"codex-{item_id}",
        "event_type": event_type,
        "logged_at": _logged_at(created_at_ms),
        "payload": payload,
    }


def iter_sqlite_events(path: str | Path) -> Iterator[dict[str, Any]]:
    uri = f"file:{Path(path)}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT created_at_ms, item_json FROM thread_items ORDER BY created_at_ms, item_id"
        )
        for created_at_ms, raw_item in rows:
            try:
                item = json.loads(raw_item)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and (event := adapt_codex_item(item, created_at_ms=created_at_ms)):
                yield event


def inventory_sqlite(path: str | Path) -> tuple[int, list[dict[str, Any]]]:
    uri = f"file:{Path(path)}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        inventoried = connection.execute("SELECT count(*) FROM thread_items").fetchone()[0]
    return int(inventoried), list(iter_sqlite_events(path))


def _digest_events(events: Iterable[dict[str, Any]]) -> str:
    canonical = json.dumps(list(events), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P5 over a read-only Codex thread-history database")
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from .p5_detector import _find_markers, detect_p5_events

    inventoried, events = inventory_sqlite(args.database)
    candidates = [event for event in events if event["event_type"] == "tool_call"]
    matched = [event for event in candidates if _find_markers(event["payload"])]
    digest = _digest_events(events)
    findings = detect_p5_events(
        events,
        manifest_id=f"codex-history-{digest[:16]}",
        manifest_digest=digest,
        schema_version="log-event-schema-v1.0",
    )
    report = {
        "source": str(args.database),
        "records_inventoried": inventoried,
        "records_normalized": len(events),
        "p5_candidates": len(candidates),
        "injection_pattern_matches": len(matched),
        "matched_events": matched,
        "p5_findings": findings,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
