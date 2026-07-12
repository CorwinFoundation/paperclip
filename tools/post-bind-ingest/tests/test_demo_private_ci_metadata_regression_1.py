from __future__ import annotations

from pathlib import Path
import sys

import pytest


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from app import _deployment_metadata, _health_metadata  # noqa: E402


# Regression: ISSUE-003 — partner page exposed a link into private GitHub Actions
# Found by /qa on 2026-07-12
# Report: .gstack/qa-reports/qa-report-hughes-combine-indiana-advisory-trycloudflare-com-2026-07-11.md

def test_public_metadata_keeps_plain_provenance_without_private_ci_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_GITHUB_REPOSITORY", "private-owner/private-repository")
    monkeypatch.setenv("DEMO_GIT_REF", "pull/61/merge")
    monkeypatch.setenv("DEMO_GITHUB_RUN_ID", "123456789")
    monkeypatch.setenv("DEMO_GITHUB_RUN_URL", "https://github.com/private/actions/runs/123456789")

    deployment = _deployment_metadata()
    assert deployment["github_run_id"] == "123456789"
    assert "repository" not in deployment
    assert "git_ref" not in deployment
    assert "github_run_url" not in deployment

    health = _health_metadata()
    assert health == {
        "status": "ok",
        "mode": "synthetic-lifecycle",
        "production_contacted": False,
        "vapi_used": False,
    }

    page = (DEMO_DIR / "index_v2.html").read_text(encoding="utf-8")
    assert "github_run_url" not in page
    assert "Actions run" in page