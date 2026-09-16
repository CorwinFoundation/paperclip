from harness.model_dependency_declaration import DEFAULT_SEEDS, run_model_dependency_probe


def test_model_dependency_verdicts_and_evidence_shape():
    stable = run_model_dependency_probe(fixture="stable").to_dict()
    unstable = run_model_dependency_probe(fixture="unstable").to_dict()
    undeclared = run_model_dependency_probe(fixture="undeclared").to_dict()
    assert [stable["verdict"], unstable["verdict"], undeclared["verdict"]] == [
        "declared_and_stable", "declared_unstable", "undeclared"]
    assert stable["scored"] is False
    assert stable["rubric_point_id"] == "GOVERNANCE.MODEL_PROVENANCE"
    assert len(stable["evidence"]["per_seed_fingerprints"]) == len(DEFAULT_SEEDS) == 5
    assert stable["evidence"]["declaration_text"] == {
        "model_identity": "atlas", "model_version": "2026-09", "fine_tune_or_adapter": "safety-v3"}


def test_model_dependency_all_fixtures_are_byte_stable_on_replay():
    for fixture in ("stable", "unstable", "undeclared"):
        assert run_model_dependency_probe(fixture=fixture).evidence_fingerprint == run_model_dependency_probe(fixture=fixture).evidence_fingerprint
