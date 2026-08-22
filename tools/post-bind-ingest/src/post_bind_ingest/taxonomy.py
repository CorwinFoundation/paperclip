"""Additive OWASP Agentic Security Initiative views for paid perils.

The Pn identifier remains authoritative.  These labels are reported-only and
must never be imported by Scanner scoring code.
"""
from __future__ import annotations

from typing import Final


ASI_TAXONOMY_VERSION: Final = "owasp-agentic-top-10-2026"
PN_TO_ASI: Final[dict[str, tuple[str, ...]]] = {
    "P1": ("ASI02", "ASI03"),  # unauthorized money movement
    "P2": ("ASI02",),           # tool misuse
    "P3": ("ASI03",),           # data exfiltration
    "P4": ("ASI09",),           # hallucinated action / misplaced trust
    "P5": ("ASI01",),           # prompt injection / goal hijack
    "P6": ("ASI09",),           # unauthorized commitment
}


def asi_view(peril: str) -> dict[str, object]:
    """Return a deterministic additive view without replacing ``peril``."""
    try:
        identifiers = PN_TO_ASI[peril]
    except KeyError as exc:
        raise ValueError(f"unsupported paid peril: {peril!r}") from exc
    return {"taxonomy_version": ASI_TAXONOMY_VERSION, "identifiers": identifiers}
