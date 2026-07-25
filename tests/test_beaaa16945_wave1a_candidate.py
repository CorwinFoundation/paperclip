from __future__ import annotations

from dataclasses import replace

import pytest

from evals.aiuc_path_b import base
from evals.aiuc_path_b import consequential_action_confirmation as cac
from evals.aiuc_path_b import cross_session_data_leak as csdl
from evals.aiuc_path_b import wave1a_candidate as wave
from tools.source_identity import repo_head


def _reference_authorization_for(commit_sha: str) -> wave.TargetAuthorization:
    pins = wave.expected_wave1a_hashes_for_commit(commit_sha)
    return replace(
        wave.reference_authorization(),
        candidate_commit_sha=commit_sha,
        corpus_sha256=pins["corpus_sha256"],
        config_sha256=pins["config_sha256"],
    )


def _invalid_signature_authorization_for(commit_sha: str) -> wave.TargetAuthorization:
    return replace(
        _reference_authorization_for(commit_sha),
        authorization_signature=f"ed25519:{wave.AUTHORIZED_SIGNER_KEY_ID}:invalid",
    )


def _head_sha() -> str:
    return repo_head()


def test_wave1a_stale_embedded_reference_signature_fails_closed() -> None:
    commit_sha = _head_sha()
    target = _reference_authorization_for(commit_sha)
    packet = wave.build_candidate_packet(
        target=target,
        commit_sha=commit_sha,
    )
    assert wave.REFERENCE_AUTHORIZATION_SIGNATURE == ""
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_missing:authorization_signature"


def test_wave1a_old_production_key_id_fails_closed() -> None:
    commit_sha = _head_sha()
    old_signature = (
        "ed25519:wave1a-production-authority-2026-07:"
        "prld--NVoNfE4pwC_uf6Fm3BCD3hIeBDmYXS4zFxLxHgt3Cea6QFiNKv5otc9ELex1FdTAYFb4VOFZ45pMQFDg"
    )
    target = replace(
        _reference_authorization_for(commit_sha),
        authorization_signature=old_signature,
    )
    packet = wave.build_candidate_packet(
        target=target,
        commit_sha=commit_sha,
    )
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signer_unknown"


def test_wave1a_old_public_pin_replacement_does_not_authorize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wave,
        "AUTHORIZED_SIGNER_PUBLIC_KEY_B64URL",
        "U89QKWg5b1goQOswjgKFpUpg4119BpZtLNKouItZXWA",
    )
    target = replace(
        _reference_authorization_for(_head_sha()),
        authorization_signature=(
            f"ed25519:{wave.AUTHORIZED_SIGNER_KEY_ID}:"
            "T-BSSd9JUopBSoJ7fXvzdGsxgclR2n7RclX8KdCii0oO8TOPoBKEhenjgE-RSqRp2xA6c6V4boJB_Gf9yLNTBA"
        ),
    )

    packet = wave.build_candidate_packet(
        target=target,
        commit_sha=target.candidate_commit_sha,
    )
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_invalid"


