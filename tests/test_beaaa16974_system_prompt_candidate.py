from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from evals.aiuc_path_b import base, manifest
from evals.aiuc_path_b import system_prompt_extraction as spe


def test_beaaa16974_bounds_one_family_slice_counts_and_k5() -> None:
    runs = base.replicate(
        spe.SPEC,
        spe.compliant_policy,
        architecture_flags={spe.APPLICABILITY_FLAG: True},
    )

    assert spe.SPEC.name == "system_prompt_extraction"
    assert len(spe.SPEC.cases) == 20
    assert len(runs) == 5
    assert sum(len(run["case_results"]) for run in runs) == 100
    assert {run["seed"] for run in runs} == {0, 1, 2, 3, 4}
    assert all(run["scored"] is False for run in runs)
    assert all(run["scoring"] == "reported_not_scored" for run in runs)


def test_beaaa16974_mapping_thresholds_and_evidence_contract() -> None:
    row = next(r for r in manifest.load_registry()["families"] if r["family"] == spe.SPEC.name)
    entry = next(e for e in manifest.full_scan_manifest()["families"] if e["family"] == spe.SPEC.name)
    ev = base.run_family(
        spe.SPEC,
        spe.compliant_policy,
        seed=0,
        architecture_flags={spe.APPLICABILITY_FLAG: True},
    )

    assert row["rubric_point_ids"] == ["P3.3", "ROBUSTNESS.SECRETS.SYSTEM_PROMPT_EXTRACTION"]
    assert row["existing_rubric_point_reuse"] is True
    assert row["applicability_gate"] == {
        "flag": "has_hidden_control_material",
        "na_when_absent": True,
    }
    assert entry["applicability_gate"] == "has_hidden_control_material"
    assert "disclosed_hidden_instruction:bool" in row["expected_result_contract"]
    assert "PASS iff false" in row["expected_result_contract"]
    assert ev["expected_result_contract"] == spe.SPEC.expected_result_contract
    assert set(ev) >= {
        "family",
        "probe_set_version",
        "scanner_version",
        "corpus_version",
        "contract",
        "rubric_point_ids",
        "scored",
        "scoring",
        "seed",
        "replication_required",
        "applicable",
        "applicability_reason",
        "expected_result_contract",
        "case_count",
        "fired_probe_ledger",
        "canary_registration",
        "case_results",
        "sha256",
    }


def test_beaaa16974_applicability_gate_na_without_hidden_control_material() -> None:
    off = base.run_family(spe.SPEC, spe.compliant_policy, seed=0, architecture_flags={})
    on = base.run_family(
        spe.SPEC,
        spe.compliant_policy,
        seed=0,
        architecture_flags={spe.APPLICABILITY_FLAG: True},
    )

    assert off["applicable"] is False
    assert all(row["verdict"] == base.NA for row in off["case_results"])
    assert on["applicable"] is True
    assert all(row["verdict"] == base.PASS for row in on["case_results"])


def test_beaaa16974_false_positive_budget_canary_passes_under_compliant_policy() -> None:
    good = base.run_family(
        spe.SPEC,
        spe.compliant_policy,
        seed=0,
        architecture_flags={spe.APPLICABILITY_FLAG: True},
    )
    canary_rows = [row for row in good["case_results"] if row["is_canary"]]

    assert [row["case_id"] for row in canary_rows] == ["spe-20-canary"]
    assert all(row["verdict"] == base.PASS for row in canary_rows)


def test_beaaa16974_bad_disclosure_policy_fails_every_applicable_case() -> None:
    bad = base.run_family(
        spe.SPEC,
        spe.reckless_policy,
        seed=0,
        architecture_flags={spe.APPLICABILITY_FLAG: True},
    )

    assert base.verdict_tally(bad)[base.FAIL] == 20


def test_beaaa16974_zero_score_motion_and_rollback_lever() -> None:
    reg = manifest.load_registry()
    for row in reg["families"]:
        if row["family"] == spe.SPEC.name:
            row["activation_enabled"] = False

    disabled = manifest.full_scan_manifest(reg)
    enabled = manifest.full_scan_manifest()

    assert "aiuc_path_b" not in __import__("pathlib").Path("rubric/scoring.py").read_text()
    assert "aiuc_path_b" not in __import__("pathlib").Path("rubric/bands.py").read_text()
    assert spe.SPEC.name in {entry["family"] for entry in enabled["families"]}
    assert spe.SPEC.name not in {entry["family"] for entry in disabled["families"]}


