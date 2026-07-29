from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.full_scan_executor import FullScanJob
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.tranche1_canary_runner import (
    EXACT_TARGET_CALLS,
    Tranche1CanaryError,
    select_exact_tranche,
    run_tranche1_canary,
)
from tools.tranche1_observational import ALLOWLIST


def test_public_full_scan_path_overdispatches_and_does_not_carry_capability_manifest():
    rows = build_registry()
    executable = [row for row in rows if row.get("production_runner_executable")]
    assert len(executable) > sum(ALLOWLIST.values())
    assert "capability_manifest" not in FullScanJob.__dataclass_fields__


def test_exact_selector_rejects_count_and_family_drift_before_target_call(monkeypatch):
    commit = "a" * 40
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", commit)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", commit)
    rows = build_registry()
    selected = select_exact_tranche(rows)
    assert len(selected) == 36
    assert {row["test_id"] for row in selected} == set(ALLOWLIST)

    called = 0

    def target(_prompt: str) -> str:
        nonlocal called
        called += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    drifted = [dict(row) for row in rows]
    victim = next(row for row in drifted if row.get("test_id") in ALLOWLIST)
    victim["case_id"] = "drifted"
    with pytest.raises(Tranche1CanaryError):
        run_tranche1_canary(
            registry_rows=drifted,
            subject=MockRunner("target", target),
            evidence_root=Path("/tmp/not-written"),
            job_id="drift",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha=commit,
            image_ref="image@sha256:" + "b" * 64,
        )
    assert called == 0


@pytest.mark.parametrize(
    ("commit", "runtime", "authorized", "image_ref"),
    [
        ("a" * 39, "a" * 40, "a" * 40, "scanner@sha256:" + "b" * 64),
        ("A" * 40, "A" * 40, "A" * 40, "scanner@sha256:" + "b" * 64),
        ("c" * 40, "a" * 40, "c" * 40, "scanner@sha256:" + "b" * 64),
        ("c" * 40, "c" * 40, "a" * 40, "scanner@sha256:" + "b" * 64),
        ("a" * 40, "a" * 40, "a" * 40, "scanner:mutable-latest"),
        ("a" * 40, "a" * 40, "a" * 40, "scanner@sha256:" + "B" * 64),
        ("a" * 40, "a" * 40, "a" * 40, "sha256:" + "b" * 64),
    ],
)
def test_identity_drift_is_rejected_before_target_call(
    tmp_path, monkeypatch, commit, runtime, authorized, image_ref
):
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", runtime)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", authorized)
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    with pytest.raises(Tranche1CanaryError):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path,
            job_id="identity-drift",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha=commit,
            image_ref=image_ref,
        )
    assert calls == 0


def test_runner_executes_exact_budget_zero_judges_and_redacts_secret(tmp_path, monkeypatch):
    commit = "a" * 40
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", commit)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", commit)
    secret = "never-persist-this-secret"

    report = run_tranche1_canary(
        registry_rows=build_registry(),
        subject=MockRunner(
            "target",
            lambda _prompt: '{"actual_response_label":"resisted_no_disclosure"}',
        ),
        evidence_root=tmp_path,
        job_id="canary",
        tenant_id="tenant-a",
        authorization_ref="root-custody:lease-7",
        commit_sha=commit,
        image_ref="scanner@sha256:" + "b" * 64,
    )

    assert report["budget"]["target_calls"] == EXACT_TARGET_CALLS == 180
    assert report["budget"]["judge_calls"] == 0
    assert report["capability_manifest"] == {"has_tool_calling": True}
    assert report["authorization_ref"] == "root-custody:lease-7"
    assert report["zero_weight_invariants"]["absolute_delta"] == {
        "are_total": 0,
        "pillars": 0,
        "band": 0,
        "eligibility": 0,
        "pricing": 0,
        "premium": 0,
        "quote_policy_behavior": 0,
        "published_weighted_countable_totals": 0,
    }
    assert all(row["scoring_active"] for row in report["families"])
    assert all(not row["underwriting_active"] for row in report["families"])
    assert all(row["effective_underwriting_weight"] == 0.0 for row in report["families"])
    materialized = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json")
    )
    assert secret not in materialized
    manifest = json.loads((tmp_path / "canary" / "manifest.json").read_text())
    assert manifest["pins"]["commit"] == commit
