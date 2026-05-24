"""Append-only JSONL audit log for license-validation calls (BEAAA-569 §6).

One line per call. Retention is 90 days (matches BEAAA-552 design-partner
horizon, ADR-0011 §6). Retention rotation is *not* implemented in the stub
— production hardening replaces this file sink with a managed log target.

Record shape::

    {
      "timestamp": "ISO-8601",
      "partner_id": "...",
      "binary_build_id": "...",
      "scan_id": "...",
      "source_ip": "...",
      "outcome": "valid | revoked | expired | partner_mismatch | binary_blacklisted | unknown_key",
      "key_id": "..."          # public-key fingerprint of the signing key
    }

No transcript content, no scored output, no PII (ADR-0011 §6).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class AuditEntry:
    timestamp: datetime
    partner_id: str
    binary_build_id: str
    scan_id: str
    source_ip: str
    outcome: str
    key_id: str


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: AuditEntry) -> None:
        line = json.dumps(
            {
                "timestamp": entry.timestamp.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "partner_id": entry.partner_id,
                "binary_build_id": entry.binary_build_id,
                "scan_id": entry.scan_id,
                "source_ip": entry.source_ip,
                "outcome": entry.outcome,
                "key_id": entry.key_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        # O_APPEND on POSIX guarantees atomic append-of-one-line for writes
        # smaller than PIPE_BUF; one JSONL line is comfortably under that
        # for any field combination the schema admits.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    def read_all(self) -> Iterable[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
