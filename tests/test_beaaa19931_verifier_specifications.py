import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "artifacts/beaaa-19931/vapi-invalid-family-verifier-specifications-v1.0.json"
LEDGER = ROOT / "artifacts/beaaa-19327/scoreability-ledger-v1.1"

def test_all_invalid_families_have_exactly_one_pinned_disposition():
    registry = json.loads(REGISTRY.read_text())
    ledger = json.loads(LEDGER.read_text())
    expected = {k for k, v in ledger["family_aggregation"].items() if "deterministic_invalid_unsupported_family" in v}
    entries = registry["entries"]
    assert len(expected) == len(entries) == 27
    assert {e["family_id"] for e in entries} == expected
    assert len({e["family_id"] for e in entries}) == 27
    assert all(e["disposition"] in {"pinned_verifier_spec", "terminal_reason", "delegated_pinned_spec"} for e in entries)
    assert "unsupported_family" not in REGISTRY.read_text()

def test_specs_pin_fail_closed_schema_fixtures_and_linkage():
    registry = json.loads(REGISTRY.read_text())
    for entry in registry["entries"]:
        assert entry["malformed_or_missing_evidence"].startswith("INVALID")
        assert entry["immutable_verdict_schema"]["verdict_enum"] == ["PASS", "FAIL", "INVALID"]
        assert set(entry["fixture_requirements"]) >= {"positive", "negative", "missing_response", "wrong_response_hash", "malformed_oracle"}
        assert "source_row_sha256" in entry["row_linkage"]
        assert entry["scoring_active"] is False

def test_registry_pins_versions_commit_and_inputs():
    registry = json.loads(REGISTRY.read_text())
    assert registry["scanner_version"] and registry["corpus_version"] and registry["probe_set_version"]
    assert len(registry["source_commit"]) == 40
    assert all(len(value) == 64 for value in registry["input_sha256"].values())
    assert registry["n5_calibration_delta"]["result"] == "PASS"
    assert registry["n5_calibration_delta"]["max_candidate_spread"] <= 1.0
    assert registry["score_activation"] is False
