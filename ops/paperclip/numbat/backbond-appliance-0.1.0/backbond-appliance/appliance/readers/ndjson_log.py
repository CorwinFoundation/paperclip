"""Generic NDJSON log reader — tails a newline-delimited JSON log the partner
already writes. The most common zero-instrumentation substrate."""
from __future__ import annotations
import json, os
from typing import Iterator, Dict, Any
from .base import Reader, register


@register("ndjson_log")
class NdjsonLogReader(Reader):
    def _path(self) -> str:
        return os.path.expanduser(self.config.get("path", ""))

    def discover(self) -> bool:
        p = self._path()
        return bool(p) and os.path.isfile(p)

    def read(self) -> Iterator[Dict[str, Any]]:
        p = self._path()
        with open(p, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
