from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from harness.demo_readiness_gate import evaluate_readiness


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_demo_readiness_gate.py"


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _registry(tmp_path: Path) -> Path:
    return _write_json(tmp_path / "registry.json", {
        "rows": [
            {
                "case_id": "CASE-001",
                "rubric_point_id": "OPS.HALLUCINATION.FACTUALITY",
                "pillar": "OPS",
                "status": "live",
                "applicable": True,
                "denominator_included": True,
            }
        ]
    })


def _registry_with_reporting_rows(tmp_path: Path) -> Path:
    rows = [
        {
            "case_id": "CASE-001",
            "rubric_point_id": "OPS.HALLUCINATION.FACTUALITY",
            "pillar": "OPS",
            "status": "live",
            "applicable": True,
            "denominator_included": True,
            "countable_as_executable": True,
        }
    ]
    rows.extend(
        {
            "case_id": f"REPORT-{idx:03d}",
            "rubric_point_id": "OPS.CITATION.INTEGRITY",
            "pillar": "OPS",
            "status": "not_executable",
            "applicable": True,
            "denominator_included": False,
            "countable_as_executable": False,
        }
        for idx in range(188)
    )
    return _write_json(tmp_path / "registry-278.json", {"rows": rows})


def _registry_with_ready_candidate_delta(tmp_path: Path) -> Path:
    return _write_json(tmp_path / "registry-delta.json", {
        "rows": [
            {
                "case_id": "CASE-001",
                "rubric_point_id": "OPS.HALLUCINATION.FACTUALITY",
                "pillar": "OPS",
                "status": "ready_candidate",
                "applicable": True,
                "denominator_included": True,
            }
        ]
    })


def _calibration(tmp_path: Path, *, clean: bool = True) -> Path:
    return _write_json(tmp_path / "golden.json", {"status": "pass" if clean else "fail", "breaches": [] if clean else ["G1"]})


def _evidence_dir(tmp_path: Path, *, overrides_by_seed: dict[int, dict] | None = None) -> Path:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for seed in range(5):
        artifact = evidence_dir / f"CASE-001-seed-{seed}.artifact.txt"
        artifact.write_text('{"terminal": true}\n', encoding="utf-8")
        record = {
            "case_id": "CASE-001",
            "rubric_point_id": "OPS.HALLUCINATION.FACTUALITY",
            "target_response": "answer",
            "verdict": "pass",
            "evidence_uri": artifact.name,
            "judge_oracle_id": "deterministic-factuality-oracle",
            "judge_oracle_version": "2026-07-18",
            "scanner_version": "0.5.0",
            "corpus_version": "0.1.1",
            "contract": "are-scanner-api-v1@v1.0",
            "seed": seed,
            "terminal_at": "2026-07-18T00:00:00Z",
            "content_digest": f"sha256:{seed:064x}",
            "total": 80 + (0.2 if seed == 4 else 0),
            "pillar_scores": {"OPS": 20, "ROBUSTNESS": 20, "BIAS_FAIRNESS": 20, "GOVERNANCE": 20},
        }
        record.update((overrides_by_seed or {}).get(seed, {}))
        _write_json(evidence_dir / f"CASE-001-seed-{seed}.json", record)
    return evidence_dir


def _evidence_dir_for_registry_rows(tmp_path: Path, rows: list[dict]) -> Path:
    evidence_dir = tmp_path / "registry-evidence"
    evidence_dir.mkdir()
    for index, row in enumerate(rows):
        case_id = row["case_id_or_probe_id"]
        rubric_point_id = row["rubric_point_id"]
        for seed in range(5):
            _write_json(evidence_dir / f"{index:03d}-{seed}.json", {
                "case_id": case_id,
                "rubric_point_id": rubric_point_id,
                "target_response": "answer",
                "verdict": "pass",
                "evidence_uri": f"evidence-store://unit/{case_id}/{seed}",
                "judge_oracle_id": "deterministic-readiness-oracle",
                "judge_oracle_version": "2026-07-18",
                "scanner_version": "0.5.0",
                "corpus_version": "0.1.1",
                "contract": "are-scanner-api-v1@v1.0",
                "seed": seed,
                "terminal_at": "2026-07-18T00:00:00Z",
                "content_digest": f"sha256:{index:060x}{seed:04x}",
                "total": 80,
                "pillar_scores": {"P1": 80, "P2": 80, "P3": 80, "P4": 80},
            })
    return evidence_dir


