"""BEAAA-39490: pinned OSS rows are runnable fixed-form reference tests."""
import json
from pathlib import Path

from api.full_scan_packs import resolve_pack
from scanners.oss_imported_conversation import (
    JBB_CASES, JBB_COMMIT, JBB_PACK_ID,
    XSTEST_CASES, XSTEST_COMMIT, XSTEST_PACK_ID,
    STRONGREJECT_CASES, STRONGREJECT_COMMIT, STRONGREJECT_PACK_ID,
    HARMBENCH_CASES, HARMBENCH_COMMIT, HARMBENCH_PACK_ID,
    MULTIJAIL_CASES, MULTIJAIL_COMMIT, MULTIJAIL_PACK_ID,
    CYBERSECEVAL_CASES, CYBERSECEVAL_COMMIT, CYBERSECEVAL_PACK_ID,
)

ROOT = Path(__file__).resolve().parents[1]


def test_jbb_pin_licence_drop_log_and_count():
    root = ROOT / "third_party" / "oss-imports" / "jailbreakbench"
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    assert provenance["commit"] == JBB_COMMIT
    assert provenance["licence"] == "MIT"
    assert provenance["source_rows"] == 200
    assert provenance["rows_imported"] == 197
    assert sum(provenance["rows_dropped"].values()) == 3
    assert (root / "LICENSE").read_text().startswith("MIT License")


def test_jbb_rows_are_fixed_report_only_cases():
    assert resolve_pack(JBB_PACK_ID) == JBB_CASES
    assert len(JBB_CASES) == 197
    assert {case.family_id for case in JBB_CASES} == {"harmful_content", "over_refusal"}
    assert all(case.form_policy == "fixed" for case in JBB_CASES)
    assert sum(case.counts_as_unique_tests for case in JBB_CASES) == 197
    assert all(case.calibration_status == "uncalibrated" for case in JBB_CASES)
    assert all(case.provenance["licence"] == "MIT" for case in JBB_CASES)
    assert all(case.provenance["source"].endswith(JBB_COMMIT) for case in JBB_CASES)


def test_xstest_pin_licence_count_and_fixed_rows():
    root = ROOT / "third_party" / "oss-imports" / "xstest"
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    assert provenance["commit"] == XSTEST_COMMIT
    assert provenance["licence"] == "CC-BY-4.0"
    assert provenance["source_rows"] == 450
    assert provenance["rows_imported"] == 441
    assert len(provenance["rows_dropped"]["near_duplicate_jaccard_gt_0_6"]) == 9
    assert resolve_pack(XSTEST_PACK_ID) == XSTEST_CASES
    assert len(XSTEST_CASES) == 441
    assert sum(c.family_id == "over_refusal" for c in XSTEST_CASES) == 250
    assert sum(c.family_id == "harmful_content" for c in XSTEST_CASES) == 191
    assert all(c.form_policy == "fixed" and c.counts_as_unique_tests == 1 for c in XSTEST_CASES)
    assert all(c.calibration_status == "uncalibrated" for c in XSTEST_CASES)


def test_strongreject_pin_rubric_drop_log_and_fixed_rows():
    root = ROOT / "third_party" / "oss-imports" / "strongreject"
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    rubric = (root / "judge-rubric.txt").read_text()
    assert provenance["commit"] == STRONGREJECT_COMMIT
    assert provenance["licence"] == "MIT"
    assert provenance["source_rows"] == 313
    assert provenance["rows_imported"] == 309
    assert provenance["rows_dropped"]["prohibited_minor_sexual_content"] == 2
    assert len(provenance["rows_dropped"]["exact_duplicate_prompt_sha256"]) == 2
    assert "how convincing" in rubric and "how specific" in rubric
    assert resolve_pack(STRONGREJECT_PACK_ID) == STRONGREJECT_CASES
    assert len(STRONGREJECT_CASES) == 309
    assert all(c.form_policy == "fixed" and c.counts_as_unique_tests == 1 for c in STRONGREJECT_CASES)
    assert all(c.judge_rubric == rubric for c in STRONGREJECT_CASES)
    assert all(c.provenance["licence"] == "MIT" for c in STRONGREJECT_CASES)


def test_harmbench_text_only_pin_drop_log_and_fixed_rows():
    root = ROOT / "third_party" / "oss-imports" / "harmbench"
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    assert provenance["commit"] == HARMBENCH_COMMIT
    assert provenance["licence"] == "MIT"
    assert provenance["source_rows"] == 400
    assert provenance["eligible_rows"] == 300
    assert provenance["rows_imported"] == 272
    assert provenance["rows_dropped"]["copyright"] == 100
    assert provenance["rows_dropped"]["prohibited_minor_sexual_content"] == 1
    assert len(provenance["rows_dropped"]["near_or_exact_duplicate_behavior_ids"]) == 27
    assert resolve_pack(HARMBENCH_PACK_ID) == HARMBENCH_CASES
    assert len(HARMBENCH_CASES) == 272
    assert sum(c.family_id == "harmful_content" for c in HARMBENCH_CASES) == 180
    assert sum(c.family_id == "prompt_injection_indirect" for c in HARMBENCH_CASES) == 92
    assert all(c.form_policy == "fixed" and c.counts_as_unique_tests == 1 for c in HARMBENCH_CASES)


def test_multijail_pin_languages_and_single_test_pairs():
    root = ROOT / "third_party" / "oss-imports" / "multijail"
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    assert provenance["commit"] == MULTIJAIL_COMMIT
    assert provenance["licence"] == "MIT"
    assert provenance["source_rows"] == 315
    assert provenance["rows_imported"] == 310
    assert provenance["rows_dropped"]["near_or_exact_duplicate_ids"] == ["9", "305", "306", "307", "312"]
    assert provenance["languages_imported"] == ["en", "ar"]
    assert provenance["requested_languages_unavailable"] == ["he", "es", "fr", "de", "pt"]
    assert resolve_pack(MULTIJAIL_PACK_ID) == MULTIJAIL_CASES
    assert len(MULTIJAIL_CASES) == 310
    assert all(c.turns and c.pair_turns and c.check == "pair" for c in MULTIJAIL_CASES)
    assert sum(c.counts_as_unique_tests for c in MULTIJAIL_CASES) == 310


def test_cyberseceval_pin_source_rubric_and_fixed_rows():
    root = ROOT / "third_party" / "oss-imports" / "cyberseceval"
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    assert provenance["commit"] == CYBERSECEVAL_COMMIT
    assert provenance["licence"] == "MIT"
    assert provenance["source_rows"] == 251
    assert provenance["rows_imported"] == 205
    assert len(provenance["rows_dropped"]["near_or_exact_duplicate_prompt_ids"]) == 46
    assert resolve_pack(CYBERSECEVAL_PACK_ID) == CYBERSECEVAL_CASES
    assert len(CYBERSECEVAL_CASES) == 205
    assert sum(c.family_id == "prompt_injection_direct" for c in CYBERSECEVAL_CASES) == 178
    assert sum(c.family_id == "prompt_injection_indirect" for c in CYBERSECEVAL_CASES) == 27
    assert all("Source judge question:" in (c.judge_rubric or "") for c in CYBERSECEVAL_CASES)
    assert all(c.form_policy == "fixed" and c.counts_as_unique_tests == 1 for c in CYBERSECEVAL_CASES)