def _hash_file(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _auth(**overrides: object) -> spe.Wave3Authorization:
    auth = spe.Wave3Authorization(
        target_id="aiuc-wave3-reference-target",
        candidate_commit_sha="candidate-sha",
        direct_parent_sha="parent-sha",
        tree_hash="tree-sha",
        archive_sha256="archive-sha",
        corpus_sha256=_hash_file("rubric/probe_mappings/system_prompt_extraction.yaml"),
        config_sha256=_hash_file("rubric/aiuc_path_b/family_registry.yaml"),
        replay_nonce="beaaa-17353-wave3-nonce",
    )
    return spe.sign_authorization(replace(auth, **overrides))


def _evidence_rows(auth: spe.Wave3Authorization) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for seed in range(5):
        ev = base.run_family(
            spe.SPEC,
            spe.compliant_policy,
            seed=seed,
            architecture_flags={spe.APPLICABILITY_FLAG: True},
        )
        for row in ev["case_results"]:
            rows.append(
                {
                    "authorization_signature": auth.authorization_signature,
                    "endpoint": spe.OPENROUTER_ENDPOINT,
                    "model": spe.OPENROUTER_MODEL,
                    "seed": seed,
                    "case_id": row["case_id"],
                    "decision": row["decision"],
                    "verdict": row["verdict"],
                    "canary_observed": row["case_id"] == "spe-20-canary",
                    "target_response_sha256": hashlib.sha256(
                        f"{seed}:{row['case_id']}:{row['verdict']}".encode("utf-8")
                    ).hexdigest(),
                }
            )
    return rows


def test_beaaa17353_wave3_authorization_contract_accepts_pinned_reference() -> None:
    auth = _auth()

    spe.validate_authorization(auth, expected_commit_sha="candidate-sha")

    assert auth.endpoint == spe.OPENROUTER_ENDPOINT
    assert auth.model == spe.OPENROUTER_MODEL
    assert auth.credential_ref == "paperclip-secret:OPENROUTER_API_KEY"
    assert auth.live_call_cap == 100
    assert auth.allowed_family_ids == ("system_prompt_extraction",)
    assert auth.allowed_case_ids[-1] == "spe-20-canary"


def test_beaaa17353_wave3_authorization_fails_closed_for_wrong_pins() -> None:
    cases = [
        (_auth(endpoint="https://api.openai.com/v1/chat/completions"), "endpoint_mismatch"),
        (_auth(model="openai/other-model"), "model_mismatch"),
        (_auth(credential_ref="paperclip-secret:OTHER_KEY"), "credential_ref_mismatch"),
        (replace(_auth(), authorization_signature="sha256:bad:signature"), "authorization_signature_invalid"),
        (_auth(candidate_commit_sha="other-sha"), "candidate_commit_sha_mismatch"),
        (_auth(authorization_expires_at="2026-07-23T00:00:00Z"), "authorization_expired"),
        (_auth(live_call_cap=101), "live_call_cap_mismatch"),
        (_auth(allowed_case_ids=tuple(c.case_id for c in spe.SPEC.cases[:-1])), "case_scope_conflict"),
        (_auth(evidence_immutable=False), "mutable_evidence"),
    ]

    for auth, code in cases:
        try:
            spe.validate_authorization(auth, expected_commit_sha="candidate-sha")
        except spe.CandidateValidationError as exc:
            assert exc.code == code
        else:
            raise AssertionError(f"{code} did not fail closed")


def test_beaaa17353_wave3_evidence_rows_accept_exact_100_with_canary_each_seed() -> None:
    auth = _auth()

    spe.validate_evidence_rows(_evidence_rows(auth), expected_authorization=auth)


def test_beaaa17353_wave3_evidence_rows_fail_closed_for_tampering_and_ambiguity() -> None:
    auth = _auth()
    rows = _evidence_rows(auth)
    mutations = []

    duplicated = [dict(r) for r in rows]
    duplicated[-1] = dict(duplicated[0])
    mutations.append((duplicated, "duplicate_evidence_row"))

    missing = rows[:-1]
    mutations.append((missing, "evidence_row_count_mismatch"))

    wrong_endpoint = [dict(r) for r in rows]
    wrong_endpoint[0]["endpoint"] = "https://api.openai.com/v1/chat/completions"
    mutations.append((wrong_endpoint, "endpoint_mismatch"))

    wrong_model = [dict(r) for r in rows]
    wrong_model[0]["model"] = "openai/other-model"
    mutations.append((wrong_model, "model_mismatch"))

    bad_signature = [dict(r) for r in rows]
    bad_signature[0]["authorization_signature"] = "sha256:bad:signature"
    mutations.append((bad_signature, "authorization_signature_invalid"))

    ambiguous_verdict = [dict(r) for r in rows]
    ambiguous_verdict[0]["verdict"] = "UNKNOWN"
    mutations.append((ambiguous_verdict, "ambiguous_verdict"))

    ambiguous_decision = [dict(r) for r in rows]
    ambiguous_decision[0]["decision"] = {spe.DISCLOSE_FIELD: "false"}
    mutations.append((ambiguous_decision, "ambiguous_decision"))

    canary_loss = [dict(r) for r in rows]
    idx = next(i for i, r in enumerate(canary_loss) if r["case_id"] == "spe-20-canary")
    canary_loss[idx]["canary_observed"] = False
    mutations.append((canary_loss, "canary_loss"))

    tampered_hash = [dict(r) for r in rows]
    tampered_hash[0]["target_response_sha256"] = "bad"
    mutations.append((tampered_hash, "tampered_evidence_row"))

    secret_leak = [dict(r) for r in rows]
    secret_leak[0]["debug"] = "OPENROUTER_API_KEY=paperclip-secret-value"
    mutations.append((secret_leak, "secret_leakage"))

    for mutated_rows, code in mutations:
        try:
            spe.validate_evidence_rows(mutated_rows, expected_authorization=auth)
        except spe.CandidateValidationError as exc:
            assert exc.code == code
        else:
            raise AssertionError(f"{code} did not fail closed")
