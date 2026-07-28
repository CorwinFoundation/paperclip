"""Fail-closed release/runtime identity parity for BEAAA-15699 candidates."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from release_identity import RELEASE_IDENTITY
from scanners import CONTRACT_VERSION, SCANNER_VERSION
from scripts.beaaa6190_v05_rc_n5 import CONTRACT_REFERENCES


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "tools" / "backbond-scanner"
FIXTURE = ROOT / "tools" / "backbond-scanner-fixtures" / "sample_agent_fixture.json"
GOLDEN_ROOT = ROOT / "tests" / "fixtures" / "golden_agents"
N5_REPORT = ROOT / "tools" / "calibration-deltas" / "2026-06-22-beaaa6190-v05-rc-n5.md"


def _manifest() -> dict:
    return {
        "scanner_version": RELEASE_IDENTITY.scanner_version,
        "corpus_version": RELEASE_IDENTITY.corpus_version,
        "contract": RELEASE_IDENTITY.contract_version,
        "model_versions": RELEASE_IDENTITY.model_versions,
    }


def test_runtime_constants_match_immutable_release_manifest() -> None:
    manifest = _manifest()
    assert SCANNER_VERSION == manifest["scanner_version"]
    assert CONTRACT_VERSION == manifest["contract"]


def test_fixture_runtime_emits_immutable_release_manifest_identity(tmp_path: Path) -> None:
    output = tmp_path / "score.json"
    completed = subprocess.run(
        [sys.executable, str(ENTRYPOINT), "--fixture", str(FIXTURE), "--out", str(output), "--seed", "0", "--freeze-timestamp"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    score = json.loads(output.read_text(encoding="utf-8"))
    manifest = _manifest()
    assert score["scanner_version"] == manifest["scanner_version"]
    assert score["contract_version"] == manifest["contract"]


def test_golden_provenance_matches_immutable_release_manifest() -> None:
    manifest = _manifest()
    paths = sorted(GOLDEN_ROOT.glob("*/expected_score.json"))
    assert paths
    for path in paths:
        score = json.loads(path.read_text(encoding="utf-8"))
        assert score["scanner_version"] == manifest["scanner_version"], path
        assert score["contract_version"] == manifest["contract"], path


def test_historical_n5_report_remains_immutable_and_explicitly_pre_reconciliation() -> None:
    manifest_contract = _manifest()["contract"]
    assert CONTRACT_REFERENCES[0] == manifest_contract

    report = N5_REPORT.read_text(encoding="utf-8")
    frontmatter = report.split("---", 2)[1]
    historical_contract = "are-scanner-api-v1@v1.0"
    assert historical_contract != manifest_contract
    assert f'contracts_referenced: ["{historical_contract}",' in frontmatter
    assert f"- contract_version: `{historical_contract}`" in report
