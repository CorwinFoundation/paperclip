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
    assert packet["signed_manifest"]["algorithm"] == "sha256-canonical-json-self-signature"
    assert packet["signed_manifest"]["preimage_excludes"] == [
        "candidate_commit_sha",
        "signed_manifest",
    ]
    assert packet["signed_manifest"]["signature"] == f"sha256:{packet['signed_manifest']['sha256']}"
    assert packet["reproducibility_summary"]["terminal_verdict_class_invariant"] is True
    assert packet["reproducibility_summary"]["golden_n5_delta"] == {"total": 0, "pillars": 0}
    assert packet["families"]["cross_session_data_leak"]["canary_records"] == ["csdl-20-canary"]
    assert packet["families"]["consequential_action_confirmation"]["canary_records"] == [
        "cac-15-canary"
    ]


def test_wave1a_signature_is_stable_across_exact_candidate_sha() -> None:
    first = wave.build_candidate_packet(
        target=wave.reference_authorization(),
        commit_sha="cd349cd4b2d205d69492ad94c4088a7f7fb1f000",
    )
    replacement = wave.build_candidate_packet(
        target=wave.reference_authorization(),
        commit_sha="ffffffffffffffffffffffffffffffffffffffff",
    )

    assert first["candidate_commit_sha"] == "cd349cd4b2d205d69492ad94c4088a7f7fb1f000"
    assert replacement["candidate_commit_sha"] == "ffffffffffffffffffffffffffffffffffffffff"
    assert first["signed_manifest"] == replacement["signed_manifest"]


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


def test_wave1a_fail_closed_missing_authorization_signature() -> None:
    target = replace(wave.reference_authorization(), authorization_signature="")
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_missing:authorization_signature"


def test_wave1a_fail_closed_invalid_authorization_signature() -> None:
    target = replace(
        wave.reference_authorization(),
        authorization_signature=f"ed25519:{wave.AUTHORIZED_SIGNER_KEY_ID}:invalid",
    )
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_invalid"


def test_wave1a_fail_closed_unknown_authorization_signer() -> None:
    signature = wave.reference_authorization().authorization_signature.split(":", 2)[2]
    target = replace(
        wave.reference_authorization(),
        authorization_signature=f"ed25519:attacker-key:{signature}",
    )
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signer_unknown"


def test_wave1a_fail_closed_stale_authorization_signature() -> None:
    stale = replace(
        wave.reference_authorization(),
        authorization_expires_at="2026-07-21T23:59:59Z",
    )
    packet = wave.build_candidate_packet(target=stale, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_stale"


def test_wave1a_fail_closed_post_signature_scope_tampering() -> None:
    tampered = replace(
        wave.reference_authorization(),
        use_case_scope="attacker-expanded-scope",
    )

    packet = wave.build_candidate_packet(target=tampered, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_invalid"


def test_wave1a_reference_authorized_signature_succeeds() -> None:
    packet = wave.build_candidate_packet(target=wave.reference_authorization(), commit_sha="candidate")
    assert packet["fail_closed"] is False


def test_wave1a_tampered_signed_manifest_fails_closed() -> None:
    packet = wave.build_scoring_active_packet(commit_sha="candidate")
    assert wave.fail_closed_for_signed_manifest(packet)["fail_closed"] is False

    tampered = {**packet, "promotion_state": "scoring_active_tampered_after_signature"}
    result = wave.fail_closed_for_signed_manifest(tampered)
    assert result["fail_closed"] is True
    assert result["failure_code"] == "signed_manifest_verification_failed"


def test_wave1a_scoring_active_runtime_contract_separates_substrate_runs() -> None:
    raw_runs = base.replicate(csdl.SPEC, csdl.compliant_policy)
    scored_runs = wave.scoring_active_runtime_contract(csdl.SPEC.name, raw_runs)

    assert {run["scored"] for run in raw_runs} == {False}
    assert {run["scoring"] for run in raw_runs} == {"reported_not_scored"}
    assert {run["scored"] for run in scored_runs} == {True}
    assert {run["scoring"] for run in scored_runs} == {"scoring_active"}
    assert {run["expected_result_contract"] for run in scored_runs} == {
        wave.SCORING_ACTIVE_RUNTIME_CONTRACT
    }
    assert {run["score_weight"] for run in scored_runs} == {0.0}
    assert {run["score_contribution"] for run in scored_runs} == {0}


def test_wave1a_fail_closed_missing_tool_inventory() -> None:
    target = replace(wave.reference_authorization(), declared_tool_inventory=())
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "declared_tool_inventory_missing"


def test_wave1a_fail_closed_scope_conflict() -> None:
    target = replace(
        wave.reference_authorization(),
        allowed_family_ids=("cross_session_data_leak",),
    )
    packet = wave.build_candidate_packet(target=target, commit_sha="candidate")
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "family_scope_conflict"


def test_wave1a_fail_closed_hash_mismatch() -> None:
    target = wave.reference_authorization()
    bad = replace(
        target,
        corpus_sha256={**target.corpus_sha256, "cross_session_data_leak": "bad"},
    )
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
