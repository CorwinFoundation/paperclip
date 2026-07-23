import json
import base64
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evals.aiuc_path_b import base
from evals.aiuc_path_b import lane_a_depth as lane
from rubric.scoring import Rubric, score
from tools.active_test_registry import build_registry, count_active_executable


EXPECTED_COUNTS = {
    "out_of_scope_containment": 30,
    "harmful_output_golden": 30,
    "ai_disclosure": 12,
}


def _b64url(blob: bytes) -> str:
    return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def _sign(private_key: Ed25519PrivateKey, payload: dict) -> str:
    signature = private_key.sign(lane._canonical(payload).encode("utf-8"))
    return f"ed25519:{lane.AUTHORIZATION_KEY_ID}:{_b64url(signature)}"


@pytest.fixture()
def external_authority(monkeypatch, tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setattr(lane, "AUTHORIZATION_PUBLIC_KEY_B64URL", _b64url(public_key))

    target = tmp_path / "wave2-target.py"
    target.write_text(
        """#!/usr/bin/env python3
import json
import sys

request = json.loads(sys.stdin.read())
fields = {
    "out_of_scope_containment": "refused_or_redirected",
    "harmful_output_golden": "safely_refused",
    "ai_disclosure": "disclosed_ai",
}
field = fields[request["family"]]
print(json.dumps({
    "status": 200,
    "body": "external target-production adapter response",
    "decision": {field: True},
}, sort_keys=True))
""",
        encoding="utf-8",
    )
    target.chmod(0o755)

    def authorize(commit_sha: str, parent_sha: str) -> lane.TargetProductionAuthorization:
        unsigned = replace(
            lane.reference_authorization(commit_sha=commit_sha, parent_sha=parent_sha),
            target_endpoint=f"exec://{target}",
        )
        return replace(unsigned, signature=_sign(private_key, lane.authorization_preimage(unsigned)))

    def manifest_signature(preimage: dict) -> str:
        return _sign(private_key, preimage)

    return authorize, manifest_signature


def _signed_packet_inputs(commit_sha: str, parent_sha: str, external_authority):
    authorize, sign_manifest = external_authority
    auth = authorize(commit_sha, parent_sha)
    evidence_by_family = {
        family: lane.replicate_authorized_target(family, auth=auth)
        for family in sorted(lane.SPECS)
    }
    family_hashes = {
        family: [run["sha256"] for run in runs]
        for family, runs in evidence_by_family.items()
    }
    preimage = lane.candidate_manifest_preimage(
        commit_sha=commit_sha,
        parent_sha=auth.parent_sha,
        target_authorization=auth,
        execution_evidence_sha256=lane._sha256_obj(evidence_by_family),
        family_evidence_sha256=family_hashes,
    )
    return auth, sign_manifest(preimage)


@pytest.mark.parametrize("family,count", EXPECTED_COUNTS.items())
def test_embedded_cases_are_materially_distinct(family, count):
    spec = lane.SPECS[family]
    prompts = [case.scenario_variants[0] for case in spec.cases]
    assert len(spec.cases) == count
    assert len(set(prompts)) == count
    assert all("Variation" not in prompt for prompt in prompts)
    assert all(len(case.rubric_point_ids) == 2 for case in spec.cases)
    assert any(case.is_canary for case in spec.cases)


@pytest.mark.parametrize("family", EXPECTED_COUNTS)
def test_compliant_reckless_and_gaming_fixtures_discriminate(family):
    spec = lane.SPECS[family]
    policies = {
        "compliant": (lane.compliant_policy(family), base.PASS),
        "reckless": (lane.reckless_policy(family), base.FAIL),
        "gaming": (lane.gaming_policy(family), base.INVALID),
    }
    for policy, expected in policies.values():
        for evidence in base.replicate(spec, policy):
            assert evidence["scored"] is False
            assert evidence["scoring"] == "reported_not_scored"
            assert evidence["corpus_version"] == f"{family}@0.2.0"
            assert len(evidence["fired_probe_ledger"]) == len(spec.cases)
            assert len(evidence["canary_registration"]) >= 1
            assert {row["verdict"] for row in evidence["case_results"]} == {expected}


@pytest.mark.parametrize("family", EXPECTED_COUNTS)
def test_missing_and_non_boolean_decisions_are_invalid(family):
    spec = lane.SPECS[family]
    field = lane.FIELDS[family]
    case = spec.cases[0]
    for decision in ({}, {field: 1}, {field: "true"}, {field: None}):
        assert spec.grader(case, decision) == base.INVALID


def test_registry_and_probe_mappings_are_pinned_and_isolated():
    root = Path(__file__).resolve().parents[1]
    registry = yaml.safe_load(
        (root / "rubric/aiuc_path_b/lane_a_family_registry.yaml").read_text()
    )
    assert registry["ticket_ref"] == "BEAAA-17078"
    assert registry["scanner_version_baseline"] == "0.5.0"
    assert registry["scanner_version_candidate"] == "0.5.0"
    assert registry["contract"] == "are-scanner-api-v1@v1.3.0"
    assert registry["scoring"] == "reported_not_scored"
    assert registry["reporting_status"] == "scoring_active"
    assert {row["family"] for row in registry["families"]} == set(EXPECTED_COUNTS)
    for row in registry["families"]:
        family = row["family"]
        assert row["version_pin"] == f"{family}@0.2.0"
        assert row["corpus_version"] == f"{family}@0.2.0"
        assert row["case_count"] == EXPECTED_COUNTS[family]
        assert row["replication_required"] == 5
        assert row["activation_enabled"] is True
        assert row["scoring_active"] is True
        assert row["underwriting_active"] is True
        mapping = yaml.safe_load((root / f"rubric/probe_mappings/{family}.yaml").read_text())
        assert mapping["version"] == "0.2.0"
        assert mapping["reporting_status"] == "scoring_active"
        assert mapping["scoring"] == "reported_not_scored"
        assert mapping["scoring_active"] is True
        assert mapping["binding"]["contract"] == "are-scanner-api-v1@v1.3.0"
        assert mapping["binding"]["underwriting_cosign_ref"] == "PENDING-BEAAA-17079"


def test_surface_seed_rotates_prompt_but_not_verdict():
    spec = lane.SPECS["out_of_scope_containment"]
    evidence = base.replicate(spec, lane.compliant_policy(spec.name))
    first_case_prompts = [run["case_results"][0]["rendered_prompt"] for run in evidence]
    assert len(set(first_case_prompts)) >= 3
    assert all(run["case_results"][0]["verdict"] == base.PASS for run in evidence)


def test_reported_not_scored_n5_delta_is_exact_zero():
    root = Path(__file__).resolve().parents[1]
    fixtures = root / "tests/fixtures/golden_agents"
    rubric = Rubric.load(root / "rubric/rubric.yaml")
    for fixture in sorted(fixtures.iterdir()):
        agent = json.loads((fixture / "agent.json").read_text())
        if agent.get("status") != "live":
            continue
        expected = json.loads((fixture / "expected_score.json").read_text())
        eval_outputs = json.loads((fixture / "eval_outputs.json").read_text())
        app_path = fixture / "applicability.json"
        applicability = None
        applicability_policy_version = None
        if app_path.exists():
            raw = json.loads(app_path.read_text())
            applicability = raw["applicability_decisions"]
            applicability_policy_version = raw["policy_version"]
        runs = []
        for _seed in range(5):
            runs.append(score(
                rubric,
                eval_outputs,
                snapshot_hash="sha256:" + "a" * 64,
                forensic_bundle_uri=f"https://golden.local/{fixture.name}",
                scored_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
                corpus_version=expected.get("corpus_version"),
                seed=expected.get("seed"),
                model_versions=expected.get("model_versions"),
                applicability_decisions=applicability,
                applicability_policy_version=applicability_policy_version,
            ))
        assert all(run == expected for run in runs)
        totals = [run["bmi"] for run in runs]
        assert max(totals) - min(totals) == 0
        for pillar in runs[0]["pillars"]:
            values = [run["pillars"][pillar]["score"] for run in runs]
            assert max(values) - min(values) == 0


def test_wave2_candidate_manifest_preimage_and_hashes_are_current_master_bound():
    root = Path(__file__).resolve().parents[1]
    commit_sha = "f" * 40
    parent_sha = "d6542eb52b1ea436fbd0d48cf7ebf78dae88188e"
    preimage = lane.candidate_manifest_preimage(
        commit_sha=commit_sha,
        parent_sha=parent_sha,
    )

    assert preimage["ticket_ref"] == "BEAAA-17078"
    assert preimage["source_candidate_sha"] == "b30c095fa9d39467f5137f6b83e796f197eaff89"
    assert preimage["candidate_commit_sha"] == commit_sha
    assert preimage["parent_sha"] == parent_sha
    assert preimage["contract"] == "are-scanner-api-v1@v1.3.0"
    assert preimage["unique_scenario_count"] == 72
    assert preimage["execution_count_k5"] == 360
    assert preimage["scoring_active_count"] == 72
    assert preimage["underwriting_active_count"] == 72
    assert preimage["target_execution"]["target_bound"] is False
    assert preimage["target_execution"]["execution_evidenced"] is False
    assert preimage["score_effect"] == {
        "score": 0,
        "pillar": 0,
        "band": 0,
        "pricing": 0,
        "reason": "zero-weight scoring-active underwriting eligibility gates",
    }
    assert set(preimage["corpus_sha256"]) == set(EXPECTED_COUNTS)
    assert all(
        value.startswith("sha256:") and len(value) == 71
        for value in preimage["corpus_sha256"].values()
    )
    assert set(preimage["config_sha256"]) >= {
        "wave2_registry",
        "out_of_scope_containment_mapping",
        "harmful_output_golden_mapping",
        "ai_disclosure_mapping",
        "active_test_registry",
        "candidate_module",
    }
    assert all(
        value.startswith("sha256:") and len(value) == 71
        for value in preimage["config_sha256"].values()
    )
    assert "Wave 1A" in preimage["rollback"]
    assert (root / "evals/aiuc_path_b/lane_a_depth.py").exists()


def test_wave2_authorized_target_execution_records_request_response_hashes(external_authority):
    commit_sha = "f" * 40
    parent_sha = "d6542eb52b1ea436fbd0d48cf7ebf78dae88188e"
    auth, manifest_signature = _signed_packet_inputs(commit_sha, parent_sha, external_authority)
    packet = lane.build_target_evidence_packet(
        commit_sha=commit_sha,
        parent_sha=parent_sha,
        auth=auth,
        manifest_signature=manifest_signature,
    )

    assert packet["ticket_ref"] == "BEAAA-17091"
    assert packet["unique_scenario_count"] == 72
    assert packet["execution_count_k5"] == 360
    assert packet["signed_manifest_verifies"] is True
    assert lane.verify_signed_manifest(
        packet["signed_manifest_preimage"], packet["signed_manifest"]
    ) is True
    assert packet["signed_manifest"]["preimage_sha256"].startswith("sha256:")
    total_rows = 0
    for family, runs in packet["evidence_by_family"].items():
        assert len(runs) == 5
        for run in runs:
            assert run["target_bound"] is True
            assert run["execution_evidenced"] is True
            assert run["reporting_status"] == "scoring_active"
            assert run["sha256"].startswith("sha256:")
            total_rows += len(run["case_results"])
            for row in run["case_results"]:
                assert row["target_request"]["authorization_ref"].startswith("BEAAA-17091")
                assert row["target_request_sha256"].startswith("sha256:")
                assert row["target_response_sha256"].startswith("sha256:")
                assert row["execution_evidenced"] is True
                assert row["verdict"] == base.PASS
    assert total_rows == 360


def test_wave2_signed_manifest_fails_closed_on_preimage_tamper(external_authority):
    authorize, sign_manifest = external_authority
    auth = authorize("f" * 40, "d6542eb52b1ea436fbd0d48cf7ebf78dae88188e")
    preimage = lane.candidate_manifest_preimage(
        commit_sha=auth.candidate_commit_sha,
        parent_sha=auth.parent_sha,
        target_authorization=auth,
        execution_evidence_sha256="sha256:" + "a" * 64,
        family_evidence_sha256={family: ["sha256:" + "b" * 64] for family in EXPECTED_COUNTS},
    )
    signed = {
        "algorithm": lane.AUTHORIZATION_ALGORITHM,
        "key_id": lane.AUTHORIZATION_KEY_ID,
        "preimage_sha256": lane._sha256_obj(preimage),
        "signature": sign_manifest(preimage),
    }
    assert lane.verify_signed_manifest(preimage, signed) is True
    tampered = {**preimage, "underwriting_active_count": 71}
    assert lane.verify_signed_manifest(tampered, signed) is False


def test_wave2_rejects_local_target_and_missing_external_manifest_signature(external_authority):
    authorize, _sign_manifest = external_authority
    commit_sha = "f" * 40
    parent_sha = "d6542eb52b1ea436fbd0d48cf7ebf78dae88188e"

    local_target = replace(
        authorize(commit_sha, parent_sha),
        target_endpoint="local://backbond/reference-target/wave2-authorized-production-adapter",
    )
    local_target = replace(
        local_target,
        signature=_sign_manifest(lane.authorization_preimage(local_target)),
    )
    packet = lane.build_fail_closed_target_evidence_packet(
        commit_sha=commit_sha,
        parent_sha=parent_sha,
        auth=local_target,
    )
    assert packet["fail_closed"] is True
    assert packet["failure_code"] == "target_endpoint_local_forbidden"

    valid_auth = authorize(commit_sha, parent_sha)
    unsigned_manifest = lane.build_fail_closed_target_evidence_packet(
        commit_sha=commit_sha,
        parent_sha=parent_sha,
        auth=valid_auth,
    )
    assert unsigned_manifest["fail_closed"] is True
    assert unsigned_manifest["failure_code"] == "manifest_signature_missing"


def test_wave2_rejects_parent_mismatch_and_non_200_external_target(
    external_authority,
    tmp_path,
):
    authorize, _sign_manifest = external_authority
    commit_sha = "f" * 40
    parent_sha = "d6542eb52b1ea436fbd0d48cf7ebf78dae88188e"

    auth = authorize(commit_sha, parent_sha)
    mismatched_parent = lane.build_fail_closed_target_evidence_packet(
        commit_sha=commit_sha,
        parent_sha="e" * 40,
        auth=auth,
    )
    assert mismatched_parent["fail_closed"] is True
    assert mismatched_parent["failure_code"] == "parent_sha_mismatch"

    bad_target = tmp_path / "wave2-target-non-200.py"
    bad_target.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({"status": 503, "decision": {"refused_or_redirected": True}}))
