from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from shutil import copytree

import yaml

from evals.perils.wave1a import candidate as wave
from tools.active_test_registry import build_registry, count_active_executable
from tools.source_identity import repo_head


ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = ROOT / "rubric" / "probe_mappings"
WAVE1A_SCORING_ACTIVE_FAMILIES = {
    "cross_session_data_leak",
    "consequential_action_confirmation",
}


def _head_sha() -> str:
    return repo_head(ROOT)


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


def test_wave1a_fresh_candidate_requires_out_of_band_signature() -> None:
    commit_sha = _head_sha()
    packet = wave.build_scoring_active_packet(
        target=_reference_authorization_for(commit_sha),
        commit_sha=commit_sha,
    )

    assert wave.REFERENCE_AUTHORIZATION_SIGNATURE == ""
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "authorization_missing:authorization_signature"


def test_wave1a_authorization_preimage_binds_exact_candidate_sha() -> None:
    commit_sha = _head_sha()
    target = _reference_authorization_for(commit_sha)
    preimage = wave.authorization_signature_preimage(target)

    assert preimage["candidate_commit_sha"] == commit_sha
    assert preimage["corpus_sha256"] == wave.expected_wave1a_hashes_for_commit(commit_sha)["corpus_sha256"]
    assert preimage["config_sha256"] == wave.expected_wave1a_hashes_for_commit(commit_sha)["config_sha256"]
    assert preimage["algorithm"] == wave.AUTHORIZATION_SIGNATURE_ALGORITHM


def test_wave1a_stale_or_invalid_signature_fails_closed() -> None:
    commit_sha = _head_sha()
    stale = replace(
        _invalid_signature_authorization_for(commit_sha),
        authorization_expires_at="2026-07-21T23:59:59Z",
    )
    unknown = replace(
        _invalid_signature_authorization_for(commit_sha),
        authorization_signature="ed25519:attacker-key:invalid",
    )

    cases = {
        "authorization_signature_invalid": _invalid_signature_authorization_for(commit_sha),
        "authorization_signature_stale": stale,
        "authorization_signer_unknown": unknown,
    }
    for code, target in cases.items():
        packet = wave.build_candidate_packet(target=target, commit_sha=commit_sha)
        assert packet["fail_closed"] is True
        assert packet["failure_code"] == code


def test_wave1a_candidate_sha_mismatch_and_unreadable_commit_fail_closed() -> None:
    commit_sha = _head_sha()
    target = _invalid_signature_authorization_for(commit_sha)

    rebuilt_sha = "ffffffffffffffffffffffffffffffffffffffff"
    mismatch = wave.build_candidate_packet(target=target, commit_sha=rebuilt_sha)
    assert mismatch["fail_closed"] is True
    assert mismatch["failure_code"] == "candidate_commit_sha_mismatch"

    attacker_target = replace(target, candidate_commit_sha=rebuilt_sha)
    unreadable = wave.build_candidate_packet(target=attacker_target, commit_sha=rebuilt_sha)
    assert unreadable["fail_closed"] is True
    assert unreadable["failure_code"] == "candidate_commit_sha_unreadable"


def test_wave1a_counts_and_k5_invariants_remain_zero_score_movement() -> None:
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

    scored_runs = wave.scoring_active_runtime_contract(
        wave.CROSS_SESSION_DATA_LEAK.name,
        wave.replicate(wave.CROSS_SESSION_DATA_LEAK),
    )
    assert {run["scored"] for run in scored_runs} == {True}
    assert {run["score_weight"] for run in scored_runs} == {0.0}
    assert {run["score_contribution"] for run in scored_runs} == {0}


def test_wave1a_families_discriminate_bad_reference_policies() -> None:
    csdl_bad = wave.verdict_tally(wave.run_family(wave.CROSS_SESSION_DATA_LEAK, wave.reckless_policy, seed=0))
    cac_bad = wave.verdict_tally(
        wave.run_family(wave.CONSEQUENTIAL_ACTION_CONFIRMATION, wave.reckless_policy, seed=0)
    )
    assert csdl_bad[wave.FAIL] == 20
    assert cac_bad[wave.FAIL] == 15


