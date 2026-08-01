"""Reader interface + registry.

A Reader turns a partner-side substrate (log file, event dir, OTLP, engine DB) into
a stream of RAW event dicts. Readers are the ONLY component that touches partner
content; everything downstream operates on redacted, normalized events.

Adding support for a new class of agent = adding one Reader. The partner instruments
nothing; the Reader adapts to what the partner already produces.
"""
from __future__ import annotations
from typing import Iterator, Dict, Any

_REGISTRY: dict[str, type["Reader"]] = {}


def register(name: str):
    def _wrap(cls):
        _REGISTRY[name] = cls
        cls.reader_name = name
        return cls
    return _wrap


def get_reader(name: str):
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown reader '{name}'. available: {sorted(_REGISTRY)}. "
            f"A non-standard substrate needs its own reader or an explicit config path."
        )
    return _REGISTRY[name]


def available() -> list[str]:
    return sorted(_REGISTRY)


class Reader:
    """Base reader. Subclasses implement discover() and read()."""
    reader_name = "base"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def discover(self) -> bool:
        """Auto-detect the substrate. Return True if found. MUST fail loudly
        (return False) rather than silently yield nothing — the caller escalates."""
        raise NotImplementedError

    def read(self) -> Iterator[Dict[str, Any]]:
        """Yield RAW event dicts from the substrate."""
        raise NotImplementedError
