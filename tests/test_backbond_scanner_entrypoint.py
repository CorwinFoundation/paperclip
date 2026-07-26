from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "tools" / "backbond-scanner"
FIXTURE = ROOT / "tools" / "backbond-scanner-fixtures" / "sample_agent_fixture.json"
REQUIRED_KEYS = {
    "are_total",
    "subscores",
    "band",
    "confidence",
    "evidence_refs",
    "uncovered_dimensions",
    "scanner_version",
    "corpus_version",
    "model_versions",
    "seed",
    "run_timestamp",
    "extensions",
}


def test_backbond_scanner_entrypoint_writes_v1_contract_shape(tmp_path: Path) -> None:
    out = tmp_path / "score.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(out),
            "--seed",
            "0",
            "--freeze-timestamp",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    score = json.loads(out.read_text(encoding="utf-8"))
    assert REQUIRED_KEYS.issubset(score)
    assert score["are_total"] == 59.0
    assert score["band"] == "C"
    assert set(score["subscores"]) == {"GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"}
    assert score["scanner_version"] == "0.5.0"
    assert score["corpus_version"] == "2026.6.24"
    assert score["model_versions"]["primary"]
    assert isinstance(score["model_versions"]["judges"], list)
    assert score["seed"] == 0
    assert score["run_timestamp"] == "1970-01-01T00:00:00Z"
    assert score["evidence_refs"]
    assert score["uncovered_dimensions"]
    assert score["probe_provenance"]["mode"] == "stratified_rotation"
    assert score["probe_provenance"]["sample_coverage"]["selected"] == 8
    assert score["probe_provenance"]["sample_coverage"]["total"] == 25
    assert score["anti_gaming"]["raw_scan_seed_in_envelope"] is False
    anti_gaming = score["extensions"]["v1.6.0"]["anti_gaming"]
    assert anti_gaming["score_integrity_divergence"] == 0.75
    assert anti_gaming["production_evidence_score"] == 58.25
    assert anti_gaming["underwriting_review_flag"] is False
    assert anti_gaming["review_signal_only"] is True


def test_backbond_scanner_version_command_reports_release_pins() -> None:
    completed = subprocess.run(
        [sys.executable, str(ENTRYPOINT), "--version"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    pins = json.loads(completed.stdout)
    assert pins["scanner_version"] == "0.5.0"
    assert pins["corpus_version"] == "2026.6.24"
    assert pins["contract_version"] == "are-scanner-api-v1@v1.0"


def test_backbond_scanner_rejects_confidence_above_contract_range(tmp_path: Path) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["confidence"] = 1.01
    fixture_path = tmp_path / "bad-confidence.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(fixture_path),
            "--out",
            str(tmp_path / "score.json"),
            "--seed",
            "0",
            "--freeze-timestamp",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert "fixture.confidence must be a number in [0,1]" in completed.stderr


def test_backbond_scanner_accepts_confidence_contract_boundaries(tmp_path: Path) -> None:
    for confidence in (0, 1):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        fixture["confidence"] = confidence
        fixture_path = tmp_path / f"confidence-{confidence}.json"
        out = tmp_path / f"score-{confidence}.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                str(ENTRYPOINT),
                "--fixture",
                str(fixture_path),
                "--out",
                str(out),
                "--seed",
                "0",
                "--freeze-timestamp",
            ],
            check=False,
            text=True,
            capture_output=True,
        )

        assert completed.returncode == 0, completed.stderr
        score = json.loads(out.read_text(encoding="utf-8"))
        assert score["confidence"] == confidence


def test_backbond_scanner_sampling_is_seed_deterministic(tmp_path: Path) -> None:
    outputs = []
    for index in range(2):
        out = tmp_path / f"score-{index}.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(ENTRYPOINT),
                "--fixture",
                str(FIXTURE),
                "--out",
                str(out),
                "--seed",
                "0",
                "--freeze-timestamp",
                "--scan-mode",
                "field",
                "--rotation-epoch",
                "3",
                "--scan-nonce",
                "nonce-123",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(json.loads(out.read_text(encoding="utf-8")))

    assert outputs[0]["probe_provenance"] == outputs[1]["probe_provenance"]
    assert outputs[0]["probe_provenance"]["probe_set_hash"].startswith("sha256:")
    assert outputs[0]["probe_provenance"]["scan_seed_ref"].startswith("sha256:")


def test_backbond_scanner_rotation_accrues_without_full_corpus(tmp_path: Path) -> None:
    covered: set[str] = set()
    per_scan_counts = []
    for epoch in range(4):
        out = tmp_path / f"score-{epoch}.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(ENTRYPOINT),
                "--fixture",
                str(FIXTURE),
                "--out",
                str(out),
                "--seed",
                "0",
                "--freeze-timestamp",
                "--rotation-epoch",
                str(epoch),
                "--scan-nonce",
                "rotation-window",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stderr
        coverage = json.loads(out.read_text(encoding="utf-8"))["probe_provenance"]["sample_coverage"]
        per_scan_counts.append(coverage["selected"])
        for pillar in coverage["by_pillar"].values():
            covered.update(pillar["dimensions"])

    assert per_scan_counts == [8, 8, 8, 8]
    assert all(count < 25 for count in per_scan_counts)
    assert len(covered) > per_scan_counts[0]


