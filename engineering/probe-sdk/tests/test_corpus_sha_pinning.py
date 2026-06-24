"""Corpus SHA pinning enforced. Tamper detection: any byte change to a
corpus file must be rejected by the loader before any probe runs.
"""
from pathlib import Path

import pytest

from are_probe.corpus import CorpusError, load_lockfile, load_pack
from are_probe.corpus_fetcher import CorpusSource, github_raw_url

SDK_ROOT = Path(__file__).resolve().parents[1]


def test_lockfile_corpus_sha_matches_disk():
    pack = load_pack(SDK_ROOT, "refusal_robustness_v0.1",
                     load_lockfile(SDK_ROOT / "lockfile.toml"))
    assert pack.sha256 != "POPULATED_AT_BUILD_TIME"
    assert pack.corpus_version == "0.1.0"
    assert pack.probe_set_version == "0.1.0"
    assert pack.upstream_source_repo == "https://github.com/backbond-ai/are-corpus"
    assert pack.upstream_source_path == "corpus/refusal_robustness_v0.1.jsonl"
    assert len(pack.upstream_source_commit_sha) == 40
    assert pack.upstream_source_commit_sha == pack.upstream_source_commit_sha.lower()
    assert "ROBUSTNESS.REFUSAL.DONOTANSWER_SMOKE" in pack.rubric_point_ids
    assert len(pack.probes) >= 5  # R1 minimum corpus size


def test_bias_corpus_packs_are_pinned_and_loadable():
    lockfile = load_lockfile(SDK_ROOT / "lockfile.toml")
    expected = {
        "bias_demographic_parity_v0.1": "P2.1",
        "bias_equal_opportunity_v0.1": "P2.2",
        "bias_counterfactual_stability_v0.1": "P2.3",
        "bias_eval_representation_v0.1": "P2.4",
        "bias_sentiment_skew_v0.1": "P2.5",
    }
    for pack_id, rubric_point_id in expected.items():
        pack = load_pack(SDK_ROOT, pack_id, lockfile)
        assert pack.corpus_version == "0.1.0"
        assert pack.probe_set_version == "0.1.0"
        assert rubric_point_id in pack.rubric_point_ids
        assert len(pack.upstream_source_commit_sha) == 40
        assert len(pack.probes) >= 5
        assert all(p.metadata for p in pack.probes)


def test_corpus_sha_mismatch_rejected(tmp_path):
    # Construct a tampered lockfile with a wrong SHA and confirm the
    # loader refuses to use the on-disk file.
    lf = load_lockfile(SDK_ROOT / "lockfile.toml")
    lf["corpus"]["packs"]["refusal_robustness_v0_1"]["sha256"] = "0" * 64
    with pytest.raises(CorpusError) as exc:
        load_pack(SDK_ROOT, "refusal_robustness_v0.1", lf)
    assert "SHA mismatch" in str(exc.value)


def test_corpus_pack_requires_upstream_source_commit_sha():
    lf = load_lockfile(SDK_ROOT / "lockfile.toml")
    del lf["corpus"]["packs"]["refusal_robustness_v0_1"]["upstream_source"]["commit_sha"]
    with pytest.raises(CorpusError) as exc:
        load_pack(SDK_ROOT, "refusal_robustness_v0.1", lf)
    assert "missing upstream_source.commit_sha" in str(exc.value)


def test_corpus_pack_rejects_branch_or_short_source_refs():
    lf = load_lockfile(SDK_ROOT / "lockfile.toml")
    source = lf["corpus"]["packs"]["refusal_robustness_v0_1"]["upstream_source"]
    source["commit_sha"] = "main"
    with pytest.raises(CorpusError) as exc:
        load_pack(SDK_ROOT, "refusal_robustness_v0.1", lf)
    assert "40-character lowercase hex commit SHA" in str(exc.value)


def test_github_raw_url_is_commit_pinned():
    source = CorpusSource(
        repo="https://github.com/backbond-ai/are-corpus",
        path="corpus/refusal_robustness_v0.1.jsonl",
        commit_sha="d801736000000000000000000000000000000000",
    )
    assert github_raw_url(source) == (
        "https://github.com/backbond-ai/are-corpus/raw/"
        "d801736000000000000000000000000000000000/"
        "corpus/refusal_robustness_v0.1.jsonl"
    )


def test_github_raw_url_rejects_unpinned_source_ref():
    source = CorpusSource(
        repo="https://github.com/backbond-ai/are-corpus",
        path="corpus/refusal_robustness_v0.1.jsonl",
        commit_sha="v0.1.0",
    )
    with pytest.raises(CorpusError):
        github_raw_url(source)
