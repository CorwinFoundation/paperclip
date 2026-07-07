"""BEAAA-12362 JADEPUFFER fixture-gap guard.

This is intentionally non-scoring. It records that the current Scanner repo has
slice coverage for JADEPUFFER-like behavior, but no deterministic end-to-end
JADEPUFFER chain fixture yet.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

EXPECTED_FIXTURE = REPO / "harness" / "mock_tools" / "pilots" / "jadepuffer_loss_chain_v1.yaml"


def test_jadepuffer_source_verified_mapping_artifact_exists() -> None:
    artifact = REPO / "artifacts" / "beaaa-12362" / "jadepuffer_loss_chain_coverage_inventory_v0.1.md"
    text = artifact.read_text(encoding="utf-8")
    assert "Sysdig Threat Research Team" in text
    assert "Langflow CVE-2025-3248" in text
    assert "MinIO/default credential access" in text
    assert "Nacos takeover" in text
    assert "Database encryption/drop" in text
    assert "are-scanner-api-v1@v1.0" in text
    assert "No approval gate is needed" in text


@pytest.mark.xfail(reason="BEAAA-12362 maps the gap; the non-scoring JADEPUFFER chain fixture is backlog work.")
def test_jadepuffer_end_to_end_mock_tool_fixture_exists() -> None:
    assert EXPECTED_FIXTURE.exists()
