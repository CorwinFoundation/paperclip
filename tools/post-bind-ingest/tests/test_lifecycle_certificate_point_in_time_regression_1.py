from __future__ import annotations

from pathlib import Path
import sys
import tempfile


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from lifecycle import run_preproduction  # noqa: E402


# Regression: ISSUE-001 — certificate incorrectly carried a 30-day validity window
# Found by /qa on 2026-07-12
# Report: .gstack/qa-reports/qa-report-hughes-combine-indiana-advisory-trycloudflare-com-2026-07-11.md
def test_certificate_is_a_point_in_time_attestation() -> None:
    events: list[dict] = []

    def emit(kind: str, message: str, **details) -> None:
        events.append({"kind": kind, "message": message, **details})

    with tempfile.TemporaryDirectory(prefix="certificate-point-in-time-") as tmp:
        lifecycle, _tests = run_preproduction(
            Path(tmp) / "run",
            "certificate-regression",
            emit,
            lambda: {"status": "passed", "summary": "test callback passed", "elapsed_ms": 1},
        )

        certificate = lifecycle["certificate"]
        certificate_html = (Path(tmp) / "run" / "certificate.html").read_text(encoding="utf-8")

        assert certificate["issued_at"].endswith("Z")
        assert "valid_through" not in certificate
        assert "Valid through" not in certificate_html
        assert "Issued at" in certificate_html
        assert any("Point-in-time certificate issued at" in event["message"] for event in events)
