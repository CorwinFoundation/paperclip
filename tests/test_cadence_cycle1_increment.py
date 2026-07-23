"""Acceptance tests for the Cycle 1 offline candidate increment (BEAAA-16536).

Proves the acceptance criteria of the cadence spec
(``artifacts/beaaa-16530/continuous-live-test-cadence-v0.1.md``):

  * exactly 12 executable endpoint-free cases, split A=6 / B=3 / C=3;
  * case_id_hash and semantic_hash unique within the increment and zero
    collision against the verified predecessor canonical manifest;
  * every row materializes, terminates, recomputes its deterministic oracle,
    has reachable evidence and complete scanner/corpus/model/seed/runner/oracle
    pins;
  * candidate_only=true, scored=false, promotion=not_authorized;
  * rollback restores the predecessor digest and scoring surfaces are unchanged;
  * the builder fails closed on a missing / tampered predecessor and on a
    predecessor collision.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from cadence import identity, increment_cycle1 as ic, roster_oracle  # noqa: E402
from cadence.increment_cycle1 import IncrementError  # noqa: E402
from determination.continuity import recompute_decision, verify_bundle  # noqa: E402
from evals.aiuc_path_b import base as aiuc_base  # noqa: E402


@pytest.fixture(scope="module")
def manifest() -> dict:
    return ic.build_increment()


# --------------------------------------------------------------------------- #
# Count + split
# --------------------------------------------------------------------------- #

def test_exactly_12_cases_and_split(manifest):
    assert manifest["case_count"] == 12
    assert len(manifest["cases"]) == 12
    assert manifest["stream_counts"] == {"A": 6, "B": 3, "C": 3}
    counts = {"A": 0, "B": 0, "C": 0}
    for c in manifest["cases"]:
        counts[c["stream"]] += 1
    assert counts == {"A": 6, "B": 3, "C": 3}


# --------------------------------------------------------------------------- #
# Identity uniqueness + zero collision
# --------------------------------------------------------------------------- #

def test_identity_hashes_unique_within_increment(manifest):
    cid = [c["case_id_hash"] for c in manifest["cases"]]
    sem = [c["semantic_hash"] for c in manifest["cases"]]
    assert len(set(cid)) == 12
    assert len(set(sem)) == 12
    # hashes are full sha256 hex
    for h in cid + sem:
        assert len(h) == 64 and int(h, 16) >= 0


def test_zero_collision_with_predecessor(manifest):
    predecessor = ic.load_predecessor()
    pred_cid = {c["case_id_hash"] for c in predecessor.get("cases", [])}
    pred_sem = {c["semantic_hash"] for c in predecessor.get("cases", [])}
    for c in manifest["cases"]:
        assert c["case_id_hash"] not in pred_cid
        assert c["semantic_hash"] not in pred_sem
    assert manifest["collision_report"]["against_predecessor"] == []


def test_case_id_hash_reconstructs(manifest):
    # case_id_hash is fully reconstructable from public row fields.
    for c in manifest["cases"]:
        assert c["case_id_hash"] == identity.case_id_hash(
            c["stream"], c["family"], c["case_id"]
        )


def test_semantic_hash_is_deterministic_rebuild(manifest):
    # Same manifest rebuilt -> byte-identical semantic hashes (identity stability).
    rebuilt = {c["case_id"]: c["semantic_hash"] for c in ic.build_increment()["cases"]}
    for c in manifest["cases"]:
        assert rebuilt[c["case_id"]] == c["semantic_hash"]


# --------------------------------------------------------------------------- #
# Materialize / terminate / oracle recompute
# --------------------------------------------------------------------------- #

def test_every_row_terminates_pass(manifest):
    for c in manifest["cases"]:
        assert c["terminal"]["verdict"] == "PASS"


def test_stream_a_oracle_recomputes_offline(manifest):
    for c in [c for c in manifest["cases"] if c["stream"] == "A"]:
        spec = c["oracle"]["spec"]
        result = roster_oracle.evaluate(c["terminal"]["response"], spec)
        assert result["verdict"] == "PASS"
        assert result == c["terminal"]["oracle_result"]


def test_stream_b_oracle_recomputes_from_retained_evidence(manifest):
    for c in [c for c in manifest["cases"] if c["stream"] == "B"]:
        bundle = c["evidence_ref"]["bundle"]
        assert verify_bundle(bundle) is True
        recomputed = recompute_decision(bundle)
        assert recomputed["hash_matches"] is True
        assert bundle["band"] == c["oracle"]["spec"]["expected_band"]
        assert bundle["decision"] == c["oracle"]["spec"]["expected_decision"]
        assert c["terminal"]["band"] == bundle["band"]


def test_stream_b_provider_verdict_input_only(manifest):
    # Provider-native verdicts are present in evidence but never became the band.
    for c in [c for c in manifest["cases"] if c["stream"] == "B"]:
        provider = c["terminal"]["provider_native_determinations_input_only"]
        assert provider  # at least one provider verdict recorded
        assert c["terminal"]["band"] == c["oracle"]["spec"]["expected_band"]


def test_stream_c_oracle_recomputes_and_replicates(manifest):
    for c in [c for c in manifest["cases"] if c["stream"] == "C"]:
        spec = c["oracle"]["spec"]
        reps = c["terminal"]["replications"]
        assert len(reps) == ic.REPLICATION_K
        seeds = {r["seed"] for r in reps}
        assert len(seeds) == ic.REPLICATION_K
        for rep in reps:
            regrade = aiuc_base.binary_match_grade(
                rep["decision"], field=spec["field"], expected=spec["expected"]
            )
            assert regrade == "PASS" == rep["verdict"]


# --------------------------------------------------------------------------- #
# Evidence + pins completeness
# --------------------------------------------------------------------------- #

def test_pins_complete_every_row(manifest):
    required = ("scanner_version", "corpus_version", "model_versions", "seed", "runner_version", "oracle_version")
    for c in manifest["cases"]:
        for key in required:
            assert c["pins"].get(key) not in (None, "", {}), (c["case_id"], key)
        assert c["pins"]["baseline_scanner_version"] == "0.4.1"
        assert c["pins"]["candidate_scanner_version"] == "0.5.0"
        assert c["pins"]["scanner_version"] == "0.5.0"


def test_reachable_evidence_every_row(manifest):
    for c in manifest["cases"]:
        assert c["source"]["sha256"] and len(c["source"]["sha256"]) == 64
        src_path = REPO / c["source"]["path"]
        assert src_path.exists(), c["source"]["path"]
        # live source sha still matches the pinned source sha (reachable + bound)
        assert identity.source_sha256(str(src_path)) == c["source"]["sha256"]
        assert c["evidence_ref"]


def test_pii_and_no_weights_statement(manifest):
    for c in manifest["cases"]:
        assert c["pii_scrub"]
        assert c["no_customer_weights"] is True
        assert "No customer model weights" in c["no_customer_weights_statement"]


# --------------------------------------------------------------------------- #
# Candidate flags
# --------------------------------------------------------------------------- #

def test_candidate_flags_fail_safe(manifest):
    assert manifest["candidate_only"] is True
    assert manifest["scored"] is False
    assert manifest["promotion"] == "not_authorized"
    for c in manifest["cases"]:
        assert c["candidate_only"] is True
        assert c["scored"] is False
        assert c["promotion"] == "not_authorized"


# --------------------------------------------------------------------------- #
# Committed manifest is byte-stable
# --------------------------------------------------------------------------- #

def test_committed_manifest_matches_build(manifest):
    committed = json.loads(ic.INCREMENT_PATH.read_text(encoding="utf-8"))
    assert committed["increment_digest"] == manifest["increment_digest"]
    assert committed["cases"] == manifest["cases"]
    # byte-stable regeneration
    regenerated = json.dumps(ic.build_increment(), indent=2, sort_keys=True) + "\n"
    assert regenerated == ic.INCREMENT_PATH.read_text(encoding="utf-8")


def test_increment_digest_stable(manifest):
    assert ic.build_increment()["increment_digest"] == manifest["increment_digest"]
    assert ic.manifest_digest(manifest) == manifest["increment_digest"]


# --------------------------------------------------------------------------- #
# Fail-closed behaviors
# --------------------------------------------------------------------------- #

def test_fail_closed_missing_predecessor(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(IncrementError):
        ic.build_increment(predecessor_path=missing)


def test_fail_closed_tampered_predecessor_digest(tmp_path):
    predecessor = ic.load_predecessor()
    predecessor["manifest_digest"] = "0" * 64
    p = tmp_path / "tampered.json"
    p.write_text(json.dumps(predecessor), encoding="utf-8")
    with pytest.raises(IncrementError):
        ic.build_increment(predecessor_path=p)


def test_fail_closed_on_predecessor_collision(tmp_path, manifest):
    # Seed a predecessor that already contains one increment identity -> collision.
    victim = manifest["cases"][0]
    predecessor = ic.load_predecessor()
    predecessor["cases"] = [
        {"case_id_hash": victim["case_id_hash"], "semantic_hash": victim["semantic_hash"],
         "stream": victim["stream"], "family": victim["family"], "case_id": victim["case_id"],
         "terminal": {"verdict": "PASS"}, "pins": {"oracle_version": victim["pins"]["oracle_version"]}},
    ]
    predecessor["manifest_digest"] = ic.manifest_digest(predecessor)
    p = tmp_path / "collide.json"
    p.write_text(json.dumps(predecessor), encoding="utf-8")
    with pytest.raises(IncrementError):
        ic.build_increment(predecessor_path=p)


# --------------------------------------------------------------------------- #
# Rollback + scoring-surface invariance
# --------------------------------------------------------------------------- #

def test_rollback_restores_predecessor_digest():
    predecessor = ic.load_predecessor()
    restored = ic.rollback_active_digest()
    assert restored == predecessor["manifest_digest"]
    # predecessor targeted regression: the predecessor still verifies post-rollback
    assert ic.load_predecessor()["manifest_digest"] == restored


def test_scoring_surfaces_unchanged(manifest):
    # No scoring path is imported by the cadence increment.
    import cadence.increment_cycle1 as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "rubric.scoring" not in src
    assert "import rubric" not in src
    # No band/pillar/total scoring surface is emitted by the manifest.
    blob = json.dumps(manifest)
    for forbidden in ('"are_total"', '"pillar_scores"', '"raw_score"'):
        assert forbidden not in blob
    assert all(c["scored"] is False for c in manifest["cases"])


# --------------------------------------------------------------------------- #
# Normalization semantics
# --------------------------------------------------------------------------- #

def test_normalization_collapses_ws_but_preserves_quoted():
    assert identity.normalize_text("a   b\r\n c") == "a b c"
    # whitespace inside a quoted literal is preserved verbatim
    assert identity.normalize_text('say "BBSEC   LEAK" now') == 'say "BBSEC   LEAK" now'
    assert identity.normalize_text("  trim  me  ") == "trim me"
