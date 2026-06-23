from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import e2e_harness  # noqa: F401,E402
from e2e_harness.validation import run_validation_suite  # noqa: E402


def test_monitor_validation_suite_passes_release_blocking_checks():
    report = run_validation_suite()
    assert report["summary"]["passed"] is True
    assert report["checks"]["redaction_success"]["passed"] is True
    assert report["checks"]["egress_minimization"]["passed"] is True
    assert report["checks"]["false_alerts_per_1k"]["count"] == 0
    assert report["checks"]["evidence_completeness"]["missing_count"] == 0
    assert all(row["recall"] == 1.0 for row in report["checks"]["seeded_incident_recall"]["by_peril"].values())
