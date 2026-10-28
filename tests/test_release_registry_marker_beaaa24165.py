from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.active_test_registry import build_registry
from tools.release_registry_marker import (
    _normalized_registry_bytes,
    build_release_registry_marker,
)


REPO = Path(__file__).resolve().parents[1]
# Re-pinned for BEAAA-31092: the 67 evidenced rank-2 rows opt in to development-mode
# scoring through `attestation_tier=founder_dev_mode` + `dev_scoring_active`, which
# changes those rows' normalized bytes. The row census does not move -- counts stay
# 2,353 / 2,353 / 2,352 and the exclusion proof stays at 137 rows -- so only the
# digest is re-pinned. Previous pin ca712717...69bf8 came from BEAAA-31091 (ii-015
# indirect-injection fixture prompt).
EXPECTED_NORMALIZED_REGISTRY_SHA256 = (
    # Rehashed after BEAAA-33568 re-admitted approval_freshness_replay's 22 rows
    # to the rank-2 ledger's scored set, which flips those rows to the dev-scored
    # mapping and evidence fields and so changes their normalized bytes. Prior pin
    # fcde4065...97bef came from the BEAAA-33507 dev-tier mapping rewrite.
    #
    # Re-pinned again for BEAAA-33810: the agent-escape incident tranche adds 8
    # executable rows (OOSC-INC-01..08) to out_of_scope_containment, so unlike the
    # re-pins above this one DOES move the census -- 2,353 / 2,353 / 2,352 becomes
    # 2,361 / 2,361 / 2,360, all asserted below. The digest is not the proof that
    # only the tranche moved; the row-level diff is. Regenerating the registry from
    # a clean origin/master (213f7133) reproduces the committed artifact with 0 rows
    # added and 0 removed, and the branch adds exactly 11 rows over that baseline:
    # the 8 executable cases plus 3 fixture_test_only fixture files. Prior pin
    # 774b614b...e715f came from BEAAA-33568.
    #
    # Re-pinned for BEAAA-33992. This one moves NO row into or out of the
    # registry and changes exactly one field on the rows it touches. Building the
    # registry at origin/master (96c35b7f) and at this branch and diffing row by
    # row: 0 added, 0 removed, 43 changed -- the 43 scored rank-2 rows, 22 in
    # approval_freshness_replay and 21 in authorization_revocation_race -- and on
    # every one of them the sole differing field is
    # `execution_evidence_run_manifest_sha256`,
    # ab9fdb03...92607cb -> d73af798...4efe3d7e6.
    #
    # That field is a provenance pointer the rows read off the rank-2 ledger's
    # `run_manifest_sha256`, and the move is a repair: master's committed ledger
    # named ab9fdb03 while the manifest committed beside it hashed f1f32147, so
    # the pointer was stale before this branch touched it. It now names the bytes
    # it is stored next to.
    #
    # `scoring_active`, `reporting_status`, `attestation_tier` and every evidence
    # field are unmoved on all 2,498 rows, so no row entered the scored set and
    # the BEAAA-33992 fence holds. Counts are unchanged at 2,361 / 2,361 / 2,360,
    # asserted below. Prior pin b4b6e143...e686331 came from BEAAA-33810.
    #
    # Re-pinned for BEAAA-35380. The registry edit that moved it is the
    # regeneration of tools/probe-registry/{active,authoritative}_registry.json
    # after the two TOOL_MISUSE families were added to the founder-dev-scored set
    # in tools/active_test_registry.py. Like BEAAA-33992 this moves no row into or
    # out of the registry. Building the registry at the merge base (1ba1e8a0) and
    # at this branch and diffing row by row: 2,498 rows both sides, 0 added,
    # 0 removed, 23 changed -- exactly the 23 activated rows, TOOL_MISUSE_PT-01..04,
    # PT-06..12 and UC-01..12. TOOL_MISUSE_PT-05 is held as unmeasured and is NOT
    # among them.
    #
    # On those 23 rows the differing fields are only the dev-tier mapping and
    # evidence fields: attestation_tier, dev_scoring_active,
    # candidate_rubric_mapping_status, candidate_rubric_point_id,
    # candidate_rubric_mapping_rationale, execution_evidenced,
    # execution_evidence_status, execution_evidence_ref. `scoring_active` stays
    # False and `reporting_status` stays "reported_not_scored" on all 2,498 rows,
    # so no row entered the production scored set. Counts are unchanged at
    # 2,361 / 2,361 / 2,360 and the exclusion proof stays at 137, both asserted
    # below -- the census not moving is independent corroboration of the
    # zero-production-delta claim. Prior pin dc6ec6ba...4fdd588 came from
    # BEAAA-33992.
    # Re-pinned for BEAAA-35384: nine evaluation_boundary_credential_use rows
    # were added as reported-not-scored with zero weight. Executable counts
    # remain unchanged; only the normalized registry bytes move.
    #
    # Re-pinned again on the merge of this branch with master. Both edits above
    # are present in the merged tree and neither subsumes the other: master's nine
    # BEAAA-35384 rows are all still here and this branch's two TOOL_MISUSE families
    # are activated on top of them. Recomputed on the merged tree rather than
    # reconciled by hand -- building the registry at master (0313498b) reproduces
    # master's pin 20eb4449...8ba7c exactly, which is what makes the merged value
    # below trustworthy. Merged tree vs master: 2,507 rows both sides, 0 added,
    # 0 removed, and keyed by test_id exactly two entries change,
    # tool_misuse_param_tamper and tool_misuse_unauthorized_call. The only fields
    # that move on them are the eight dev-activation fields (attestation_tier,
    # dev_scoring_active, candidate_rubric_mapping_status,
    # candidate_rubric_point_id, candidate_rubric_mapping_rationale,
    # execution_evidenced, execution_evidence_status, execution_evidence_ref).
    # The scoring_active census is unmoved at 45 on both sides and the
    # reporting_status distribution is identical, so no row entered the production
    # scored set. Counts stay 2,361 / 2,361 / 2,360 and the exclusion proof stays
    # at master's 146.
    #
    # Re-pinned for BEAAA-35915: the confidence_calibration dev activation. Like
    # BEAAA-35380 and BEAAA-33992 this moves no row into or out of the registry.
    # Building the registry at origin/master (b87c90d3) and at this branch and
    # diffing row by row, keyed by (test_id, case_id): 2,507 rows both sides,
    # 0 added, 0 removed, exactly 70 changed -- the 70 confidence_calibration
    # rows the BEAAA-35665 ledger resolves to `scored`. The slice is 98 rows; the
    # 28 hard-arithmetic rows are `evidence_only` and are correctly absent from
    # this diff, as are any `held` rows (there are none).
    #
    # On those 70 rows the differing fields are only the eight dev-activation
    # fields, the same set BEAAA-35380 moved: attestation_tier,
    # dev_scoring_active, candidate_rubric_mapping_status,
    # candidate_rubric_point_id, candidate_rubric_mapping_rationale,
    # execution_evidenced, execution_evidence_status, execution_evidence_ref.
    # No other field moves on any of the 2,507 rows.
    #
    # Independent corroboration that nothing entered the production scored set:
    # the `scoring_active` census is 479 on both sides and the reporting_status
    # distribution is identical (444 scored / 2,028 reported_not_scored /
    # 35 scoring_active). Counts stay 2,361 / 2,361 / 2,360 and the exclusion
    # proof stays at 146, both asserted below.
    #
    # Building the registry at master reproduces master's pin
    # 2f2ea28b...b204e97 exactly, which is what makes the value below
    # trustworthy rather than merely observed. Prior pin 2f2ea28b...b204e97
    # came from the BEAAA-35380 merge.
    #
    # Re-pinned for BEAAA-35938: the hallucination_legal dev-activation ledger
    # flips 106 rows to the dev-scored mapping and evidence fields, which changes
    # their normalized bytes. Attributed by row-diffing the normalized registry
    # against 7a2a573b rather than by reasoning from the aggregate: 2,507 rows on
    # both sides, exactly 106 differ, and the eight fields that move on those rows
    # are attestation_tier, candidate_rubric_mapping_rationale,
    # candidate_rubric_mapping_status, candidate_rubric_point_id,
    # dev_scoring_active, execution_evidence_ref, execution_evidence_status and
    # execution_evidenced -- the dev tier only. Building the marker at 7a2a573b in
    # a separate worktree reproduces bc6bddc5...6ab3943 exactly, so the new value
    # is caused by this change and nothing else in the tree. Production is
    # unaffected: the scoring_active census is 479 on both sides and the counts
    # below are unchanged at 2,361 / 2,361 / 2,360. Prior pin bc6bddc5...6ab3943
    # came from the BEAAA-33810 agent-escape tranche via BEAAA-35915.
    #
    # Re-pinned for BEAAA-40376 after binding 24 browser-surface action-integrity
    # rows as reported-not-scored trace observations. The executable census is
    # unchanged; only those rows' normalized registry metadata changes.
    # Re-pinned for BEAAA-40377 after binding 24 long-running cancellation-race
    # rows under the same report-only contract. Counts and scoring stay unchanged.
    # Re-pinned for BEAAA-40425 after binding 24 persistent-memory-poisoning rows
    # under that contract. Counts and scoring remain unchanged.
    # Re-pinned for BEAAA-40470 after binding 24 retrieval-source-trust rows.
    # Re-pinned for BEAAA-40539 after binding 24 file-parser-boundary rows.
    # Re-pinned for BEAAA-40842 after binding 24 paired-bias rows.
    # Re-pinned for BEAAA-40887 after binding 24 approval-freshness replay rows
    # to receipt-backed traces. The rows remain reported-not-scored, so the
    # executable and scored censuses are unchanged.
    # Re-pinned for BEAAA-40934 after binding 19 revocation-race rows to
    # receipt-backed traces. The rows remain reported-not-scored, so the
    # executable and scored censuses are unchanged.
    # Re-pinned for BEAAA-40978 after binding 24 C1 authorization/recipient
    # rows to recorded traces; all remain reported-not-scored.
    # Re-pinned for BEAAA-41000 after binding the next 24 C1 recipient,
    # transaction-end-state, and retrieval-grounding rows under the same contract.
    # Re-pinned for BEAAA-41004 after binding the next 24 retrieval,
    # indirect-injection, and transformed-exfiltration target traces.
    # Re-pinned for BEAAA-41006 after binding 24 transformed-exfiltration,
    # multi-agent-handoff, and stop/resume-memory target traces.
    # Re-pinned for BEAAA-41008 after binding 24 stop/resume-memory and
    # counterfactual-stability target traces.
    # Re-pinned for BEAAA-41033 after binding the next 24 counterfactual-
    # stability target traces.
    # Re-pinned for BEAAA-41035 after binding the final 18 counterfactual-
    # stability rows and six hallucination-legal target traces.
    # Re-pinned for BEAAA-41058 after binding the remaining 23 hallucination-
    # legal target traces. All 1,181 content-candidate rows are now bound, and
    # every newly bound row remains reported-not-scored.
    # Re-pinned for BEAAA-41063 after activating development-mode scoring for
    # the 100 spread-qualified, receipt-backed rows selected by the committed
    # activation ledger. Production scoring remains disabled for these rows;
    # the executable census asserted below is unchanged.
    # E018 follow-up: append twelve disclosure-bypass cases and version the
    # expanded lane-A pack at 0.2.0. Existing C10 activations are unchanged.
    # EU/ISO adds twelve conditional, report-only case contracts. Re-generated
    # from the reviewed registry; measured and published counts stay unchanged.
    # Evidence expansion adds five scoped log-quality cases, changes their source
    # closure and retains every scoring activation and published count.
    # PR #1304 adds nine conditional, zero-score evidence cases. Isolated VM
    # job 122514194748 observed this hash after the unchanged 2373/2373/2372
    # measured/runtime count assertions passed.
    # Nine further conditional evidence cases; exact generated rows, same measured counts.
    "b928928b6638f9090b491d97ce8bcf86c2ff008a5ad8a576df3c1af23aaa33ef"
)


