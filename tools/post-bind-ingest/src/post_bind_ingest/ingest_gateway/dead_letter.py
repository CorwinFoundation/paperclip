"""Dead-letter queue for permanent ingest failures.

Captures manifests rejected by signature verification, schema validation, or
schema-version drift (Schema §Cross-event invariant 5). Stores enough context
to re-investigate without retrying: original manifest body, failure reason,
and the canonical-JSON SHA the gateway computed at receipt.

Production deployment is a dedicated SQS / SNS surface (Phase 2.0.1). The MVP
substrate is local-FS JSONL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DeadLetterQueue:
    queue_path: Path

    def __post_init__(self) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        *,
        reason: str,
        detail: str,
        manifest_sha: str | None,
        manifest_body: Any,
        signature_alg: str | None = None,
    ) -> None:
        record = {
            "reason": reason,
            "detail": detail,
            "manifest_sha": manifest_sha,
            "manifest_body": manifest_body,
            "signature_alg": signature_alg,
        }
        with self.queue_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            fh.write("\n")

    def entries(self) -> list[dict]:
        if not self.queue_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.queue_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
