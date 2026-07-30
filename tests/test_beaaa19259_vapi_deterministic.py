import copy
import json
from pathlib import Path

import pytest

from evals.vapi_deterministic.verifier import build_results, canonical_bytes

SOURCE = Path("artifacts/beaaa-19220/vapi-2000-authorized-execution-evidence.json")


def test_source_fixtures_prove_all_255_verdicts_are_missing():
    records = json.loads(SOURCE.read_text())["records"]
    executed = [r for r in records if r.get("status") == "executed" and r.get("judge_mode") == "deterministic"]
    assert len(executed) == 255
    assert all("verdict" not in (r.get("deterministic_result") or {}) for r in executed)
    assert all("result_sha256" not in (r.get("deterministic_result") or {}) for r in executed)


def test_emits_canonical_hashed_result_for_every_executed_case():
    result = build_results(SOURCE)
    assert result["counts"]["executed_deterministic"] == 255
    assert len({r["stable_case_id"] for r in result["records"]}) == 255
    assert all(len(r["result_sha256"]) == 64 for r in result["records"])
    assert all(r["mapping"] or r["uncovered_dimensions"] for r in result["records"])
    assert all(r["score_input_eligible"] is False for r in result["records"])


def test_repeated_runs_are_byte_identical():
    assert canonical_bytes(build_results(SOURCE)) == canonical_bytes(build_results(SOURCE))


def test_source_response_hash_is_verified(tmp_path):
    evidence = json.loads(SOURCE.read_text())
    record = next(r for r in evidence["records"] if r.get("status") == "executed" and r.get("judge_mode") == "deterministic")
    record["responses"][0]["response_text"] += "tampered"
    changed = copy.deepcopy(evidence)
    changed["records"] = [record]
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="response hash mismatch"):
        build_results(path)
