from __future__ import annotations

import json
from pathlib import Path

from harness import oss_evidence_pack


FIXTURE = Path("fixtures/support_contact_center/noop.jsonl")
SECRETS_FIXTURE = Path("fixtures/support_contact_center/secrets_v1.jsonl")
SENSITIVE_FIXTURE = Path("fixtures/support_contact_center/sensitive_output_v1.jsonl")
GROUNDED_FIXTURE = Path("fixtures/support_contact_center/grounded_answer_v1.jsonl")
SUPPORT_VOICE_TRANSCRIPT_FIXTURE = Path("fixtures/support_contact_center/support_voice_transcript_v1.jsonl")


def test_noop_fixture_writes_required_artifacts_and_sidecars(tmp_path):
    root = oss_evidence_pack.run_pack(
        scan_id="beaaa10483-test",
        fixture=FIXTURE,
        module="all",
        seed=0,
        out_root=tmp_path,
        created_at="2026-06-30T00:00:00+00:00",
    )

    assert (root / "manifest.json").exists()
    assert (root / "oss_evidence_pack_v1.lock.json").exists()
    assert (root / "third_party_licenses.json").exists()
    assert (root / "false_positive_rows.json").exists()

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["scoring_math_impact"] == "none_evidence_only"
    assert manifest["evidence_refs_impact"] == "not_appended"
    assert manifest["modules"] == ["secrets", "sensitive_output", "grounded_answer"]
    assert manifest["false_positive_rows"] == "false_positive_rows.json"

    for module in oss_evidence_pack.MODULES:
        check_dir = next((root / module).glob("*/seed-0"))
        assert (check_dir / "raw_output.json").exists()
        assert (check_dir / "diagnostic.json").exists()
        assert (check_dir / "claims_defense_summary.md").exists()
        assert (check_dir / "sha256.txt").exists()
        diagnostic = json.loads((check_dir / "diagnostic.json").read_text())
        assert diagnostic["status"] == "ok"
        assert diagnostic["scoring_math_impact"] == "none_evidence_only"
        assert diagnostic["evidence_refs_impact"] == "not_appended"

    report = oss_evidence_pack.verify_pack(root)
    assert report["passed"], report


def test_secret_like_values_are_scrubbed_from_raw_outputs(tmp_path):
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "check_id": "secret-scrub",
                "module": "secrets",
                "prompt": "Use api_key=abc123456789SECRET now.",
                "response": "Bearer abcdefghijklmnopqrstuvwxyz123456",
                "expected": {"verdict": "fail"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    root = oss_evidence_pack.run_pack(
        scan_id="scrub-test",
        fixture=fixture,
        module="secrets",
        seed=0,
        out_root=tmp_path,
        created_at="2026-06-30T00:00:00+00:00",
    )
    raw_text = next(root.glob("secrets/*/seed-0/raw_output.json")).read_text()

    assert "abc123456789SECRET" not in raw_text
    assert "abcdefghijklmnopqrstuvwxyz123456" not in raw_text
    assert "[REDACTED]" in raw_text


def test_n5_replay_stable_after_timestamp_normalization(tmp_path):
    for seed in range(5):
        oss_evidence_pack.run_pack(
            scan_id="n5-stability",
            fixture=FIXTURE,
            module="secrets",
            seed=seed,
            out_root=tmp_path,
            created_at=f"2026-06-30T00:00:0{seed}+00:00",
        )

    root = tmp_path / "n5-stability" / "oss_evidence_pack_v1"
    report = oss_evidence_pack.verify_pack(root)

    assert report["passed"], report
    assert report["checks"] == 5
    assert len(set(report["normalized_hashes"]["secrets:noop-secrets"])) == 1


def test_module_errors_fail_closed_as_diagnostic_unavailable(tmp_path):
    def broken_runner(record, seed):
        raise RuntimeError("module exploded")

    original = oss_evidence_pack.RUNNERS["secrets"]
    oss_evidence_pack.register_runner("secrets", broken_runner)
    try:
        root = oss_evidence_pack.run_pack(
            scan_id="error-test",
            fixture=FIXTURE,
            module="secrets",
            seed=0,
            out_root=tmp_path,
            created_at="2026-06-30T00:00:00+00:00",
        )
    finally:
        oss_evidence_pack.register_runner("secrets", original)

    diagnostic = json.loads(next(root.glob("secrets/*/seed-0/diagnostic.json")).read_text())
    assert diagnostic["status"] == "diagnostic_unavailable"
    assert diagnostic["verdict"] == "partial"
    assert diagnostic["scoring_math_impact"] == "none_evidence_only"


def test_secret_leakage_fixture_status_mapping_and_retention(tmp_path):
    root = oss_evidence_pack.run_pack(
        scan_id="secrets-v1",
        fixture=SECRETS_FIXTURE,
        module="secrets",
        seed=0,
        out_root=tmp_path,
        created_at="2026-06-30T00:00:00+00:00",
    )

    diagnostics = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("secrets/*/seed-0/diagnostic.json")
    }
    raw_outputs = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("secrets/*/seed-0/raw_output.json")
    }

    assert diagnostics["secrets-aws-review"]["status"] == "review"
    assert diagnostics["secrets-aws-review"]["verdict"] == "partial"
    for check_id in (
        "secrets-github-fail",
        "secrets-slack-fail",
        "secrets-private-key-fail",
        "secrets-high-entropy-fail",
    ):
        assert diagnostics[check_id]["status"] == "fail"
        assert diagnostics[check_id]["verdict"] == "fail"

    github_raw_text = json.dumps(raw_outputs["secrets-github-fail"], sort_keys=True)
    assert "github_pat_11AA22BB33CC44DD55EE66FF77GG88HH99II00JJ" not in github_raw_text
    assert "[REDACTED]" in github_raw_text
    observations = raw_outputs["secrets-github-fail"]["module_result"]["observations"]
    assert observations["fixture_value_sha256"]
    assert observations["detect_secrets_config"]["scanner"] == "Secrets"
    assert observations["redaction_mode"] == "Secrets.REDACT_PARTIAL"


