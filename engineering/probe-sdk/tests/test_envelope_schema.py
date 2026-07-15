"""Envelope schema: required fields enforced; band/are_total bounds checked."""
from types import SimpleNamespace

import pytest

from are_probe.envelope import EnvelopeError, assert_v1_output_shape, build_envelope


def _valid_contract_output(**overrides):
    base = {
        "are_total": 72,
        "subscores": {
            "OPS": 18.0,
            "BIAS_FAIRNESS": 17.5,
            "ROBUSTNESS": 19.0,
            "GOVERNANCE": 17.5,
        },
        "band": "B",
        "evidence_refs": [],
        "scanner_version": "0.4.1",
        "corpus_version": "0.1.0",
        "model_versions": {"primary": "fixture-agent-v0", "judges": []},
        "seed": 7,
        "run_timestamp": "2026-06-13T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_valid_contract_output_passes():
    assert_v1_output_shape(_valid_contract_output())


def test_missing_field_raises():
    co = _valid_contract_output()
    del co["scanner_version"]
    with pytest.raises(EnvelopeError):
        assert_v1_output_shape(co)


def test_are_total_out_of_range_raises():
    with pytest.raises(EnvelopeError):
        assert_v1_output_shape(_valid_contract_output(are_total=101))


def test_band_invalid_raises():
    with pytest.raises(EnvelopeError):
        assert_v1_output_shape(_valid_contract_output(band="E"))


def test_missing_subscore_raises():
    co = _valid_contract_output()
    del co["subscores"]["ROBUSTNESS"]
    with pytest.raises(EnvelopeError):
        assert_v1_output_shape(co)


def test_build_envelope_preserves_master_scanner_evidence_refs():
    master_refs = [
        {
            "rubric_point_id": "ROBUSTNESS.PROMPT_INJECTION.DIRECT",
            "test_id": "master.pi.direct.001",
            "artifact_uri": "scanner://evidence/master/pi-direct-001.json",
            "verdict": "pass",
            "weight": 0.5,
        },
        {
            "rubric_point_id": "OPS.REPRODUCIBILITY.SEED_PIN",
            "test_id": "master.ops.seed.001",
            "artifact_uri": "scanner://evidence/master/seed-pin-001.json",
            "verdict": "partial",
            "weight": 0.25,
        },
    ]
    contract_output = _valid_contract_output(evidence_refs=master_refs)
    config = SimpleNamespace(
        corpus_version="0.1.0",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=7,
    )
    corpus_pack = SimpleNamespace(
        pack_id="fixture_pack_v0.1",
        sha256="0" * 64,
        upstream_source_repo="fixture",
        upstream_source_path="corpus.jsonl",
        upstream_source_commit_sha="fixture-sha",
        corpus_version="0.1.0",
        probe_set_version="0.1.0",
    )

    envelope = build_envelope(
        contract_output=contract_output,
        agent_id="fixture-agent",
        config=config,
        corpus_pack=corpus_pack,
        eval_results={},
        family_results=[],
        freeze_timestamp=True,
    )

    assert envelope["evidence_refs"][: len(master_refs)] == master_refs
    assert contract_output["evidence_refs"] == master_refs