@pytest.mark.skip(reason="Requires a fresh out-of-band production-authority signature.")
def test_wave1a_candidate_counts_and_reported_not_scored_packet() -> None:
    commit_sha = "acb36976e171ace36dff21e9e02f29cc783390f2"
    packet = wave.build_candidate_packet(
        target=_reference_authorization_for(commit_sha),
        commit_sha=commit_sha,
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
    assert packet["signed_manifest"]["preimage_excludes"] == ["signed_manifest"]
    assert packet["signed_manifest"]["signature"] == f"sha256:{packet['signed_manifest']['sha256']}"
    assert packet["reproducibility_summary"]["terminal_verdict_class_invariant"] is True
    assert packet["reproducibility_summary"]["golden_n5_delta"] == {"total": 0, "pillars": 0}
    assert packet["families"]["cross_session_data_leak"]["canary_records"] == ["csdl-20-canary"]
    assert packet["families"]["consequential_action_confirmation"]["canary_records"] == [
        "cac-15-canary"
    ]


@pytest.mark.skip(reason="Requires a fresh out-of-band production-authority signature.")
def test_wave1a_signed_manifest_binds_exact_candidate_sha() -> None:
    commit_sha = "cd349cd4b2d205d69492ad94c4088a7f7fb1f000"
    packet = wave.build_candidate_packet(
        target=_reference_authorization_for(commit_sha),
        commit_sha=commit_sha,
    )

    assert packet["candidate_commit_sha"] == "cd349cd4b2d205d69492ad94c4088a7f7fb1f000"
    assert wave.fail_closed_for_signed_manifest(packet)["fail_closed"] is False

    tampered = {**packet, "candidate_commit_sha": "ffffffffffffffffffffffffffffffffffffffff"}
    result = wave.fail_closed_for_signed_manifest(tampered)
    assert result["fail_closed"] is True
    assert result["failure_code"] == "signed_manifest_verification_failed"


def test_wave1a_production_authority_requires_valid_signature_for_candidate_sha() -> None:
    authorized_sha = _head_sha()
    target = _invalid_signature_authorization_for(authorized_sha)

    packet = wave.build_candidate_packet(target=target, commit_sha=authorized_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_invalid"

    rebuilt_sha = "ffffffffffffffffffffffffffffffffffffffff"
    rebuilt = wave.build_candidate_packet(target=target, commit_sha=rebuilt_sha)
    assert rebuilt["fail_closed"] is True
    assert rebuilt["failure_code"] == "candidate_commit_sha_mismatch"

    attacker_target = replace(target, candidate_commit_sha=rebuilt_sha)
    rebuilt_manifest = wave.build_candidate_packet(target=attacker_target, commit_sha=rebuilt_sha)
    assert rebuilt_manifest["fail_closed"] is True
    assert rebuilt_manifest["failure_code"] == "candidate_commit_sha_unreadable"


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
    commit_sha = _head_sha()
    target = replace(_invalid_signature_authorization_for(commit_sha), authorization_ref="")
    packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["underwriting_state"] == "not_underwriting_active"
    assert packet["failure_code"] == "authorization_missing:authorization_ref"


def test_wave1a_fail_closed_missing_authorization_signature() -> None:
    commit_sha = _head_sha()
    target = replace(_reference_authorization_for(commit_sha), authorization_signature="")
    packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_missing:authorization_signature"


def test_wave1a_fail_closed_invalid_authorization_signature() -> None:
    commit_sha = _head_sha()
    target = replace(
        _reference_authorization_for(commit_sha),
        authorization_signature=f"ed25519:{wave.AUTHORIZED_SIGNER_KEY_ID}:invalid",
    )
    packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_invalid"


def test_wave1a_fail_closed_unknown_authorization_signer() -> None:
    commit_sha = _head_sha()
    signed = _reference_authorization_for(commit_sha)
    signed = _invalid_signature_authorization_for(commit_sha)
    signature = signed.authorization_signature.split(":", 2)[2]
    target = replace(
        signed,
        authorization_signature=f"ed25519:attacker-key:{signature}",
    )
    packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signer_unknown"


def test_wave1a_fail_closed_stale_authorization_signature() -> None:
    commit_sha = _head_sha()
    stale = replace(
        _invalid_signature_authorization_for(commit_sha),
        authorization_expires_at="2026-07-21T23:59:59Z",
    )
    packet = wave.build_candidate_packet(target=stale, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_stale"


def test_wave1a_fail_closed_post_signature_scope_tampering() -> None:
    commit_sha = _head_sha()
    tampered = replace(
        _invalid_signature_authorization_for(commit_sha),
        use_case_scope="attacker-expanded-scope",
    )

    packet = wave.build_candidate_packet(target=tampered, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_signature_invalid"


def test_wave1a_reference_authorized_signature_fails_until_fresh_out_of_band_signature() -> None:
    commit_sha = _head_sha()
    packet = wave.build_candidate_packet(
        target=_reference_authorization_for(commit_sha),
        commit_sha=commit_sha,
    )
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_missing:authorization_signature"


@pytest.mark.skip(reason="Requires a fresh out-of-band production-authority signature.")
def test_wave1a_tampered_signed_manifest_fails_closed() -> None:
    commit_sha = "candidate"
    packet = wave.build_scoring_active_packet(
        commit_sha=commit_sha,
        target=_reference_authorization_for(commit_sha),
    )
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
    commit_sha = _head_sha()
    target = replace(_invalid_signature_authorization_for(commit_sha), declared_tool_inventory=())
    packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "declared_tool_inventory_missing"


def test_wave1a_fail_closed_scope_conflict() -> None:
    commit_sha = _head_sha()
    target = replace(
        _invalid_signature_authorization_for(commit_sha),
        allowed_family_ids=("cross_session_data_leak",),
    )
    packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "family_scope_conflict"


def test_wave1a_fail_closed_hash_mismatch() -> None:
    commit_sha = _head_sha()
    target = _invalid_signature_authorization_for(commit_sha)
    bad = replace(
        target,
        corpus_sha256={**target.corpus_sha256, "cross_session_data_leak": "bad"},
    )
    packet = wave.build_candidate_packet(target=bad, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "corpus_hash_mismatch"


def test_wave1a_fail_closed_mutable_evidence() -> None:
    commit_sha = _head_sha()
    target = replace(_invalid_signature_authorization_for(commit_sha), evidence_immutable=False)
    packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "mutable_evidence"


@pytest.mark.skip(reason="Requires a fresh out-of-band production-authority signature.")
def test_wave1a_fail_closed_evaluator_incomplete() -> None:
    packet = wave.build_candidate_packet(
        target=_reference_authorization_for("candidate"),
        commit_sha="candidate",
        force_incomplete_family="cross_session_data_leak",
    )
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "incomplete_runs:cross_session_data_leak"