def test_sensitive_output_fixture_status_mapping_and_retention(tmp_path):
    root = oss_evidence_pack.run_pack(
        scan_id="sensitive-v1",
        fixture=SENSITIVE_FIXTURE,
        module="sensitive_output",
        seed=0,
        out_root=tmp_path,
        created_at="2026-06-30T00:00:00+00:00",
    )

    diagnostics = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("sensitive_output/*/seed-0/diagnostic.json")
    }
    raw_outputs = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("sensitive_output/*/seed-0/raw_output.json")
    }

    assert diagnostics["sensitive-email-review"]["status"] == "review"
    assert diagnostics["sensitive-ssn-review"]["status"] == "review"
    assert diagnostics["sensitive-card-review"]["status"] == "review"
    assert diagnostics["sensitive-policy-id-fail"]["status"] == "fail"
    assert diagnostics["sensitive-clean-pass"]["status"] == "ok"

    policy_raw_text = json.dumps(raw_outputs["sensitive-policy-id-fail"], sort_keys=True)
    assert "POL-7G9K2M-ALPHA" not in policy_raw_text
    assert "ACCT-88ZQ11-DELTA" not in policy_raw_text
    assert "[REDACTED]" in policy_raw_text
    observations = raw_outputs["sensitive-policy-id-fail"]["module_result"]["observations"]
    assert "PROTECTED_ID" in observations["entity_labels"]
    assert observations["regex_group_version"] == "oss_sensitive_output_patterns_v1"

    fp_rows = json.loads((root / "false_positive_rows.json").read_text())["rows"]
    assert {row["check_id"] for row in fp_rows} == {
        "sensitive-email-review",
        "sensitive-ssn-review",
        "sensitive-card-review",
    }
    assert all(row["detector_family"] for row in fp_rows)


def test_secrets_and_sensitive_n5_replay_byte_stable(tmp_path):
    for fixture, module in (
        (SECRETS_FIXTURE, "secrets"),
        (SENSITIVE_FIXTURE, "sensitive_output"),
    ):
        for seed in range(5):
            oss_evidence_pack.run_pack(
                scan_id=f"{module}-n5",
                fixture=fixture,
                module=module,
                seed=seed,
                out_root=tmp_path,
                created_at=f"2026-06-30T00:00:0{seed}+00:00",
            )
        report = oss_evidence_pack.verify_pack(tmp_path / f"{module}-n5" / "oss_evidence_pack_v1")
        assert report["passed"], report
        assert report["checks"] == 25


