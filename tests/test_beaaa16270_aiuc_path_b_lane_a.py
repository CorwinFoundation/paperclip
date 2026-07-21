import json
from pathlib import Path

import yaml

from harness.bench.aiuc_path_b_lane_a import PACKS, corpus_sha256, embedded_cases, is_applicable, run
from tools.active_test_registry import build_registry


ROOT = Path(__file__).resolve().parents[1]


def test_all_families_have_exact_case_counts_unique_cases_and_dual_namespace_mappings():
    registry = json.loads((ROOT / "config/aiuc_path_b_lane_a_registry.json").read_text())
    rows = {row["family"]: row for row in registry["families"]}
    assert set(rows) == set(PACKS)
    for family, spec in PACKS.items():
        cases = embedded_cases(family)
        assert len(cases) == spec.count == rows[family]["case_count"]
        assert len({case["case_id"] for case in cases}) == spec.count
        assert rows[family]["rubric_point_ids"] == list(spec.rubric_ids)
        assert rows[family]["rubric_point_ids"][0].startswith("P")
        assert rows[family]["rubric_point_ids"][1].count(".") >= 2


def test_every_family_is_reported_not_scored_k5_and_anti_gaming_registered():
    registry = json.loads((ROOT / "config/aiuc_path_b_lane_a_registry.json").read_text())
    for row in registry["families"]:
        assert row["scoring_active"] is False
        assert row["status"] == "reported-not-scored"
        assert row["replication_required"] == 5
        assert row["fired_probe_ledger"] and row["canary_registered"] and row["surface_seed_rotation"]


def test_every_family_has_probe_mapping_yaml_and_full_scan_manifest_state():
    for family, spec in PACKS.items():
        mapping = yaml.safe_load((ROOT / "rubric/probe_mappings" / f"{family}.yaml").read_text())
        assert mapping["case_count"] == spec.count
        assert mapping["rubric_point_ids"] == list(spec.rubric_ids)
        assert mapping["status"] == "reported-not-scored"
        assert mapping["scoring_active"] is False
    manifest = json.loads((ROOT / "config/full_scan_reported_not_scored.json").read_text())
    assert set(manifest["families"]) == {
        "out_of_scope_containment", "harmful_output_golden", "ai_disclosure", "voice_adversarial_pack"
    }
    assert set(manifest["activation_blocked"]) == {"cbrn_refusal", "cyber_misuse_refusal"}


def test_refusal_corpora_are_non_operational_and_activation_blocked_for_compliance():
    banned = ("step-by-step", "payload", "exploit", "quantity", "parameter", "procedure", "optimize")
    registry = json.loads((ROOT / "config/aiuc_path_b_lane_a_registry.json").read_text())
    rows = {row["family"]: row for row in registry["families"]}
    for family in ("cbrn_refusal", "cyber_misuse_refusal"):
        text = " ".join(case["prompt"].lower() for case in embedded_cases(family))
        assert not any(term in text for term in banned)
        assert rows[family]["active"] is False
        assert rows[family]["activation_blocker"] == "compliance_corpus_hash_review"
        assert corpus_sha256(family).startswith("sha256:")


def test_voice_gate_is_deterministic_and_cannot_award_points():
    assert is_applicable("voice_adversarial_pack", {}) == (False, "voice_audio_ingress_absent")
    assert is_applicable("voice_adversarial_pack", {"input_modalities": ["audio"], "audio_ingress_testable": True}) == (True, "voice_audio_ingress_present")
    result = run("voice_adversarial_pack", {}, capabilities={})
    assert result["status"] == "not_applicable"
    assert result["scoring_active"] is False and result["reported_not_scored"] is True


def test_n5_outputs_are_byte_identical_and_score_delta_is_exactly_zero():
    for family, spec in PACKS.items():
        responses = {case["case_id"]: spec.expected_label for case in embedded_cases(family)}
        capabilities = {"input_modalities": ["voice"], "audio_ingress_testable": True}
        outputs = [json.dumps(run(family, responses, capabilities=capabilities), sort_keys=True) for _ in range(5)]
        assert len(set(outputs)) == 1
        assert json.loads(outputs[0])["scoring_active"] is False


def test_both_manifests_are_consumed_by_production_registry_and_cannot_be_orphaned():
    lane_manifest = json.loads((ROOT / "config/aiuc_path_b_lane_a_registry.json").read_text())
    full_scan = json.loads((ROOT / "config/full_scan_reported_not_scored.json").read_text())
    rows = build_registry(ROOT / "rubric" / "probe_mappings")
    discovered = {row["test_id"] for row in rows}
    eligible = set(full_scan["families"])
    blocked = set(full_scan["activation_blocked"])
    assert eligible | blocked == {row["family"] for row in lane_manifest["families"]}
    assert eligible | blocked <= discovered
    for family in eligible:
        family_rows = [row for row in rows if row["test_id"] == family]
        assert family_rows and all(row["status"] == "active" for row in family_rows)
        assert all(row["production_runner_executable"] for row in family_rows)
        assert all(row["reporting_status"] == "reported_not_scored" for row in family_rows)
    for family in blocked:
        family_rows = [row for row in rows if row["test_id"] == family]
        assert family_rows and all(row["status"] == "quarantined" for row in family_rows)
        assert all(row["activation_blocker"] for row in family_rows)


def test_exact_zero_artifact_has_five_seeded_score_vectors_and_immutable_hashes():
    proof = json.loads((ROOT / "artifacts/beaaa-16284/n5-exact-zero.json").read_text())
    assert [row["seed"] for row in proof["runs"]] == list(range(5))
    assert proof["gate"] == "PASS_EXACT_ZERO"
    assert all(set(row["delta"].values()) == {0.0} for row in proof["runs"])
    assert proof["baseline"]["sha256"]
    assert set(proof["corpus_versions"]) == {f"{family}@0.1.0" for family in PACKS}
    assert all(value.startswith("sha256:") and len(value) == 71 for value in proof["corpus_versions"].values())
