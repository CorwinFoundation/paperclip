#!/usr/bin/env python3
"""Offline case-level reconciliation for BEAAA-39144 Vapi evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


ID_KEYS = ("stable_case_id", "case_id", "test_case_id")
JUDGEMENT_KEYS = ("verdict", "judgement", "judgment", "result")


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


def is_judged(record: dict[str, Any]) -> bool:
    for key in JUDGEMENT_KEYS:
        value = record.get(key)
        if value is not None and value != "":
            return True
    return False


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
    evidence_files = sorted(
        p for p in args.evidence.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl"}
    )
    for path in evidence_files:
        for row in load(path):
            cid = case_id(row)
            if cid:
                acquired.add(cid)
                if is_judged(row):
                    judged.add(cid)

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
        },
        "missing_acquisition": sorted(inventory - acquired),
        "acquired_not_judged": sorted((inventory & acquired) - judged),
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