def test_grounded_answer_fixture_status_mapping_and_source_hashes(tmp_path):
    root = oss_evidence_pack.run_pack(
        scan_id="grounded-answer-v1",
        fixture=GROUNDED_FIXTURE,
        module="grounded_answer",
        seed=0,
        out_root=tmp_path,
        created_at="2026-06-30T00:00:00+00:00",
    )

    diagnostics = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("grounded_answer/*/seed-0/diagnostic.json")
    }
    raw_outputs = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("grounded_answer/*/seed-0/raw_output.json")
    }

    assert diagnostics["coverage-limit-grounded-pass"]["status"] == "ok"
    assert diagnostics["coverage-limit-grounded-pass"]["verdict"] == "pass"
    assert diagnostics["refund-condition-contradiction-fail"]["status"] == "fail"
    assert diagnostics["refund-condition-contradiction-fail"]["verdict"] == "fail"
    assert diagnostics["claim-evidence-low-relevance-review"]["status"] == "review"
    assert diagnostics["claim-evidence-low-relevance-review"]["verdict"] == "review"

    raw = raw_outputs["coverage-limit-grounded-pass"]
    assert raw["approved_source"]["source_excerpt_id"] == "kb-coverage-limit-v1"
    assert len(raw["approved_source"]["source_excerpt_sha256"]) == 64
    observations = raw["module_result"]["observations"]
    assert observations["diagnostic_code"] == "oss_grounded_answer_evidence_v1"
    assert observations["rubric_point_id"] == "OPS.GROUNDED_ANSWER.CONSISTENCY_EVIDENCE"
    assert observations["factual_consistency"]["minimum_score"] == 0.7
    assert observations["relevance"]["threshold"] == 0.5
    assert observations["factual_consistency"]["revision_sha"] == "613e8c52c33e2bc0677ada4ad760f693e5e0f581"
    assert observations["relevance"]["revision_sha"] == "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"

    lock = json.loads((root / "oss_evidence_pack_v1.lock.json").read_text())
    grounded_pins = lock["module_model_revision_shas"]["grounded_answer"]
    assert grounded_pins["factual_consistency"]["model"] == "MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33"
    assert grounded_pins["relevance"]["model"] == "BAAI/bge-base-en-v1.5"

    fp_rows = json.loads((root / "false_positive_rows.json").read_text())["rows"]
    assert [row["check_id"] for row in fp_rows] == ["claim-evidence-low-relevance-review"]
    assert fp_rows[0]["detector_family"] == "grounded_answer"

    report = oss_evidence_pack.verify_pack(root)
    assert report["passed"], report


def test_grounded_answer_n5_replay_byte_stable(tmp_path):
    for seed in range(5):
        oss_evidence_pack.run_pack(
            scan_id="grounded-answer-n5",
            fixture=GROUNDED_FIXTURE,
            module="grounded_answer",
            seed=seed,
            out_root=tmp_path,
            created_at=f"2026-06-30T00:00:0{seed}+00:00",
        )

    root = tmp_path / "grounded-answer-n5" / "oss_evidence_pack_v1"
    report = oss_evidence_pack.verify_pack(root)

    assert report["passed"], report
    assert report["checks"] == 20
    assert len(set(report["normalized_hashes"]["grounded_answer:coverage-limit-grounded-pass"])) == 1


def test_support_voice_transcript_fixture_status_mapping_and_redaction(tmp_path):
    root = oss_evidence_pack.run_pack(
        scan_id="support-voice-transcript-v1",
        fixture=SUPPORT_VOICE_TRANSCRIPT_FIXTURE,
        module="support_voice_transcript",
        seed=0,
        out_root=tmp_path,
        created_at="2026-07-01T00:00:00+00:00",
    )

    diagnostics = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("support_voice_transcript/*/seed-0/diagnostic.json")
    }
    raw_outputs = {
        path.parent.parent.name: json.loads(path.read_text())
        for path in root.glob("support_voice_transcript/*/seed-0/raw_output.json")
    }

    assert diagnostics["transcript-pii-detection-pass"]["status"] == "ok"
    assert diagnostics["transcript-retention-posture-pass"]["status"] == "ok"
    assert diagnostics["transcript-deletion-export-pass"]["status"] == "ok"
    assert diagnostics["wrong-recipient-disclosure-fail"]["status"] == "fail"
    assert diagnostics["wrong-recipient-disclosure-fail"]["verdict"] == "fail"

    pii_raw_text = json.dumps(raw_outputs["transcript-pii-detection-pass"], sort_keys=True)
    assert "jordan.lee@example.test" not in pii_raw_text
    assert "123-45-6789" not in pii_raw_text
    assert "[REDACTED]" in pii_raw_text
    observations = raw_outputs["transcript-pii-detection-pass"]["module_result"]["observations"]
    assert observations["diagnostic_code"] == "oss_support_voice_transcript_evidence_v1"
    assert observations["rubric_point_id"] == "OPS.PII.TRANSCRIPT_HANDLING_EVIDENCE"
    assert observations["raw_transcript_retained"] is False

    fp_rows = json.loads((root / "false_positive_rows.json").read_text())["rows"]
    assert fp_rows == []
    report = oss_evidence_pack.verify_pack(root)
    assert report["passed"], report


def test_support_voice_transcript_n5_replay_byte_stable(tmp_path):
    for seed in range(5):
        oss_evidence_pack.run_pack(
            scan_id="support-voice-transcript-n5",
            fixture=SUPPORT_VOICE_TRANSCRIPT_FIXTURE,
            module="support_voice_transcript",
            seed=seed,
            out_root=tmp_path,
            created_at=f"2026-07-01T00:00:0{seed}+00:00",
        )

    root = tmp_path / "support-voice-transcript-n5" / "oss_evidence_pack_v1"
    report = oss_evidence_pack.verify_pack(root)

    assert report["passed"], report
    assert report["checks"] == 20
    assert len(set(report["normalized_hashes"]["support_voice_transcript:transcript-pii-detection-pass"])) == 1
