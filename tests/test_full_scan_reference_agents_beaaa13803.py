"""BEAAA-13803 controlled full-scan reference-agent gate tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.full_scan_reference_agents import (
    CONTRACT_VERSION,
    N5_SEEDS,
    REFERENCE_AGENTS,
    REFERENCE_FAMILIES,
    build_reference_validation_report,
)


REPO = Path(__file__).resolve().parents[1]


def test_reference_agent_set_has_known_good_and_known_bad_per_family():
    report = build_reference_validation_report()

    assert report["contract_version"] == CONTRACT_VERSION
    expectations = {agent["agent_id"]: agent["expectation"] for agent in report["reference_agents"]}
    assert "known_good" in expectations.values()
    assert "known_bad" in expectations.values()

    families = {family.family for family in REFERENCE_FAMILIES}
    for agent in REFERENCE_AGENTS:
        assert set(agent.families) == families
        for family in families:
            assert agent.expected_bands_by_family[family]


def test_reference_validation_runs_n5_through_dispatcher_with_evidence_refs():
    report = build_reference_validation_report()

    assert report["passed"] is True
    assert report["band_report"]["passed"] is True
    expected_receipts = len(REFERENCE_AGENTS) * len(REFERENCE_FAMILIES) * len(N5_SEEDS)
    assert len(report["dispatcher_receipts"]) == expected_receipts
    for receipt in report["dispatcher_receipts"]:
        assert receipt["status"] == "complete"
        assert receipt["evidence_ref"].startswith("evidence://beaaa-13803/")
        assert receipt["manifest_hash"].startswith("sha256:")
        assert receipt["bind_eligibility"] == "eligible"


def test_known_bad_reference_agents_fail_by_validated_family():
    report = build_reference_validation_report()

    known_bad_rows = [
        row
        for row in report["band_report"]["rows"]
        if row["agent_id"] == "ref-known-bad-adversarial-v0"
    ]
    assert {row["family"] for row in known_bad_rows} == {family.family for family in REFERENCE_FAMILIES}
    for row in known_bad_rows:
        assert row["expected_failure"] is True
        assert set(row["observed_bands"]) == {"D"}
        assert row["gate"] == "pass"


def test_cli_emits_checkable_validation_artifact(tmp_path):
    out = tmp_path / "beaaa-13803-reference-validation.json"
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "tools.full_scan_reference_agents",
            "--json-out",
            str(out),
            "--check",
        ],
        cwd=REPO,
    )

    emitted = json.loads(out.read_text(encoding="utf-8"))
    assert emitted["artifact_metadata"]["ticket_refs"] == ["BEAAA-13803", "BEAAA-13761"]
    assert emitted["scanner_version"] == "0.5.0"
    assert emitted["n5_calibration_delta"] == "0.0000 by deterministic controlled reference replay"
    assert emitted["passed"] is True
