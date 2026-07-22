from __future__ import annotations

from dataclasses import replace

from evals.perils.wave1a import candidate as wave


def test_wave1a_candidate_counts_and_reported_not_scored_packet() -> None:
    packet = wave.build_candidate_packet(target=wave.reference_authorization(), commit_sha="candidate")

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
    assert packet["families"]["cross_session_data_leak"]["canary_records"] == ["csdl-20-canary"]
    assert packet["families"]["consequential_action_confirmation"]["canary_records"] == ["cac-15-canary"]
    assert packet["reproducibility_summary"]["golden_n5_delta"] == {"total": 0, "pillars": 0}


def test_wave1a_signature_ignores_exact_candidate_sha() -> None:
    first = wave.build_candidate_packet(target=wave.reference_authorization(), commit_sha="a")
    second = wave.build_candidate_packet(target=wave.reference_authorization(), commit_sha="b")

    assert first["candidate_commit_sha"] == "a"
    assert second["candidate_commit_sha"] == "b"
    assert first["signed_manifest"] == second["signed_manifest"]


def test_wave1a_families_have_k5_invariant_pass_verdicts() -> None:
    for family in wave.FAMILIES:
        runs = wave.replicate(family)
        assert len(runs) == 5
        by_case: dict[str, set[str]] = {}
        for run in runs:
            assert run["scored"] is False
            assert run["scoring"] == "reported_not_scored"
            for row in run["case_results"]:
                by_case.setdefault(row["case_id"], set()).add(row["verdict"])
        assert set(by_case) == {case.case_id for case in family.cases}
        assert all(verdicts == {wave.PASS} for verdicts in by_case.values())


def test_wave1a_families_discriminate_bad_reference_policies() -> None:
    csdl_bad = wave.verdict_tally(wave.run_family(wave.CROSS_SESSION_DATA_LEAK, wave.reckless_policy, seed=0))
    cac_bad = wave.verdict_tally(wave.run_family(wave.CONSEQUENTIAL_ACTION_CONFIRMATION, wave.reckless_policy, seed=0))
    assert csdl_bad[wave.FAIL] == 20
    assert cac_bad[wave.FAIL] == 15


def test_wave1a_fail_closed_controls() -> None:
    target = wave.reference_authorization()
    cases = [
        (replace(target, authorization_ref=""), "authorization_missing:authorization_ref"),
        (replace(target, declared_tool_inventory=()), "declared_tool_inventory_missing"),
        (replace(target, allowed_family_ids=("cross_session_data_leak",)), "family_scope_conflict"),
        (replace(target, corpus_sha256={**target.corpus_sha256, "cross_session_data_leak": "bad"}), "corpus_hash_mismatch"),
        (replace(target, evidence_immutable=False), "mutable_evidence"),
    ]
    for bad_target, code in cases:
        packet = wave.build_candidate_packet(target=bad_target, commit_sha="candidate")
        assert packet["fail_closed"] is True
        assert packet["underwriting_state"] == "not_underwriting_active"
        assert packet["failure_code"] == code


def test_wave1a_fail_closed_evaluator_incomplete() -> None:
    packet = wave.build_candidate_packet(
        target=wave.reference_authorization(),
        commit_sha="candidate",
        force_incomplete_family="cross_session_data_leak",
    )
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "incomplete_runs:cross_session_data_leak"

