"""Envelope schema: required fields enforced; band/are_total bounds checked."""
import pytest

from are_probe.envelope import EnvelopeError, assert_v1_output_shape


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
