from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_REDACTION_CLASSES = frozenset({"clear", "hash", "redact-on-receipt", "drop"})


class InvariantError(ValueError):
    """Raised when manifest-level invariants are violated."""


def canonical_dumps(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def normalize_redaction_class(value: Any) -> str:
    if value is None:
        raise ValueError("redaction class is required")
    if not isinstance(value, str):
        raise ValueError("redaction class must be a string")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("redaction class must be non-empty")
    if normalized not in ALLOWED_REDACTION_CLASSES:
        raise ValueError(f"unsupported redaction class: {value!r}")
    return normalized


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ManifestIdempotencyStore:
    """Tracks manifest ids and digests for replay-safe ingest."""

    path: Path | None = None
    _seen: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.path, str):
            self.path = Path(self.path)
        if self.path is None:
            self.path = None
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                manifest_id, manifest_digest = line.split("=", 1)
                self._seen[manifest_id] = manifest_digest

    def is_duplicate(self, manifest_id: str, manifest_digest: str) -> bool:
        if self.path is None:
            return manifest_id in self._seen and self._seen[manifest_id] == manifest_digest
        previous = self._seen.get(manifest_id)
        if previous is None:
            return False
        if previous != manifest_digest:
            raise InvariantError(
                f"manifest_id {manifest_id} seen with different digest previously: {previous}"
            )
        return True

    def record(self, manifest_id: str, manifest_digest: str) -> None:
        if self.is_duplicate(manifest_id, manifest_digest):
            return
        self._seen[manifest_id] = manifest_digest
        if self.path is not None:
            self.path.write_text(
                "\n".join(f"{k}={v}" for k, v in sorted(self._seen.items())),
                encoding="utf-8",
            )