def test_wave1a_fail_closed_controls() -> None:
    commit_sha = _head_sha()
    target = _invalid_signature_authorization_for(commit_sha)
    cases = [
        (replace(target, authorization_ref=""), "authorization_missing:authorization_ref"),
        (replace(target, declared_tool_inventory=()), "declared_tool_inventory_missing"),
        (replace(target, allowed_family_ids=("cross_session_data_leak",)), "family_scope_conflict"),
        (replace(target, corpus_sha256={**target.corpus_sha256, "cross_session_data_leak": "bad"}), "corpus_hash_mismatch"),
        (replace(target, evidence_immutable=False), "mutable_evidence"),
    ]
    for bad_target, code in cases:
        packet = wave.build_candidate_packet(target=bad_target, commit_sha=commit_sha)
        assert packet["fail_closed"] is True
        assert packet["underwriting_state"] == "not_underwriting_active"
        assert packet["failure_code"] == code


def test_wave1a_registry_promotes_only_two_families() -> None:
    rows = build_registry()
    promoted = [row for row in rows if row["test_id"] in WAVE1A_SCORING_ACTIVE_FAMILIES]

    assert len(promoted) == 35
    assert count_active_executable(promoted) == 35
    assert {row["reporting_status"] for row in promoted} == {"scoring_active"}
    assert {row["scoring_active"] for row in promoted} == {True}
    assert {row["activation_blocker"] for row in promoted} == {None}
    for family in WAVE1A_SCORING_ACTIVE_FAMILIES:
        mapping = yaml.safe_load((MAPPING_DIR / f"{family}.yaml").read_text(encoding="utf-8"))
        assert mapping["scoring"] == "reported_not_scored"
        assert mapping["binding"]["underwriting_active"] is True


def test_wave1a_executable_rollback_restores_current_master_equivalent_count(tmp_path: Path) -> None:
    mapping_dir = tmp_path / "probe_mappings"
    copytree(MAPPING_DIR, mapping_dir)

    before_rows = build_registry(mapping_dir)
    before_count = count_active_executable(before_rows)
    assert before_count >= 35

    unrelated_before = {
        (row["test_id"], row["case_id"], row["risk_or_rubric_id"]): row
        for row in before_rows
        if row["test_id"] not in WAVE1A_SCORING_ACTIVE_FAMILIES
        and row["status"] == "active"
        and row["production_runner_executable"] is True
    }

    for family in WAVE1A_SCORING_ACTIVE_FAMILIES:
        path = mapping_dir / f"{family}.yaml"
        row = yaml.safe_load(path.read_text(encoding="utf-8"))
        row["reporting_status"] = "reported_not_scored"
        row["scoring"] = "reported_not_scored"
        binding = dict(row.get("binding") or {})
        binding["scoring_active"] = False
        binding["underwriting_active"] = False
        row["binding"] = binding
        path.write_text(yaml.safe_dump(row, sort_keys=False), encoding="utf-8")

    after_rows = build_registry(mapping_dir)
    assert count_active_executable(after_rows) == before_count - 35

    for family in WAVE1A_SCORING_ACTIVE_FAMILIES:
        restored_rows = [row for row in after_rows if row["test_id"] == family]
        assert count_active_executable(restored_rows) == 0
        mapping = yaml.safe_load((mapping_dir / f"{family}.yaml").read_text(encoding="utf-8"))
        assert mapping["reporting_status"] == "reported_not_scored"
        assert mapping["binding"]["scoring_active"] is False
        assert mapping["binding"]["underwriting_active"] is False

    unrelated_after = {
        (row["test_id"], row["case_id"], row["risk_or_rubric_id"]): row
        for row in after_rows
        if row["test_id"] not in WAVE1A_SCORING_ACTIVE_FAMILIES
        and row["status"] == "active"
        and row["production_runner_executable"] is True
    }
    assert unrelated_after == unrelated_before
