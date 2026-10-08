#!/usr/bin/env python3
"""Offline case-level reconciliation for BEAAA-39144 Vapi evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


ID_KEYS = ("runtime_case_identity", "probe_id", "stable_case_id", "case_id", "test_case_id")
JUDGEMENT_KEYS = ("verdict", "judgement", "judgment", "result")
TERMINAL_VERDICTS = frozenset({"PASS", "FAIL", "NOT_APPLICABLE"})
RESPONSE_KEYS = ("response", "responses", "target_response", "assistant_response", "raw_response")


def records(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for key in ("records", "cases", "results", "items"):
            child = value.get(key)
            if isinstance(child, list):
                for item in child:
                    yield from records(item)
    elif isinstance(value, list):
        for item in value:
            yield from records(item)


def load(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield from records(json.loads(line))
    else:
        yield from records(json.loads(path.read_text(encoding="utf-8")))


def case_id(record: dict[str, Any]) -> str | None:
    for key in ID_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def terminal_verdict(record: dict[str, Any]) -> str | None:
    for key in JUDGEMENT_KEYS:
        value = record.get(key)
        if isinstance(value, dict):
            value = value.get("verdict") or value.get("label") or value.get("result")
        normalized = str(value or "").strip().upper()
        if normalized in TERMINAL_VERDICTS:
            return normalized
    return None


def is_judged(record: dict[str, Any]) -> bool:
    return terminal_verdict(record) is not None


def is_not_applicable(record: dict[str, Any]) -> bool:
    return terminal_verdict(record) == "NOT_APPLICABLE"


def has_response(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(has_response(item) for item in value)
    if isinstance(value, dict):
        return any(has_response(item) for item in value.values())
    return value is not None


def is_acquired(record: dict[str, Any]) -> bool:
    """Require a real target receipt, not merely a registry-shaped case row."""
    if is_not_applicable(record):
        return False
    for envelope in (record, record.get("evidence"), record.get("receipt")):
        if not isinstance(envelope, dict):
            continue
        if any(has_response(envelope.get(key)) for key in RESPONSE_KEYS):
            return True
        messages = envelope.get("messages")
        if isinstance(messages, list) and any(
            isinstance(item, dict)
            and item.get("role") == "assistant"
            and has_response(item.get("content"))
            for item in messages
        ):
            return True
    return False


def call_count(record: dict[str, Any], key: str) -> int:
    """Read counters from both executor rows and retained raw receipt budgets."""
    candidates = [
        record.get(key),
        (record.get("budget") or {}).get("case_delta", {}).get(key)
        if isinstance(record.get("budget"), dict)
        else None,
    ]
    for value in candidates:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    inventory_ids = [cid for row in load(args.inventory) if (cid := case_id(row))]
    inventory = set(inventory_ids)
    acquired: set[str] = set()
    judged: set[str] = set()
    not_applicable: set[str] = set()
    target_called: set[str] = set()
    judge_called: set[str] = set()
    verdict_case_ids: dict[str, set[str]] = {value: set() for value in TERMINAL_VERDICTS}
    evidence_files = sorted(
        p for p in args.evidence.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl"}
    )
    for path in evidence_files:
        for row in load(path):
            cid = case_id(row)
            if cid:
                if is_acquired(row):
                    acquired.add(cid)
                if call_count(row, "target_calls") > 0:
                    target_called.add(cid)
                if call_count(row, "judge_calls") > 0:
                    judge_called.add(cid)
                verdict = terminal_verdict(row)
                if verdict:
                    judged.add(cid)
                    verdict_case_ids[verdict].add(cid)
                    if verdict == "NOT_APPLICABLE":
                        not_applicable.add(cid)

    completed = inventory & acquired & judged

    report = {
        "schema_version": "1.0.0",
        "inventory": {
            "rows_with_case_id": len(inventory_ids),
            "unique_case_ids": len(inventory),
            "duplicate_rows": len(inventory_ids) - len(inventory),
        },
        "evidence": {
            "files_read": len(evidence_files),
            "acquired_case_ids": len(acquired & inventory),
            "judged_case_ids": len(judged & inventory),
            "not_applicable_case_ids": len(not_applicable & inventory),
            "completed_case_ids": len(completed),
            "target_call_case_ids": len(target_called & inventory),
            "judge_call_case_ids": len(judge_called & inventory),
            "terminal_verdicts": {
                key.lower(): len(value & inventory)
                for key, value in sorted(verdict_case_ids.items())
            },
        },
        "missing_acquisition": sorted(inventory - acquired),
        "acquired_not_judged": sorted((inventory & acquired) - judged),
        "judged_without_acquisition": sorted((inventory & judged) - acquired - not_applicable),
        "responses_without_target_counter": sorted((inventory & acquired) - target_called),
        "target_counter_without_response": sorted((inventory & target_called) - acquired),
        "remaining_executable": sorted(inventory - completed - not_applicable),
        "evidence_not_in_inventory": sorted(acquired - inventory),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
