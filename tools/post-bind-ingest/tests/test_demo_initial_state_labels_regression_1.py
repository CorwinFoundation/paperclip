from __future__ import annotations

from pathlib import Path


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"


# Regression: ISSUE-005 — pre-run UI advertised completed artifacts and a removed git ref
# Found by browser QA on 2026-07-12
# Report: .gstack/qa-reports/qa-report-hughes-combine-indiana-advisory-trycloudflare-com-2026-07-11.md

def test_initial_ui_labels_wait_for_actual_lifecycle_results() -> None:
    page = (DEMO_DIR / "index_v2.html").read_text(encoding="utf-8")

    assert 'id="scan-count">0<' in page
    assert 'id="install-count">0<' in page
    assert 'id="certificate-status">waiting<' in page
    assert 'id="report-status">waiting<' in page
    assert 'id="evidence-status">waiting<' in page
    assert "state.deployment.git_ref" not in page
    assert "state.deployment.workflow" in page

    assert "cert?.signature_status==='valid'?'signed':l?.terminal_failure?'blocked':'waiting'" in page
    assert "state.result?.monitoring_report?'ready':l?.terminal_failure?'blocked':'waiting'" in page
    assert "state.result?'ready':'waiting'" in page
    assert "location.protocol==='file:'" in page
    assert "Open the HTTP demo, not this HTML file." in page
    assert "start-showcase.ps1" in page
