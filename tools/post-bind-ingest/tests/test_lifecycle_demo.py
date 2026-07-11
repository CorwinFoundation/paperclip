from __future__ import annotations

from pathlib import Path
import sys
import tempfile

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from lifecycle import run_preproduction, verify_certificate  # noqa: E402


def test_real_scanner_bind_ci_and_certificate_lifecycle() -> None:
    events: list[dict] = []

    def emit(kind: str, message: str, **details) -> None:
        events.append({"kind": kind, "message": message, **details})

    with tempfile.TemporaryDirectory(prefix="scanner-lifecycle-") as tmp:
        lifecycle, tests = run_preproduction(
            Path(tmp) / "run",
            "test-run",
            emit,
            lambda: {"status": "passed", "summary": "test callback passed", "elapsed_ms": 1},
        )
        assert lifecycle["initial_scan"]["score"] == 80
        assert lifecycle["updated_scan"]["score"] == 81
        assert lifecycle["score_delta"] == 1
        assert lifecycle["bind"]["admittance_class"] == "CONSTRAINED_BIND"
        assert lifecycle["bind"]["real_policy_bound"] is False
        assert lifecycle["installation"]["status"] == "INSTALLED"
        assert len(lifecycle["installation"]["components"]) == 4
        assert lifecycle["ci_gate"]["exit_code"] == 0
        assert tests["status"] == "passed"
        assert lifecycle["certificate"]["certificate_scope"] == "scan_only"
        assert verify_certificate(lifecycle["certificate"])
        tampered = {**lifecycle["certificate"], "backbond_score": 0}
        assert not verify_certificate(tampered)
        assert (Path(tmp) / "run" / "certificate.html").is_file()
        assert {event["kind"] for event in events} >= {"scan", "bind", "install", "version", "cicd", "certificate"}