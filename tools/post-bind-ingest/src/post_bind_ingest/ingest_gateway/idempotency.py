"""`(customer_id, manifest_sha256)` idempotency store.

MVP per CTO brief §4: in-process dict + JSONL persistence under a local-FS
substrate. Production wiring (durable store, cross-region) is Phase 2.0.1.

Behavior (CTO brief §5 AC#3):
- First call to `record_and_check` for a `(customer_id, manifest_sha)` pair
  returns `False` (not previously seen) and persists the entry.
- Any subsequent call for the same pair returns `True` (already seen);
  caller's responsibility to skip ingest / emit replay-safe response.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class IdempotencyStore:
    """File-backed idempotency store. Atomic-append on `record_and_check`."""

    store_path: Path

    def __post_init__(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[tuple[str, str]] = set()
        if self.store_path.exists():
            for line in self.store_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                self._seen.add((entry["customer_id"], entry["manifest_sha"]))

    def record_and_check(self, customer_id: str, manifest_sha: str) -> bool:
        """Return True if the pair was previously recorded; else record and return False."""
        key = (customer_id, manifest_sha)
        if key in self._seen:
            return True
        self._seen.add(key)
        with self.store_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"customer_id": customer_id, "manifest_sha": manifest_sha},
                                ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            fh.write("\n")
        return False

    def has_seen(self, customer_id: str, manifest_sha: str) -> bool:
        return (customer_id, manifest_sha) in self._seen