def test_gate_emits_ready_set_for_complete_real_terminal_evidence(tmp_path: Path):
    report = evaluate_readiness(
        registry_path=_registry(tmp_path),
        evidence_paths=[_evidence_dir(tmp_path)],
        calibration_path=_calibration(tmp_path),
        repo_root=tmp_path,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    assert report["ready"] is True
    assert report["ready_set"] == ["CASE-001"]
    assert report["rejected_cases"] == []
    assert report["n5_delta_table"][0]["total_spread"] == 0.2
    assert report["schema_version"] == "backbond-demo-coverage-readiness/v1"
    assert report["semantic_sha256"]
    assert report["ready_set_vs_live_registry_delta"]["status"] == "quiet"


def test_shipped_278_row_registry_reports_non_countable_rows_without_blocking_ready(tmp_path: Path):
    report = evaluate_readiness(
        registry_path=_registry_with_reporting_rows(tmp_path),
        evidence_paths=[_evidence_dir(tmp_path)],
        calibration_path=_calibration(tmp_path),
        repo_root=tmp_path,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    assert report["ready"] is True
    assert report["registry"]["case_count"] == 189
    assert report["registry"]["required_case_count"] == 1
    assert report["registry"]["non_countable_reporting_count"] == 188
    assert len(report["ready_set"]) == 1
    non_countable = [
        row for row in report["rejected_cases"]
        if row["reasons"] == ["not_required_by_registry"]
    ]
    assert len(non_countable) == 188


def test_current_shipped_278_row_registry_perfect_feed_exits_ready(tmp_path: Path):
    registry_path = REPO / "tools" / "probe-registry" / "active_registry.json"
    rows = json.loads(registry_path.read_text(encoding="utf-8"))["rows"]
    required_rows = [row for row in rows if row.get("countable_as_executable")]

    report = evaluate_readiness(
        registry_path=registry_path,
        evidence_paths=[_evidence_dir_for_registry_rows(tmp_path, required_rows)],
        calibration_path=_calibration(tmp_path),
        repo_root=REPO,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    assert len(rows) == 278
    assert len(required_rows) == 90
    assert report["ready"] is True
    assert len(report["ready_set"]) == 90
    assert report["registry"]["case_count"] == 278
    assert report["registry"]["required_case_count"] == 90
    assert report["registry"]["non_countable_reporting_count"] == 188
    assert len(report["rejected_cases"]) == 188
    assert {tuple(row["reasons"]) for row in report["rejected_cases"]} == {("not_required_by_registry",)}


def test_gate_rejects_missing_synthetic_fixture_and_unpinned_evidence(tmp_path: Path):
    evidence = _evidence_dir(tmp_path, overrides_by_seed={
        0: {"target_response": ""},
        1: {"schema": "synthetic-fixture-class"},
        2: {"judge_oracle_version": "latest"},
        3: {"evidence_uri": "missing.json"},
    })
    (evidence / "CASE-001-seed-4.json").unlink()

    report = evaluate_readiness(
        registry_path=_registry(tmp_path),
        evidence_paths=[evidence],
        calibration_path=_calibration(tmp_path),
        repo_root=tmp_path,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    assert report["ready"] is False
    reasons = report["rejected_cases"][0]["reasons"]
    assert "missing_required_seeds:4" in reasons
    assert "missing_target_response" in reasons
    assert "fixture_or_synthetic_evidence" in reasons
    assert "unpinned_judge_or_oracle" in reasons
    assert "unreachable_evidence_uri" in reasons


def test_gate_rejects_duplicate_registry_and_evidence_seed(tmp_path: Path):
    registry = _write_json(tmp_path / "registry.json", {
        "rows": [
            {"case_id": "CASE-001", "rubric_point_id": "OPS.HALLUCINATION.FACTUALITY", "status": "live"},
            {"case_id": "CASE-001", "rubric_point_id": "OPS.HALLUCINATION.FACTUALITY", "status": "live"},
        ]
    })
    evidence = _evidence_dir(tmp_path)
    duplicate = json.loads((evidence / "CASE-001-seed-0.json").read_text(encoding="utf-8"))
    duplicate["content_digest"] = "sha256:" + "f" * 64
    _write_json(evidence / "duplicate-seed-0.json", duplicate)

    report = evaluate_readiness(
        registry_path=registry,
        evidence_paths=[evidence],
        calibration_path=_calibration(tmp_path),
        repo_root=tmp_path,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    assert report["ready"] is False
    assert "duplicate_evidence:CASE-001:seed_0" in report["evidence_store"]["errors"]
    assert all("duplicate_registry_case_id" in row["reasons"] for row in report["rejected_cases"])


def test_gate_rejects_n5_spread_and_golden_regression(tmp_path: Path):
    report = evaluate_readiness(
        registry_path=_registry(tmp_path),
        evidence_paths=[_evidence_dir(tmp_path, overrides_by_seed={4: {"total": 83}})],
        calibration_path=_calibration(tmp_path, clean=False),
        repo_root=tmp_path,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    reasons = report["rejected_cases"][0]["reasons"]
    assert "n5_total_spread_exceeds_1" in reasons
    assert "golden_set_regression_not_clean" in reasons


def test_gate_rejects_inapplicable_required_registry_row(tmp_path: Path):
    registry = _write_json(tmp_path / "registry.json", {
        "rows": [
            {
                "case_id": "CASE-001",
                "rubric_point_id": "OPS.HALLUCINATION.FACTUALITY",
                "status": "required",
                "applicable": False,
                "denominator_included": True,
            }
        ]
    })

    report = evaluate_readiness(
        registry_path=registry,
        evidence_paths=[_evidence_dir(tmp_path)],
        calibration_path=_calibration(tmp_path),
        repo_root=tmp_path,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    assert report["ready"] is False
    assert report["ready_set"] == []
    assert report["rejected_cases"][0]["reasons"] == ["inapplicable_required", "not_required_by_registry"]


def test_ready_set_vs_live_registry_delta_reports_ready_candidates_with_evidence_links(tmp_path: Path):
    report = evaluate_readiness(
        registry_path=_registry_with_ready_candidate_delta(tmp_path),
        evidence_paths=[_evidence_dir(tmp_path)],
        calibration_path=_calibration(tmp_path),
        repo_root=tmp_path,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    delta = report["ready_set_vs_live_registry_delta"]
    assert report["ready"] is True
    assert delta["status"] == "newly_stable_not_live"
    assert delta["newly_stable_not_live_count"] == 1
    assert delta["newly_stable_not_live"][0]["case_id"] == "CASE-001"
    assert delta["newly_stable_not_live"][0]["registry_status"] == "ready_candidate"
    assert delta["newly_stable_not_live"][0]["evidence_links"] == [
        "CASE-001-seed-0.artifact.txt",
        "CASE-001-seed-1.artifact.txt",
        "CASE-001-seed-2.artifact.txt",
        "CASE-001-seed-3.artifact.txt",
        "CASE-001-seed-4.artifact.txt",
    ]


def test_cli_is_byte_stable_for_same_registry_and_evidence_digests(tmp_path: Path):
    registry = _registry(tmp_path)
    evidence = _evidence_dir(tmp_path)
    calibration = _calibration(tmp_path)

    cmd = [
        sys.executable,
        str(SCRIPT),
        "--registry",
        str(registry),
        "--evidence",
        str(evidence),
        "--calibration",
        str(calibration),
        "--repo-root",
        str(tmp_path),
    ]
    first = subprocess.run(cmd, cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    second = subprocess.run(cmd, cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    assert first.returncode == 0
    assert second.returncode == 0
    left = json.loads(first.stdout)
    right = json.loads(second.stdout)
    left.pop("generated_at")
    right.pop("generated_at")
    assert left == right


def test_semantic_sha256_is_independent_of_absolute_workspace_path(tmp_path: Path):
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    registry_left = _registry(left_root)
    evidence_left = _evidence_dir(left_root)
    calibration_left = _calibration(left_root)
    registry_right = _write_json(right_root / "registry.json", json.loads(registry_left.read_text(encoding="utf-8")))
    evidence_right = _evidence_dir(right_root)
    calibration_right = _write_json(right_root / "golden.json", json.loads(calibration_left.read_text(encoding="utf-8")))

    left = evaluate_readiness(
        registry_path=registry_left,
        evidence_paths=[evidence_left],
        calibration_path=calibration_left,
        repo_root=left_root,
        generated_at="2026-07-18T00:00:00+00:00",
    )
    right = evaluate_readiness(
        registry_path=registry_right,
        evidence_paths=[evidence_right],
        calibration_path=calibration_right,
        repo_root=right_root,
        generated_at="2026-07-18T00:00:00+00:00",
    )

    assert left["semantic_sha256"] == right["semantic_sha256"]
