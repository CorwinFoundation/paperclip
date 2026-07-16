"""Persistence operations for canonical full-suite manifest bytes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from api.full_suite_manifest_integrity import canonical_manifest_bytes


def persist_full_suite_manifest(path: Path | str, manifest: dict[str, Any]) -> bytes:
    """Persist and return exactly the canonical bytes written to ``path``."""
    payload = canonical_manifest_bytes(manifest)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return payload
