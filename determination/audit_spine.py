"""BEAAA-11372 determination audit spine.

The audit spine is the append-only book of record for determinations. In
production it is the Postgres table defined in
``determination/migrations/0001_determination_records_v1.sql`` (mirroring the
append-only semantics of the evidence store, BEAAA-11366/BEAAA-11369). In the
agent runtime and in tests it is a JSONL sink with the same append-only contract:
records are only ever appended, never updated or deleted in place.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from determination.records import DeterminationRecord
from determination.rule_dsl import canonical_json_bytes


class DeterminationAuditSpine(Protocol):
    def append(self, record: DeterminationRecord) -> None: ...


class InMemoryAuditSpine:
    """Append-only in-memory spine for tests and dry runs."""

    def __init__(self) -> None:
        self._records: list[DeterminationRecord] = []

    def append(self, record: DeterminationRecord) -> None:
        self._records.append(record)

    @property
    def records(self) -> tuple[DeterminationRecord, ...]:
        return tuple(self._records)


class JsonlAuditSpine:
    """Append-only JSONL spine.

    Each determination is one canonical JSON line. The file is opened in append
    mode only; there is no update or delete path, matching the evidence-store
    append-only book-of-record contract.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: DeterminationRecord) -> None:
        line = canonical_json_bytes(record.to_dict()).decode("utf-8")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


__all__ = [
    "DeterminationAuditSpine",
    "InMemoryAuditSpine",
    "JsonlAuditSpine",
]
