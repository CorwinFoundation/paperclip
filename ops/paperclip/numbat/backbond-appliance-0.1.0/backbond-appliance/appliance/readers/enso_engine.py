"""Enso-engine reader — the first partner substrate.

Reads Enso's guided-agent action events from the engine's own activity output
(a directory of per-run JSON files, or an NDJSON activity log). The partner
instruments nothing; this reader adapts to what the Enso engine already writes.

Enso runs agents on behalf of MANY SMB customers, so every event carries the
end-customer identity — this reader preserves the (tenant, agent) attribution
that the tenancy layer downstream depends on.

NOTE: this reader is written against a synthetic Enso event shape for pre-partner
development. The exact field mapping is finalized with Enso during co-design; the
shape here is the agreed contract placeholder.
"""
from __future__ import annotations
import json, os, glob
from typing import Iterator, Dict, Any
from .base import Reader, register


@register("enso_engine")
class EnsoEngineReader(Reader):
    def _root(self) -> str:
        return os.path.expanduser(self.config.get("path", ""))

    def discover(self) -> bool:
        root = self._root()
        if not root or not os.path.isdir(root):
            return False
        # loud discovery: there must actually be event files, else caller escalates
        return bool(glob.glob(os.path.join(root, "**", "*.json"), recursive=True))

    def read(self) -> Iterator[Dict[str, Any]]:
        root = self._root()
        for fp in sorted(glob.glob(os.path.join(root, "**", "*.json"), recursive=True)):
            try:
                with open(fp, "r", errors="replace") as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            events = doc if isinstance(doc, list) else doc.get("events", [doc])
            for ev in events:
                if isinstance(ev, dict):
                    ev.setdefault("_source_file", os.path.basename(fp))
                    yield ev
