"""Config load with sane defaults. Auto-discovery is allowed, but a missing
substrate fails LOUDLY at run time (see main.cmd_run) — never a silent no-op."""
from __future__ import annotations
import os, yaml
from typing import Dict, Any

DEFAULTS: Dict[str, Any] = {
    "appliance_id": "backbond-appliance-unset",
    "evidence_tier": "log-inferred",
    "reader": {"name": "enso_engine", "path": ""},
    "rules": {"pack": "growth_agent.cel.yaml", "allowlist": None},
    "signing": {"key_path": "~/.backbond/appliance.key", "key_id": "appliance-key-1"},
    "state_dir": "~/.backbond/state",
    "ship": {
        "mode": "outbox", "outbox_dir": "~/.backbond/outbox", "url": "",
        "max_events_per_batch": 5000,   # P0-1 windowing
        "gzip": True, "retries": 4, "backoff_base_sec": 0.5, "timeout_sec": 30,  # P0-4
    },
    "producer": {   # contract-v1.0 pin set
        "collector_version": "appliance-0.1.0",
        "evidence_profile_version": "1.0",
        "scanner_version": "na-appliance",
        "corpus_version": "na-appliance",
        "rule_pack_version": "0.1",
        "seed": 0,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config(path: str) -> Dict[str, Any]:
    loaded = {}
    if path and os.path.exists(path):
        with open(path) as fh:
            loaded = yaml.safe_load(fh) or {}
    return _merge(DEFAULTS, loaded)
