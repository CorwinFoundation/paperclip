from __future__ import annotations

from pathlib import Path

import pytest

from post_bind_ingest.invariants import InvariantError, ManifestIdempotencyStore, canonical_dumps, normalize_redaction_class


def test_canonical_dumps_is_deterministic() -> None:
    first = canonical_dumps({"b": 2, "a": 1})
    second = canonical_dumps({"a": 1, "b": 2})
    assert first == second


def test_invariant_store_rejects_digest_mismatch(tmp_path: Path) -> None:
    store = ManifestIdempotencyStore(path=tmp_path / "manifests.state")
    store.record("m-1", "a" * 64)
    with pytest.raises(InvariantError):
        store.record("m-1", "b" * 64)


def test_normalize_redaction_class_accepts_supported_classes() -> None:
    assert normalize_redaction_class("hash") == "hash"
    assert normalize_redaction_class("REDact-On-Receipt") == "redact-on-receipt"
    with pytest.raises(ValueError):
        normalize_redaction_class("unsupported")
