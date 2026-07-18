#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
summary_path = ROOT / "artifacts/beaaa-15480/execution_manifest.json"
summary = json.loads(summary_path.read_text())
assert summary["selected"] == summary["attempted"] == summary["terminal"] == summary["evidenced"] == 100
assert summary["target_calls"] == 100 <= 130
assert summary["judge_calls"] == 0 <= 120
assert summary["baseline_corpus_version"] == "0.1.1" and summary["corpus_version"] == "0.1.2"
corpus = ROOT / summary["candidate_corpus_path"]
assert hashlib.sha256(corpus.read_bytes()).hexdigest() == summary["candidate_corpus_sha256"]
assert len(summary["evidence_refs"]) == len(set(summary["evidence_refs"])) == 100
for ref in summary["evidence_refs"]:
    evidence = json.loads((ROOT / ref).read_text())
    assert evidence["attempted"] and evidence["terminal"]
    assert evidence["judge_calls"] == 0 and evidence["oracle_mode"] == "deterministic_no_llm_judge"
    assert evidence["source_manifest_sha256"] == summary["source_manifest_sha256"]
    assert evidence["candidate_corpus_sha256"] == summary["candidate_corpus_sha256"]
    assert evidence["request_hash"].startswith("sha256:") and evidence["response_hash"].startswith("sha256:")
    assert not evidence["fixture_generated_target_response"] and evidence["pii_scrubbed"]
assert summary["promotion_authorized"] is False
print("PASS: 100 selected/attempted/terminal/evidenced; 100 target; 0 judge; immutable corpus 0.1.2; no promotion")
print("execution_manifest_sha256=" + hashlib.sha256(summary_path.read_bytes()).hexdigest())
