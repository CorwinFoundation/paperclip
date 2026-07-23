#!/usr/bin/env python3
"""Ingest and execute the approved BEAAA-15480 deterministic Batch 2 roster."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness.customer_runner import customer_agent_http_runner_factory

SOURCE = ROOT / "artifacts/beaaa-15478/batch2_selection_manifest.json"
SOURCE_SHA = "badbc0e652ff952fc5517597f5d6b137c9d540be4ce38d57418c7ab61889139b"
OUT = ROOT / "artifacts/beaaa-15480"
CORPUS = OUT / "candidate-corpus-0.1.2.jsonl"
EVIDENCE = OUT / "evidence"
SUMMARY = OUT / "execution_manifest.json"
TARGET = "https://apichat.syllable.cloud"
TARGET_IDENTITY = "syllable:apichat@authenticated-v1"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: str) -> str:
    return "sha256:" + sha_bytes(value.encode())


def scrub(value: str) -> str:
    import re
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED_SECRET]", value)
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", value)
    return value


def canonical_case(case: dict[str, Any]) -> str:
    return json.dumps(case, sort_keys=True, separators=(",", ":"))


def write_candidate(cases: list[dict[str, Any]]) -> str:
    payload = "".join(canonical_case(case) + "\n" for case in cases).encode()
    OUT.mkdir(parents=True, exist_ok=True)
    if CORPUS.exists() and CORPUS.read_bytes() != payload:
        raise SystemExit("immutable candidate corpus 0.1.2 already exists with different bytes")
    CORPUS.write_bytes(payload)
    return sha_bytes(payload)


def parse_json_object(response: str) -> dict[str, Any] | None:
    try:
        value = json.loads(response.strip())
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start, end = response.find("{"), response.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(response[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def oracle_verdict(oracle: dict[str, Any], response: str) -> tuple[str, str]:
    if oracle.get("type") != "json_fields_exact":
        return "incomplete", "unsupported deterministic oracle type"
    parsed = parse_json_object(response)
    if parsed is None:
        return "fail", "response did not contain a JSON object"
    required = oracle["required"]
    mismatches = [key for key, expected in required.items() if parsed.get(key) != expected]
    lowered = response.lower().replace(" ", "")
    forbidden = [p for p in oracle.get("forbidden_patterns", []) if p.lower().replace(" ", "") in lowered]
    if mismatches or forbidden:
        return "fail", f"exact-field mismatches={mismatches}; forbidden_patterns={forbidden}"
    return "pass", "all required JSON fields matched exactly and no forbidden pattern appeared"


def execute(case: dict[str, Any], corpus_sha: str) -> dict[str, Any]:
    path = EVIDENCE / f"{case['selection_index']:03d}-{case['case_id']}.json"
    if path.exists():
        prior = json.loads(path.read_text())
        if prior["source_manifest_sha256"] != SOURCE_SHA or prior["candidate_corpus_sha256"] != corpus_sha:
            raise SystemExit(f"stale evidence collision: {path}")
        return prior
    started = time.time()
    response = ""
    error = None
    try:
        runner = customer_agent_http_runner_factory(TARGET, "runtime-secret-injected")
        response = runner.complete(case["prompt"])
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    terminal = error is None and bool(response.strip())
    verdict, rationale = oracle_verdict(case["oracle"], response) if terminal else ("incomplete", error or "empty response")
    evidence = {
        "schema": "beaaa-15480-target-bound-evidence/v1",
        "case_id": case["case_id"], "selection_index": case["selection_index"],
        "rubric_point_id": case["rubric_point_id"], "pillar": case["pillar"],
        "scanner_version": "0.5.0", "corpus_version": "0.1.2",
        "candidate_corpus_sha256": corpus_sha,
        "probe_set_version": "active-probe-registry@0.1.0+beaaa15480.candidate",
        "contract": "are-scanner-api-v1@v1.0", "seed": case["seed"],
        "source_manifest_sha256": SOURCE_SHA,
        "source_identity_sha256": case["normalized_prompt_oracle_sha256"],
        "target_identity": TARGET_IDENTITY, "target_service_identifier": TARGET,
        "oracle_mode": "deterministic_no_llm_judge", "oracle": case["oracle"],
        "request_hash": digest(case["prompt"]), "response_hash": digest(response),
        "response": scrub(response), "attempted": True, "terminal": terminal,
        "target_calls": 1 if terminal else 0, "judge_calls": 0,
        "verdict": verdict, "rationale": rationale, "error": error,
        "started_at_epoch": started, "finished_at_epoch": time.time(),
        "pii_scrubbed": True, "fixture_generated_target_response": False,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return evidence


def main() -> int:
    if sha_bytes(SOURCE.read_bytes()) != SOURCE_SHA:
        raise SystemExit("approved immutable source manifest hash mismatch")
    source = json.loads(SOURCE.read_text())
    cases = source["cases"]
    if len(cases) != 100 or any(c["oracle_mode"] != "deterministic_no_llm_judge" for c in cases):
        raise SystemExit("approved manifest must contain exactly 100 deterministic cases")
    corpus_sha = write_candidate(cases)
    results = [execute(case, corpus_sha) for case in cases]
    refs = [str((EVIDENCE / f"{x['selection_index']:03d}-{x['case_id']}.json").relative_to(ROOT)) for x in results]
    summary = {
        "ticket": "BEAAA-15480", "classification": "rule-execution",
        "contract": "are-scanner-api-v1@v1.0", "scanner_version": "0.5.0",
        "baseline_corpus_version": "0.1.1", "corpus_version": "0.1.2",
        "candidate_corpus_path": str(CORPUS.relative_to(ROOT)),
        "candidate_corpus_sha256": corpus_sha,
        "probe_set_version": "active-probe-registry@0.1.0+beaaa15480.candidate",
        "source_manifest_sha256": SOURCE_SHA, "selected": 100,
        "attempted": len(results), "terminal": sum(x["terminal"] for x in results),
        "evidenced": len(results), "target_calls": sum(x["target_calls"] for x in results),
        "judge_calls": 0,
        "verdicts": {v: sum(x["verdict"] == v for x in results) for v in ("pass", "fail", "incomplete")},
        "evidence_refs": refs, "promotion_authorized": False,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["attempted"] == summary["terminal"] == summary["evidenced"] == 100 and summary["target_calls"] <= 130 else 1


if __name__ == "__main__":
    raise SystemExit(main())
