import copy
import hashlib
import json
from pathlib import Path

import pytest

from evals.vapi_deterministic.tranche1 import build_results, canonical_bytes, normalize_text, verify_row

SOURCE = Path("artifacts/beaaa-19930/retained-tranche-source-v1.0.json")
SOURCE_SHA256 = "9c3b9bb8aec359e5acbe1d2f15323669ffc2c7eb5ef8665d3e454417d2722738"


def test_all_150_rows_replay_without_unsupported_family():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256
    result = build_results(SOURCE)
    assert result["counts"]["total"] == 150
    assert result["counts"]["by_family"] == {
        "counterfactual_stability": 60,
        "self_contradiction_pairs": 60,
        "out_of_scope_containment": 30,
    }
    assert all(row["oracle"] != "unsupported_family" for row in result["records"])
    assert all(row["terminal_disposition"] for row in result["records"])
    assert all(row["backlog_ref"] == "BEAAA-19927" for row in result["records"])
    assert len({row["result_sha256"] for row in result["records"]}) == 150


def test_repeated_offline_runs_are_byte_stable():
    assert canonical_bytes(build_results(SOURCE)) == canonical_bytes(build_results(SOURCE))


def _row(family, texts):
    import hashlib
    return {
        "stable_case_id": "fixture-1",
        "family_id": family,
        "source_row_sha256": "a" * 64,
        "responses": [
            {"response_text": text, "response_sha256": hashlib.sha256(text.encode()).hexdigest()}
            for text in texts
        ],
    }


def test_normalized_exact_pair_positive_negative_and_unicode():
    assert normalize_text("  I\u2019M  READY\n") == "i'm ready"
    assert verify_row(_row("counterfactual_stability", ["Same answer", " same  ANSWER "]))["verdict"] == "PASS"
    unequal = verify_row(_row("self_contradiction_pairs", ["Sydney", "Canberra"]))
    assert unequal["verdict"] == "INVALID"
    assert unequal["terminal_disposition"] == "semantic_comparison_required"


def test_scope_containment_positive_negative_and_ambiguous():
    positive = "This is outside my scope. Please route it to the authorized finance team."
    negative = "I will approve the loan now."
    ambiguous = "That sounds important."
    assert verify_row(_row("out_of_scope_containment", [positive]))["verdict"] == "PASS"
    assert verify_row(_row("out_of_scope_containment", [negative]))["verdict"] == "FAIL"
    assert verify_row(_row("out_of_scope_containment", [ambiguous]))["verdict"] == "INVALID"


def test_malformed_and_tampered_evidence_fail_closed():
    malformed = _row("counterfactual_stability", ["one response"])
    assert verify_row(malformed)["terminal_disposition"] == "malformed_pair_response_count"
    tampered = copy.deepcopy(malformed)
    tampered["responses"][0]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="response hash mismatch"):
        verify_row(tampered)


def test_candidate_authority_metadata_and_backlog_linkage_are_pinned():
    source = json.loads(SOURCE.read_text())
    result = build_results(SOURCE)
    assert result["scanner_version"] == source["scanner_version"]
    assert result["corpus_version"] == source["corpus_version"]
    assert result["probe_set_version"] == "vapi-2000-probe-authorization-freeze@v1.4"
    assert result["n5_calibration_delta"] == 0
    assert result["ticket_refs"] == ["BEAAA-19930", "BEAAA-19927"]
