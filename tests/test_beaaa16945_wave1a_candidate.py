from __future__ import annotations

from dataclasses import replace

from evals.aiuc_path_b import base
from evals.aiuc_path_b import consequential_action_confirmation as cac
from evals.aiuc_path_b import cross_session_data_leak as csdl
from evals.aiuc_path_b import wave1a_candidate as wave


def test_wave1a_candidate_counts_and_reported_not_scored_packet() -> None:
    packet = wave.build_candidate_packet(
        target=wave.reference_authorization(),
        commit_sha="acb36976e171ace36dff21e9e02f29cc783390f2",
    )

    assert packet["fail_closed"] is False
    assert packet["promotion_state"] == "reported_not_scored"
    assert packet["underwriting_state"] == "not_underwriting_active"
    assert packet["counts"]["unique_scenario_count_by_family"] == {
        "cross_session_data_leak": 20,
        "consequential_action_confirmation": 15,
    }
    assert packet["counts"]["execution_count_by_family"] == {
        "cross_session_data_leak": 100,
        "consequential_action_confirmation": 75,
    }
    assert packet["counts"]["underwriting_active_count"] == 0
    assert packet["signed_manifest"]["signature"].startswith("sha256:")
    assert packet["reproducibility_summary"]["terminal_verdict_class_invariant"] is True
    assert packet["reproducibility_summary"]["golden_n5_delta"] == {"total": 0, "pillars": 0}
    assert packet["families"]["cross_session_data_leak"]["canary_records"] == ["csdl-20-canary"]
    assert packet["families"]["consequential_action_confirmation"]["canary_records"] == [
        "cac-15-canary"
    ]


def test_cross_session_family_has_20_cases_and_k5_invariant_pass_verdicts() -> None:
    runs = base.replicate(csdl.SPEC, csdl.compliant_policy)
    assert len(csdl.SPEC.cases) == 20
    assert len(runs) == 5
    by_case: dict[str, set[str]] = {}
    for run in runs:
        assert run["scored"] is False
        assert run["scoring"] == "reported_not_scored"
        for row in run["case_results"]:
            by_case.setdefault(row["case_id"], set()).add(row["verdict"])
    assert set(by_case) == {case.case_id for case in csdl.SPEC.cases}
    assert all(verdicts == {base.PASS} for verdicts in by_case.values())


def test_wave1a_families_discriminate_bad_reference_policies() -> None:
    csdl_bad = base.verdict_tally(base.run_family(csdl.SPEC, csdl.reckless_policy, seed=0))
    cac_bad = base.verdict_tally(base.run_family(cac.SPEC, cac.reckless_policy, seed=0))
    assert csdl_bad[base.FAIL] == 20
    assert cac_bad[base.FAIL] == 10


def test_wave1a_fail_closed_missing_authorization() -> None:
    target = replace(wave.reference_authorization(), authorization_ref="")
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["underwriting_state"] == "not_underwriting_active"
    assert packet["failure_code"] == "authorization_missing:authorization_ref"


def test_wave1a_fail_closed_missing_tool_inventory() -> None:
    target = replace(wave.reference_authorization(), declared_tool_inventory=())
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "declared_tool_inventory_missing"


def test_wave1a_fail_closed_scope_conflict() -> None:
    target = replace(wave.reference_authorization(), allowed_family_ids=("cross_session_data_leak",))
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "family_scope_conflict"


def test_wave1a_fail_closed_hash_mismatch() -> None:
    target = wave.reference_authorization()
    bad = replace(target, corpus_sha256={**target.corpus_sha256, "cross_session_data_leak": "bad"})
    packet = wave.build_candidate_packet(target=bad, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "corpus_hash_mismatch"


def test_wave1a_fail_closed_mutable_evidence() -> None:
    target = replace(wave.reference_authorization(), evidence_immutable=False)
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "mutable_evidence"


def test_wave1a_fail_closed_evaluator_incomplete() -> None:
    packet = wave.build_candidate_packet(
        target=wave.reference_authorization(),
        commit_sha="candidate",
        force_incomplete_family="cross_session_data_leak",
    )
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "incomplete_runs:cross_session_data_leak"
