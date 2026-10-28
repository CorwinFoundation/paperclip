"""Explicitly approved 15-minute, at-most-44-call synthetic concurrency spike."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import time

from evals.amazon_quick_assessment import load_profile
from harness.amazon_quick import _sha
from harness.amazon_quick_batch import BatchCase, PersistentBrowser, QuickBatchRunner
from tools.internal_agent_pilot import read_json, save_run
from tools.quick_assessment import preflight
from tools.quick_pilot_operations import binding
from tools.quick_qualification_probe import fixture_probe


def plan(roles, nonce, probe=None):
    """Exercise every identity, including fifteen simultaneous cases, in 44 calls."""
    if len(roles) != 3 or len(set(roles)) != 3:
        raise ValueError("three_distinct_spike_roles_required")
    waves = []
    for wave_index, (level, counts) in enumerate(((1, (1, 1, 1)), (2, (2, 2, 2)),
            (3, (3, 3, 3)), (5, (5, 5, 5)), (5, (4, 4, 3)))):
        cases, expected = [], {}
        for role_index, (role, count) in enumerate(zip(roles, counts)):
            for index in range(count):
                case_id = f"spike-{nonce}-{wave_index}-{role_index}-{index}"
                marker = f"QUICK-SPIKE-{nonce}-{wave_index}-{role_index}-{index}"
                prompt = probe.prompt(marker) if probe else f"For this connection test, reply with exactly: {marker}"
                cases.append(BatchCase(case_id, role, (prompt,)))
                expected[case_id] = probe.expected_reply if probe else marker
        waves.append((level, cases, expected))
    return waves


async def spike(scope, profile, output, receipt):
    targets, _ = preflight(scope, profile, purpose="concurrency_spike")
    start = time.monotonic()
    deadline = start + 900
    nonce = secrets.token_hex(8)
    probe = fixture_probe(profile)
    receipt["probe"] = probe.metadata
    for level, cases, expected in plan(profile["fixture"]["roles"], nonce, probe):
        if time.monotonic() >= deadline:
            raise ValueError("spike_wall_clock_budget_exhausted")
        def checkpoint(result):
            # Store private capture separately; the summary receipt has hashes.
            save_run(output / (result["case_id"] + ".private.json"), result)
        runner = QuickBatchRunner(targets, authorization=scope["authorization_id"], max_calls=scope["max_calls"],
            concurrency=level, max_wall_seconds=max(1, int(deadline - time.monotonic())),
            browser=PersistentBrowser(capture_spec=scope.get("browser_capture_spec")),
            registered_tokens={r: os.environ[scope["identities"][r]["token_env"]] for r in targets},
            on_result=checkpoint, start_together=True)
        result = await runner.run(cases)
        roles = {c.case_id: c.role for c in cases}
        prompts = {c.case_id: c.turns[0] for c in cases}
        verified = (result["initialized_together"] == len(cases)
                    and len(result["results"]) == len(cases)
                    and {r["case_id"] for r in result["results"]} == set(expected)
                    and all(r["role"] == roles[r["case_id"]]
                       and r["status"] == "observed" and len(r["turns"]) == 1
                       and r["turns"][0]["transport"].get("native_status_code") == "success"
                       and r["turns"][0]["transport"].get("prompt_sha256") == _sha(prompts[r["case_id"]])
                       and r["turns"][0]["reply"].strip() == expected[r["case_id"]] for r in result["results"])
                    )
        wave = {"concurrency": level, "submitted_case_cap": len(cases), "verified": verified,
                "case_counts_by_role": dict(Counter(c.role for c in cases)),
                "elapsed_seconds": result["elapsed_seconds"], "api_calls": result["api_calls"],
                "browser_launches": result["browser_launches"], "sweeps": result["sweeps"],
                "initialized_together": result["initialized_together"],
                "native_successful_replies": sum(r["status"] == "observed" and len(r["turns"]) == 1
                    and r["turns"][0]["transport"].get("native_status_code") == "success" for r in result["results"]),
                "mean_completion_spacing_seconds": round(result["elapsed_seconds"] / len(cases), 3),
                "evidence": [{"case_id": r["case_id"], "status": r["status"],
                              "response_sha256": r["turns"][0]["transport"]["response_sha256"] if r["status"] == "observed" else None,
                              "native_status_code": r["turns"][0]["transport"].get("native_status_code") if r["status"] == "observed" else None}
                             for r in result["results"]]}
        receipt["waves"].append(wave)
        save_run(output / "spike.json", receipt)
        if not verified or result["status"] != "capture_complete":
            raise ValueError("spike_incomplete_operator_review_required")
    receipt.update(status="concurrency_qualified", qualified_concurrency=5,
                   qualified_identities={role: {"target_binding_sha256": binding(t),
                       "pilot_id_sha256": _sha(t.pilot_id)} for role, t in targets.items()},
                   elapsed_seconds=round(time.monotonic() - start, 3),
                   finished_at=datetime.now(timezone.utc).isoformat(), target_calls=44)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--variant", choices=("hr_finance", "healthcare"), default="hr_finance")
    parser.add_argument("--profile-version", choices=("1.2", "1.3"), default="1.2")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    receipt = None
    try:
        if not args.live:
            raise ValueError("explicit_live_required")
        scope = read_json(args.scope)
        profile = load_profile(args.variant, args.profile_version)
        targets, _ = preflight(scope, profile, purpose="concurrency_spike")
        args.output.mkdir(parents=True, exist_ok=False)
        receipt = {"schema": "backbond-quick-concurrency-qualification/v1.2", "status": "incomplete",
            "probe": fixture_probe(profile).metadata,
            "source_revision": scope["release_gate"]["revision"], "authorization_id": scope["authorization_id"],
            "target_account_sha256": _sha(next(iter(targets.values())).account_id),
            "started_at": datetime.now(timezone.utc).isoformat(), "waves": [], "target_call_cap": 44,
            "judge_calls": 0, "backbond_score": "N/A"}
        save_run(args.output / "spike.json", receipt)
        asyncio.run(spike(scope, profile, args.output, receipt))
        save_run(args.output / "spike.json", receipt)
        print(json.dumps({"status": receipt["status"], "target_calls": 44, "judge_calls": 0}))
        return 0
    except (Exception, KeyboardInterrupt):
        if receipt is not None:
            receipt["status"] = "incomplete"
            save_run(args.output / "spike.json", receipt)
        print(json.dumps({"status": "incomplete", "error": "spike_stopped_operator_review_required"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
