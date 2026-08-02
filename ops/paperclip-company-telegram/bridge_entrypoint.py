#!/usr/bin/env python3
"""Runtime entrypoint that avoids rewriting unchanged bridge state."""

from __future__ import annotations

import hashlib
import json

import bridge


_original_save_state = bridge.save_state
_last_digest: bytes | None = None


def save_state_when_changed(state: dict) -> None:
    global _last_digest
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    digest = hashlib.sha256(encoded).digest()
    if digest == _last_digest:
        return
    _original_save_state(state)
    _last_digest = digest


bridge.save_state = save_state_when_changed


if __name__ == "__main__":
    bridge.run()
