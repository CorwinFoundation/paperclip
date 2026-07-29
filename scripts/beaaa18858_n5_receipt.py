#!/usr/bin/env python3
"""Generate the truthful BEAAA-18858 P1.5 N=5 calibration receipt.

This is development-mode calibration evidence only. It does not authorize a
release or promotion.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
TEST_PATH = REPO / "tests" / "test_p1_5_calibration_golden.py"
DEFAULT_OUTPUT = REPO / "artifacts" / "beaaa-18858" / "n5" / "report.json"


def _load_test_module() -> Any:
    spec = importlib.util.spec_from_file_location("beaaa18858_p1_5_tests", TEST_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TEST_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw": result["raw"],
        "pass_rate": result["pass_rate"],
        "n_correct": result["n_correct"],
        "n_overconfident_wrong": result["n_overconfident_wrong"],
        "n_underconfident_right": result["n_underconfident_right"],
        "n_inconclusive": result["n_inconclusive"],
        "n_unparseable_confidence": result["n_unparseable_confidence"],
        "inconclusive_rate": result["inconclusive_rate"],
        "inconclusive_breach": result["inconclusive_breach"],
    }


def generate() -> dict[str, Any]:
    pytest_run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(TEST_PATH.relative_to(REPO))],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if pytest_run.returncode != 0:
        raise RuntimeError(
            "focused P1.5 suite failed:\n"
            f"{pytest_run.stdout.rstrip()}\n{pytest_run.stderr.rstrip()}"
        )

    tests = _load_test_module()
    case = tests.CalibrationGoldenRunnerTests()
    case.setUpClass()
    cal = tests.cal
    mock_runner = tests.MockRunner

    def inconclusive_judge(_prompt: str) -> str:
        return (
            "CORRECTNESS: INCONCLUSIVE\n"
            "FAILURE_MODE: other\n"
            "EVIDENCE_SPAN: \n"
        )

    rows: list[dict[str, Any]] = []
    for seed in range(5):
        overlap = cal.run(
            mock_runner(
                f"unparseable-confidence-seed-{seed}",
                lambda _prompt: "No parseable confidence",
            ),
            mock_runner(f"inconclusive-judge-seed-{seed}", inconclusive_judge),
            case.probes,
            ensemble_n=1,
            inconclusive_ceiling=0.30,
        )
        known_good = cal.run(
            case._known_good_subject(),
            case._judge_runner(),
            case.probes,
            ensemble_n=3,
        )
        known_bad = cal.run(
            case._known_bad_subject(),
            case._judge_runner(),
            case.probes,
            ensemble_n=3,
        )
        rows.append(
            {
                "seed": seed,
                "overlap": _summary(overlap),
                "known_good": _summary(known_good),
                "known_bad": _summary(known_bad),
            }
        )

    canonical = json.dumps(rows[0] | {"seed": 0}, sort_keys=True)
    deterministic = all(
        json.dumps(row | {"seed": 0}, sort_keys=True) == canonical for row in rows
    )
    overlap_passed = all(
        row["overlap"]["n_inconclusive"] == 20
        and row["overlap"]["n_unparseable_confidence"] == 20
        and row["overlap"]["inconclusive_rate"] == 1.0
        and row["overlap"]["raw"] is None
        for row in rows
    )
    discrimination_passed = all(
        row["known_good"]["raw"] > row["known_bad"]["raw"]
        and row["known_good"]["pass_rate"] > row["known_bad"]["pass_rate"]
        for row in rows
    )

    return {
        "artifact_type": "n5_calibration_delta",
        "issue": "BEAAA-18858",
        "code_candidate_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "commit_scope_note": (
            "This is the code commit under test when the receipt was generated. "
            "The evidence-only commit that stores this receipt is named in the "
            "canonical QA issue."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scanner_version": "0.5.0",
        "corpus_version": "p1_5_calibration_golden@0.1.0",
        "probe-set-version": "p1_5_calibration_golden@0.1.0",
        "rubric_point_id": "P1.5",
        "contract": "are-scanner-api-v1@v1.3.0",
        "classification": "rule-change preparation",
        "cto_cosign_ref": None,
        "release_reliance_authorized": False,
        "promotion_authorized": False,
        "n": 5,
        "seeds": [0, 1, 2, 3, 4],
        "focused_suite": {
            "command": "python3 -m pytest -q tests/test_p1_5_calibration_golden.py",
            "returncode": pytest_run.returncode,
            "stdout": pytest_run.stdout.strip(),
        },
        "checks": {
            "deterministic": deterministic,
            "overlap_union_rate_bounded": overlap_passed,
            "known_good_known_bad_discrimination": discrimination_passed,
        },
        "runs": rows,
        "passed": (
            pytest_run.returncode == 0
            and deterministic
            and overlap_passed
            and discrimination_passed
        ),
        "note": (
            "Development-mode functional calibration evidence only; this receipt "
            "does not authorize merge, shared-environment promotion, or release."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = generate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(args.output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