def test_backbond_scanner_calibration_and_env_rollback_force_fixed_subset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    out_calibration = tmp_path / "calibration.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(out_calibration),
            "--seed",
            "0",
            "--freeze-timestamp",
            "--scan-mode",
            "calibration",
            "--rotation-epoch",
            "9",
            "--scan-nonce",
            "ignored-in-calibration",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    calibration = json.loads(out_calibration.read_text(encoding="utf-8"))
    assert calibration["probe_provenance"]["mode"] == "fixed_subset"
    assert calibration["probe_provenance"]["scan_seed_ref"] is None
    assert calibration["are_total"] == 59.0

    monkeypatch.setenv("ARE_PROBE_SAMPLING", "off")
    out_rollback = tmp_path / "rollback.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(out_rollback),
            "--seed",
            "0",
            "--freeze-timestamp",
            "--scan-mode",
            "field",
            "--rotation-epoch",
            "9",
            "--scan-nonce",
            "ignored-by-rollback",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    rollback = json.loads(out_rollback.read_text(encoding="utf-8"))
    assert rollback["probe_provenance"]["mode"] == "fixed_subset"
    assert rollback["probe_provenance"]["probe_set_hash"] == calibration["probe_provenance"]["probe_set_hash"]


def test_backbond_scanner_audit_spine_receives_raw_scan_seed(tmp_path: Path) -> None:
    out = tmp_path / "score.json"
    audit_spine = tmp_path / "audit-spine.jsonl"
    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(out),
            "--seed",
            "0",
            "--freeze-timestamp",
            "--rotation-epoch",
            "1",
            "--scan-nonce",
            "audit-nonce",
            "--audit-spine",
            str(audit_spine),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    score = json.loads(out.read_text(encoding="utf-8"))
    audit = json.loads(audit_spine.read_text(encoding="utf-8").strip())
    assert "scan_seed" in audit
    assert audit["scan_seed_ref"] == score["probe_provenance"]["scan_seed_ref"]
    assert audit["probe_set_hash"] == score["probe_provenance"]["probe_set_hash"]
    assert "scan_seed" not in score


def test_score_integrity_monitor_flags_staged_outperforming_production(tmp_path: Path) -> None:
    store = tmp_path / "evidence-store"
    latest = store / "tenant-a" / "sample-ap-automation-agent" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(
        json.dumps(
            {
                "production_evidence_score": 42.0,
                "evidence_ref": "evidence-store://tenant-a/sample-ap-automation-agent/runtime/latest.json",
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "score.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(out),
            "--tenant-id",
            "tenant-a",
            "--production-evidence-store",
            str(store),
            "--seed",
            "0",
            "--freeze-timestamp",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    score = json.loads(out.read_text(encoding="utf-8"))
    anti_gaming = score["extensions"]["v1.6.0"]["anti_gaming"]
    assert score["are_total"] == 59.0
    assert score["subscores"] == {
        "GOVERNANCE": 16.0,
        "ROBUSTNESS": 14.5,
        "BIAS_FAIRNESS": 13.0,
        "OPS": 15.5,
    }
    assert anti_gaming["score_integrity_divergence"] == 17.0
    assert anti_gaming["underwriting_review_flag"] is True
    assert anti_gaming["reason"] == "staged_outperforms_production"
    assert anti_gaming["review_signal_only"] is True


def test_score_integrity_monitor_treats_matching_production_as_no_flag(tmp_path: Path) -> None:
    store = tmp_path / "evidence-store"
    latest = store / "tenant-a" / "sample-ap-automation-agent" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(json.dumps({"production_evidence_score": 58.5}), encoding="utf-8")
    out = tmp_path / "score.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(out),
            "--tenant-id",
            "tenant-a",
            "--production-evidence-store",
            str(store),
            "--seed",
            "0",
            "--freeze-timestamp",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    score = json.loads(out.read_text(encoding="utf-8"))
    anti_gaming = score["extensions"]["v1.6.0"]["anti_gaming"]
    assert anti_gaming["score_integrity_divergence"] == 0.5
    assert anti_gaming["underwriting_review_flag"] is False
    assert anti_gaming["reason"] == "within_threshold"


def test_score_integrity_monitor_emits_null_without_production_evidence(tmp_path: Path) -> None:
    out = tmp_path / "score.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(out),
            "--tenant-id",
            "tenant-without-evidence",
            "--production-evidence-store",
            str(tmp_path / "missing-store"),
            "--seed",
            "0",
            "--freeze-timestamp",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    score = json.loads(out.read_text(encoding="utf-8"))
    anti_gaming = score["extensions"]["v1.6.0"]["anti_gaming"]
    assert anti_gaming["score_integrity_divergence"] is None
    assert anti_gaming["production_evidence_score"] is None
    assert anti_gaming["underwriting_review_flag"] is False
    assert anti_gaming["reason"] == "no_production_evidence"


def test_score_integrity_monitor_rejects_tenant_path_escape(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "--fixture",
            str(FIXTURE),
            "--out",
            str(tmp_path / "score.json"),
            "--tenant-id",
            "../tenant-a",
            "--production-evidence-store",
            str(tmp_path / "store"),
            "--seed",
            "0",
            "--freeze-timestamp",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert "tenant_id must not contain path separators" in completed.stderr
