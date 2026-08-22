"""Synthetic engine compatibility reader.

This adapter consumes a synthetic technical fixture shaped as either per-run JSON
files or an NDJSON activity log. It preserves supplied tenant and agent attributes
without asserting compatibility with, endorsement by, or status of an external
counterparty. A production mapping requires a separately verified source contract.
"""
from __future__ import annotations
import json, os, glob
from typing import Iterator, Dict, Any
from .base import Reader, register


@register("synthetic_engine")
class SyntheticEngineReader(Reader):
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