def test_marker_derives_counts_and_hash_from_same_generated_registry() -> None:
    rows = build_registry(REPO / "rubric" / "probe_mappings")
    marker = build_release_registry_marker(REPO)

    # Exact combined-tree measurement after the Day-1 supporting-family
    # registration. The new rows remain reported-not-scored.
    assert marker["published_executable_count"] == 2_373
    assert marker["generated_executable_count"] == 2_373
    assert marker["distinct_runtime_count"] == 2_372
    assert marker["normalized_registry_sha256"] == hashlib.sha256(
        _normalized_registry_bytes(rows)
    ).hexdigest()
    assert marker["normalized_registry_sha256"] == EXPECTED_NORMALIZED_REGISTRY_SHA256
    assert all("\\" not in str(row["source_pack"]) for row in rows)
    # The 64 original direct-family rows and the 32 new supporting rows are
    # deliberately report-only and appear in the excluded-row proof. BEAAA-29742
    # gated 192 rows as unbound and the proof grew by 185, so seven of them were
    # already carried here under another disposition. Binding the 40 cyber misuse and
    # 60 harmful-output rows and 60 counterfactual-stability rows remove them from
    # this exclusion proof. The pii_attestation.evidence_pack row is now bound
    # through the production adapter. Binding the two demographic-parity rows
    # reduces the exclusion proof by two more.
    # The nine BEAAA-35384 rows are report-only and therefore extend the
    # excluded-row proof without changing executable counts.
    # Conditional operational cases extend the exclusion proof, not the
    # published measured-test census.
    assert len(marker["excluded_rows"]) == 148 + 35
    assert {row["disposition"] for row in marker["excluded_rows"]} == {
        "incompatible",
        "reported_not_scored",
    }


def test_marker_fails_closed_if_published_count_drifts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools.release_registry_marker.load_published_count", lambda _path: 2_211)
    with pytest.raises(ValueError, match="generated executable count does not match"):
        build_release_registry_marker(REPO)


def test_rendered_manifest_embeds_exact_marker(tmp_path: Path) -> None:
    output = tmp_path / "release.json"
    from scripts.render_release_manifest import render

    render(
        REPO / "release" / "backbond-scanner-release.json",
        output,
        "a" * 40,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["build_sha"] == "a" * 40
    assert manifest["scanner_version"] == "0.5.0"
    assert manifest["corpus_version"] == "0.2.0"
    assert manifest["registry_marker"] == build_release_registry_marker(REPO)
