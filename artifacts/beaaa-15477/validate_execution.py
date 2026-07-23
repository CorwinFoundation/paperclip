#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
source = json.loads((ROOT / "artifacts/beaaa-15476/batch1_activation_manifest.json").read_text())
summary = json.loads((ROOT / "artifacts/beaaa-15477/execution_manifest.json").read_text())
assert summary["selected"] == summary["attempted"] == summary["terminal"] == summary["evidenced"] == 90
assert summary["target_calls"] == sum(x["target_call_count"] for x in source["cases"]) == 112
assert summary["judge_calls"] == 270 <= 500
assert len(summary["evidence_refs"]) == len(set(summary["evidence_refs"])) == 90
for ref in summary["evidence_refs"]:
    evidence = json.loads((ROOT / ref).read_text())
    assert evidence["terminal"] and evidence["paired_turns_complete"]
    assert not evidence["fixture_generated_target_response"]
    assert len(evidence["request_hashes"]) == len(evidence["response_hashes"])
    assert len(evidence["judge_votes"]) == 3
    assert evidence["judge_model"] == "anthropic:claude-sonnet-4-6"
    assert evidence["judge_temperature"] == 0
    assert evidence["source_manifest_sha256"] == summary["source_manifest_sha256"]
assert summary["promotion_authorized"] is False
print("PASS: 90 selected/attempted/terminal/reachable; 112 target; 270 judge; no fixture responses; no promotion")
print("execution_manifest_sha256=" + hashlib.sha256((ROOT / "artifacts/beaaa-15477/execution_manifest.json").read_bytes()).hexdigest())
