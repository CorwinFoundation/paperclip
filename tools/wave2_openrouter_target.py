#!/usr/bin/env python3
"""BEAAA-17196 Wave 2 OpenRouter target adapter.

Reads one target request JSON from stdin and returns a transport response object
for lane_a_depth.py. The API key is read by OpenRouterRunner from the runtime
environment and is never emitted.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from evals.aiuc_path_b import lane_a_depth as lane  # noqa: E402
from harness.runner import OpenRouterRunner  # noqa: E402

DIAGNOSTIC_ENV_ALLOWLIST = (
    "OPENROUTER_API_KEY",
    "BACKBOND_WAVE2_OFFICIAL_LEASE_ROOT",
    "BACKBOND_WAVE2_AUTHORITY_KEY",
)


def environment_diagnostics() -> dict[str, str]:
    return {
        name: "[set]" if os.environ.get(name) else "[unset]"
        for name in DIAGNOSTIC_ENV_ALLOWLIST
    }


def main() -> int:
    if sys.argv[1:] == ["--diagnostics"]:
        print(json.dumps(environment_diagnostics(), sort_keys=True))
        return 0
    lease_root = os.environ.get("BACKBOND_WAVE2_OFFICIAL_LEASE_ROOT", "")
    authority_key = os.environ.get("BACKBOND_WAVE2_AUTHORITY_KEY", "")
    if lease_root != str(lane.OFFICIAL_RUN_LEASE_ROOT) or not authority_key:
        print("official Wave 2 entrypoint lease required", file=sys.stderr)
        return 78
    lease_path = (
        lane.OFFICIAL_RUN_LEASE_ROOT / "active" / authority_key / "lease.json"
    )
    try:
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("official Wave 2 lease state missing or malformed", file=sys.stderr)
        return 78
    if (
        lease.get("state") != "active"
        or lease.get("authority_key") != authority_key
        or lease.get("lease_format_version") != lane.RUN_LEASE_FORMAT_VERSION
    ):
        print("official Wave 2 lease state invalid", file=sys.stderr)
        return 78
    request = json.loads(sys.stdin.read())
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    runner = OpenRouterRunner(
        model=lane.OPENROUTER_TARGET_MODEL,
        max_tokens=192,
        temperature=0.0,
        system_prompt=lane.BOUNDED_AGENT_SYSTEM_POLICY,
    )
    raw = runner.complete(str(request["prompt"]))
    completed = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    print(
        json.dumps(
            {
                "status": 200,
                "raw_response": raw,
                "model_id": runner.model_id(),
                "system_policy_version": lane.BOUNDED_AGENT_SYSTEM_POLICY_VERSION,
                "system_policy_sha256": lane._sha256_obj(
                    {
                        "version": lane.BOUNDED_AGENT_SYSTEM_POLICY_VERSION,
                        "policy": lane.BOUNDED_AGENT_SYSTEM_POLICY,
                    }
                ),
                "started_at": started,
                "completed_at": completed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
