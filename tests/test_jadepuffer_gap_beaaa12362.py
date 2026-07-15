"""BEAAA-12362 JADEPUFFER fixture-gap guard.

This is intentionally non-scoring. It records that the current Scanner repo has
slice coverage for JADEPUFFER-like behavior, but no deterministic end-to-end
JADEPUFFER chain fixture yet.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

EXPECTED_FIXTURE = REPO / "harness" / "mock_tools" / "pilots" / "jadepuffer_chain_v1.yaml"
EXECUTABLE_MAPPING = REPO / "artifacts" / "beaaa-12393" / "jadepuffer_chain_fixture_mapping_v0.1.md"


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


def test_jadepuffer_end_to_end_mock_tool_fixture_exists() -> None:
    assert EXPECTED_FIXTURE.exists()


def test_jadepuffer_executable_mapping_supersedes_doc_only_gap() -> None:
    text = EXECUTABLE_MAPPING.read_text(encoding="utf-8")
    assert "BEAAA-12393" in text
    assert "chain_step_evidence_refs" in text
    assert "rce_like_initial_access_model" in text
    assert "credential_reuse_and_adaptive_retry" in text
    assert "object_store_default_credential_path" in text
    assert "service_takeover_persistence_beacon_and_destructive_action" in text
    assert "non-scoring" in text
    assert "N=5 calibration delta" in text
