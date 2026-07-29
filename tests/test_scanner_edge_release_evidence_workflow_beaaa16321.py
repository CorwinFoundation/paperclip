"""BEAAA-16321 - scanner-edge workflow tamper-surface contract (post-a1069cc).

Original intent: the scanner-edge workflow was the GitHub-side client for the
Paperclip release-evidence ingress accepted under BEAAA-16312/BEAAA-16329.

BEAAA-18854 reconciliation: canonical master commit a1069cc ("Stop legacy deploy
ceremony in development mode", authored by CorwinFoundation) converted
`.github/workflows/scanner-edge-image.yml` into the dev-image builder and
SUSPENDED the production release-evidence ceremony inside that file. The four
"presence" tests that asserted the OIDC-scoped ingress, the exchange payload
binding, the single-`sequence` dispatch surface, and the fail-closed capability
guards were therefore retired here -- they described a workflow surface that no
longer exists in dev mode. The new dev-mode contract is pinned by
tests/test_scanner_edge_dev_mode_artifact.py, whose
`test_suspended_production_ceremony_is_absent` requires exactly those surfaces to
be gone. The negative tamper guarantee below (no broad Paperclip key / no
release-candidates route in the committed workflow) is retained because it is
still a live, meaningful property of the dev-image workflow.

No network, Docker, scoring, rubric, corpus, provisioning, or promotion surface.
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "scanner-edge-image.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")


def test_workflow_removed_broad_paperclip_release_candidate_key() -> None:
    forbidden = (
        "PAPERCLIP_RELEASE_CANDIDATE_KEY",
        "PAPERCLIP_API_KEY",
        "/release-candidates",
        "/api/companies/${PAPERCLIP_COMPANY_ID}/release-candidates",
    )
    for marker in forbidden:
        assert marker not in WORKFLOW_TEXT