""",
        encoding="utf-8",
    )
    bad_target.chmod(0o755)
    bad_auth = replace(auth, target_endpoint=f"exec://{bad_target}")
    bad_auth = replace(bad_auth, signature=_sign_manifest(lane.authorization_preimage(bad_auth)))
    non_200 = lane.build_fail_closed_target_evidence_packet(
        commit_sha=commit_sha,
        parent_sha=parent_sha,
        auth=bad_auth,
    )
    assert non_200["fail_closed"] is True
    assert non_200["failure_code"] == "target_response_status_non_200"


def test_scoring_active_runtime_contract_is_zero_weight():
    for family in EXPECTED_COUNTS:
        runs = base.replicate(lane.SPECS[family], lane.compliant_policy(family))
        scored = lane.scoring_active_runtime_contract(family, runs)
        assert len(scored) == 5
        assert {run["scored"] for run in scored} == {True}
        assert {run["scoring"] for run in scored} == {"reported_not_scored"}
        assert {run["reporting_status"] for run in scored} == {"scoring_active"}
        assert {run["score_weight"] for run in scored} == {0.0}
        assert {run["score_contribution"] for run in scored} == {0}


def test_active_registry_counts_wave2_without_orphaning_wave1a():
    rows = build_registry()
    wave2_rows = [row for row in rows if row["test_id"] in EXPECTED_COUNTS]
    wave1a_rows = [
        row
        for row in rows
        if row["test_id"] in {"cross_session_data_leak", "consequential_action_confirmation"}
    ]

    assert len(wave2_rows) == 72
    assert count_active_executable(wave2_rows) == 72
    assert {row["reporting_status"] for row in wave2_rows} == {"scoring_active"}
    assert {row["scoring_active"] for row in wave2_rows} == {True}
    assert {row["runner"] for row in wave2_rows} == {"bench_wrapper"}
    assert {row["execution_evidence_status"] for row in wave2_rows} == {
        "target_bound_and_evidenced"
    }
    assert len(wave1a_rows) == 35
    assert count_active_executable(wave1a_rows) == 35


def test_wave2_registry_fails_closed_when_retained_evidence_pointer_missing(tmp_path):
    root = Path(__file__).resolve().parents[1]
    mapping_dir = tmp_path / "probe_mappings"
    mapping_dir.mkdir()
    for path in (root / "rubric/probe_mappings").glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        if path.stem in EXPECTED_COUNTS:
            data.pop("execution_evidence", None)
        (mapping_dir / path.name).write_text(yaml.safe_dump(data, sort_keys=False))

    rows = build_registry(mapping_dir)
    wave2_rows = [row for row in rows if row["test_id"] in EXPECTED_COUNTS]
    assert len(wave2_rows) == 72
    assert {row["target_bound"] for row in wave2_rows} == {False}
    assert {row["execution_evidenced"] for row in wave2_rows} == {False}
    assert count_active_executable(wave2_rows) == 0
