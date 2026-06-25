from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_equal_opportunity_mapping_points_to_signed_packet_shape():
    mapping = yaml.safe_load(
        (REPO_ROOT / "corpus/bias/equal_opportunity/equal_opportunity.yaml").read_text()
    )
    assert mapping["rubric_point_id"] == "P2.2"
    assert mapping["probe_id"] == "equal_opportunity_struct_v0.2"
    assert mapping["corpus_ref"] == (
        "artifacts/beaaa-8129/"
        "p2_2_equal_opportunity_labeled_outcome_corpus_v0.1.jsonl"
    )
    assert mapping["sdk_corpus_pack"] == (
        "engineering/probe-sdk/corpus/bias_equal_opportunity_v0.1.jsonl"
    )
    assert mapping["underwriter_signoff_ref"] == "BEAAA-8133"
    assert mapping["expected_output_field"] == "expected_label"
    assert mapping["positive_label_field"] == "positive_outcome"
    assert mapping["negative_label_field"] == "negative_outcome"
    assert mapping["metric"]["eligible_rows"] == "ground_truth_label == positive"
    assert "corpus.yaml" not in mapping["corpus_ref"]
    assert not mapping.get("case_file", "").endswith(".csv")
