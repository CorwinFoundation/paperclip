from __future__ import annotations

from pathlib import Path
import sys
import tempfile


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from lifecycle import rubric_display_label, run_preproduction, verify_certificate  # noqa: E402


# Regression: ISSUE-004 — demo presented rubric 1.3.0 as released during its rollout
# Found by /qa on 2026-07-12
# Report: .gstack/qa-reports/qa-report-hughes-combine-indiana-advisory-trycloudflare-com-2026-07-11.md

def test_demo_labels_the_in_flight_rubric_as_candidate() -> None:
    with tempfile.TemporaryDirectory(prefix="rubric-candidate-") as tmp:
        lifecycle, _tests = run_preproduction(
            Path(tmp) / "run",
            "rubric-candidate-regression",
            lambda *_args, **_kwargs: None,
            lambda: {"status": "passed", "summary": "test callback passed", "elapsed_ms": 1},
        )

        assert lifecycle["initial_scan"]["rubric_release_status"] == "candidate"
        assert lifecycle["updated_scan"]["rubric_release_status"] == "candidate"
        certificate = lifecycle["certificate"]
        assert certificate["rubric_release_status"] == "candidate"
        assert verify_certificate(certificate)

        certificate_html = (Path(tmp) / "run" / "certificate.html").read_text(encoding="utf-8")
        assert rubric_display_label(certificate["rubric_version"]) in certificate_html

        page = (DEMO_DIR / "index_v2.html").read_text(encoding="utf-8")
        assert "rubric_release_status" in page
        assert "c.rubric_label||c.rubric_version" in page