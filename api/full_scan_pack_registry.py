"""Versioned paid-pack registry for Full Scan jobs (BEAAA-38957).

Maps ``pack_id@version`` strings to the ordered case lists they represent.
Unknown pack IDs are rejected at job-acceptance time.

Adding a new pack version is a one-line change in ``_REGISTRY`` — no other
module needs to be touched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VariablePack:
    bank_version: str


def _build_registry() -> dict[str, tuple[Any, ...] | VariablePack]:
    # Deferred import so that scanners are only loaded when the registry is
    # first accessed (not at module import time).
    from scanners.full_scan_conversation_en import CASES as CASES_V1
    from scanners.full_scan_conversation_en_s2 import CASES_S2 as CASES_V2

    return {
        # Canonical pack identifiers exposed in the public API.
        # Changing a key is a breaking change — add a new key instead.
        "full-scan-conv-en@0.2": tuple(CASES_V1),
        "full-scan-conv-en@0.3": tuple(CASES_V2),
        "full-scan-conv-en@1.0": VariablePack("full-scan-conv-en@1.0"),
    }


_registry_cache: dict[str, tuple[Any, ...] | VariablePack] | None = None


def _registry() -> dict[str, tuple[Any, ...] | VariablePack]:
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = _build_registry()
    return _registry_cache


def resolve_pack(
    pack_id: str, *, allow_legacy_replay: bool = False
) -> tuple[Any, ...] | VariablePack:
    """Resolve a current pack, or a legacy fixed pack for recorded-job replay."""
    reg = _registry()
    if pack_id not in reg:
        raise KeyError(
            f"Unknown pack_id {pack_id!r}. "
            f"Known packs: {sorted(reg)}"
        )
    pack = reg[pack_id]
    if not isinstance(pack, VariablePack) and not allow_legacy_replay:
        raise KeyError(
            f"Legacy fixed pack {pack_id!r} is replay-only; "
            "new requests must use 'full-scan-conv-en@1.0'"
        )
    return pack


def known_pack_ids() -> list[str]:
    """Return a sorted list of all registered pack ids."""
    return sorted(_registry())
