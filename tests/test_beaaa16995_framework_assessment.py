from copy import deepcopy
import pytest
from framework_assessment import build_framework_envelope, validate_claim_labels
from api.scan_handlers import _external_result_envelope
from scanners.live_probe_orchestrator import ScanResult


def score():
    return {"are_total":84,"band":"B","subscores":{"GOVERNANCE":21,"ROBUSTNESS":22,"BIAS_FAIRNESS":20,"OPS":21},
            "scanner_version":"0.4.1","corpus_version":"0.2.0","run_timestamp":"2026-07-23T00:00:00Z",
            "evidence_refs":[
                {"rubric_point_id":"P1.1","test_id":"pass","artifact_uri":"evidence://pass","sha256":"a"*64,"verdict":"pass"},
                {"rubric_point_id":"P3.3","test_id":"fail","artifact_uri":"evidence://fail","sha256":"b"*64,"verdict":"fail"},
                {"rubric_point_id":"BACKBOND_EXTENSION.EXTRA","test_id":"ext","artifact_uri":"evidence://ext","sha256":"c"*64,"verdict":"pass"}]}


def test_projection_is_additive_complete_and_hashed():
    source = score(); before = deepcopy(source)
    result = build_framework_envelope(source)
    assert source == before
    assert result["backbond_score"]["total"] == 84
    assessment = result["aiuc_1_mapped_technical_assessment"]
    assert assessment["certification_status"] == "not certification"
    assert {row["status"] for row in assessment["controls"]} == {"passed","failed","not evaluated"}
    assert all(ref["artifact_uri"] and ref["sha256"] for row in assessment["controls"] for ref in row["evidence_refs"])
    assert result["backbond_extensions"]["evidence_refs"][0]["sha256"] == "c"*64


@pytest.mark.parametrize("label", ["AIUC-certified","AIUC compliant","official score"])
def test_prohibited_labels_fail_closed(label):
    result = build_framework_envelope(score())
    result["aiuc_1_mapped_technical_assessment"]["label"] = label
    with pytest.raises(ValueError, match="prohibited"):
        validate_claim_labels(result)


def test_api_feature_flag_is_additive(monkeypatch):
    source = score()
    result = ScanResult(
        scan_id="fixture-scan", status="completed", scanner_version="0.4.1",
        corpus_version="0.2.0", rows_emitted=3,
    )
    monkeypatch.delenv("BACKBOND_FRAMEWORK_ENVELOPE_V1_1", raising=False)
    v1 = _external_result_envelope(result, source)
    assert "aiuc_1_mapped_technical_assessment" not in v1
    monkeypatch.setenv("BACKBOND_FRAMEWORK_ENVELOPE_V1_1", "1")
    v11 = _external_result_envelope(result, source)
    assert {key: v11[key] for key in v1} == v1
    assert v11["backbond_score"]["total"] == v1["are_total"]
