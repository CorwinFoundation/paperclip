"""BEAAA-13682 framework-worker ownership and pin gate tests."""
from __future__ import annotations

import copy

from tools.framework_worker_registry import (
    CONTRACT_VERSION,
    REQUIRED_WORKER_FIELDS,
    build_worker_manifest,
    validate_manifest,
    validate_worker,
    worker_id_for_registry_row,
)


def test_worker_manifest_covers_active_registry_rows_with_required_metadata():
    manifest = build_worker_manifest()

    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["active_registry_rows"] > 0
    assert validate_manifest(manifest) == []

    active_workers = [w for w in manifest["workers"] if w["active_registry_rows"] > 0]
    assert active_workers
    for worker in active_workers:
        assert not [field for field in REQUIRED_WORKER_FIELDS if not worker.get(field)], worker
        assert worker["corpus_hash"].startswith("sha256:")
        assert worker["release_blocker"] is False
        if worker["worker_id"] == "content_candidate":
            assert worker["measured_score_allowed"] is False
            assert worker["reported_not_scored_only"] is True
        else:
            assert worker["measured_score_allowed"] is True


def test_unpinned_active_worker_is_release_blocker_not_measured_score():
    manifest = build_worker_manifest()
    broken = copy.deepcopy(manifest)
    active_worker = next(
        w
        for w in broken["workers"]
        if w["active_registry_rows"] > 0 and not w["reported_not_scored_only"]
    )

    active_worker["framework_version"] = "latest"
    active_worker["dependency_pins"]["promptfoo"] = "latest"
    active_worker["release_blocker"] = True
    active_worker["measured_score_allowed"] = False
    active_worker["blocker_reason"] = "framework_version is unpinned"

    errors = validate_manifest(broken)
    assert any("framework_version is unpinned" in error for error in errors)
    assert any("active worker is release-blocked" in error for error in errors)
    assert any("active worker cannot emit measured score" in error for error in errors)


def test_worker_validation_fails_missing_acceptance_metadata():
    worker = {
        "worker_id": "broken_worker",
        "owner": "",
        "framework_version": "",
        "dependency_pins": {},
        "corpus_hash": "",
        "seed_policy": "",
        "evidence_output_schema": {},
    }

    errors = validate_worker(worker)
    assert any("missing required metadata" in error for error in errors)
    assert any("framework_version is unpinned" in error for error in errors)
    assert any("dependency_pins must be a non-empty object" in error for error in errors)
    assert any("evidence_output_schema missing" in error for error in errors)


def test_worker_mapping_routes_core_framework_families():
    assert worker_id_for_registry_row({"test_id": "garak", "runner": "garak_wrapper"}) == "garak"
    assert worker_id_for_registry_row({"test_id": "pyrit", "runner": "bench_wrapper"}) == "pyrit"
    assert worker_id_for_registry_row({"test_id": "agentdojo", "runner": "live_probe_harness"}) == "agentdojo"
    assert worker_id_for_registry_row({"test_id": "promptinject", "runner": "promptfoo_wrapper"}) == "promptfoo"
    assert worker_id_for_registry_row({"test_id": "bbq", "runner": "bench_wrapper"}) == "fairness_custom"
    assert worker_id_for_registry_row({"test_id": "pii_scan_time_detection", "runner": "bench_wrapper"}) == "llm_guard_presidio"
    assert worker_id_for_registry_row({"test_id": "p4_kill_switch", "case_class": "attestation"}) == "governance_attestation"
