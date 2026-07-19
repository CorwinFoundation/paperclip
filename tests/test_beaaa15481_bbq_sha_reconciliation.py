import copy

import pytest
import yaml

from scripts.beaaa15481_reconcile_bbq_mapping import (
    DEFAULT_MAPPING,
    ReconciliationError,
    reconcile,
)


def test_bbq_mapping_corpus_manifest_and_disk_sha_reconcile():
    result = reconcile()

    assert result.corpus_file_sha256 == "d0a636e2f14cb8559aa064bda0cdf3d59cd5f4a2cae0bfefaef04a744f83e753"
    assert result.n_items == 200
    report = result.to_report()
    assert report["candidate_corpus_created"] is False
    assert report["activation"]["authorized"] is False
    assert report["contract"] == "are-scanner-api-v1@v1.0"
    assert report["rubric_point_id"] == "P2.1"


def test_bbq_mapping_sha_mismatch_fails_closed_without_candidate_corpus(tmp_path):
    mapping = yaml.safe_load(DEFAULT_MAPPING.read_text(encoding="utf-8"))
    stale_mapping = copy.deepcopy(mapping)
    stale_mapping["corpus"]["sha256"] = "0" * 64
    stale_path = tmp_path / "bbq.yaml"
    stale_path.write_text(yaml.safe_dump(stale_mapping, sort_keys=False), encoding="utf-8")

    with pytest.raises(ReconciliationError, match="FAIL CLOSED") as exc:
        reconcile(stale_path)

    assert "mapping sha256" in str(exc.value)
    assert not list(tmp_path.glob("candidate*"))
