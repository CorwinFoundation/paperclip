"""Capture adapters for the E2E harness (BEAAA-7427)."""
from __future__ import annotations

from .base import CaptureAdapter, CapturedSession, session_to_manifest, session_to_events
from .fixture import FixtureAdapter
from .retell import RetellAdapter
from .voiceflow import VoiceflowAdapter
from .vapi import VapiAdapter

#: Pre-built top-3 self-serve CX AI vendor adapters + the fixture adapter.
ADAPTERS = {
    "fixture": FixtureAdapter,
    "retell": RetellAdapter,
    "voiceflow": VoiceflowAdapter,
    "vapi": VapiAdapter,
}


def get_adapter(vendor: str) -> CaptureAdapter:
    try:
        return ADAPTERS[vendor]()
    except KeyError as exc:
        raise KeyError(
            f"unknown vendor {vendor!r}; available: {sorted(ADAPTERS)}"
        ) from exc


__all__ = [
    "CaptureAdapter",
    "CapturedSession",
    "session_to_manifest",
    "session_to_events",
    "FixtureAdapter",
    "RetellAdapter",
    "VoiceflowAdapter",
    "VapiAdapter",
    "ADAPTERS",
    "get_adapter",
]
