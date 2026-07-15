import json

from are_probe.eval_results_builder import (
    RUBRIC_V1_2_0_DIM_IDS,
    build_eval_results,
    write_eval_results_dir,
)


def test_placeholder_dimensions_are_written_as_uncovered_not_scored(tmp_path):
    eval_results = build_eval_results(
        measured={"P3.1": (4.0, "are-probe://family/refusal/sha256:abc")},
        dp_attestations={"P4.1": {"raw": 3.0, "evidence_uri": "attest://p4.1"}},
    )

    write_eval_results_dir(
        target_dir=tmp_path,
        eval_results=eval_results,
        model_versions={"primary": "fixture-agent-v0", "judges": []},
    )

    scored = json.loads((tmp_path / "eval_results.json").read_text())
    assert set(scored) == {"P3.1", "P4.1"}
    assert scored["P3.1"]["source"] == "measured"
    assert scored["P4.1"]["source"] == "dp_attested"

    applicability = json.loads((tmp_path / "applicability.json").read_text())
    placeholder_dims = set(RUBRIC_V1_2_0_DIM_IDS) - {"P3.1", "P4.1"}
    assert set(applicability["na_dims"]) == placeholder_dims
    assert set(applicability["applicability_decisions"]) == placeholder_dims
    for decision in applicability["applicability_decisions"].values():
        assert decision["fires"] is False
        assert decision["na_justification_key"] == "probe_sdk.r1_placeholder"

    invocation = json.loads(
        (tmp_path / "scanner_invocation_context.json").read_text()
    )
    assert invocation["applicability_mode"] == "probe_sdk_placeholder_exclusion"
