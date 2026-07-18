from __future__ import annotations

from pathlib import Path
import hashlib
import json
import re
import inspect
import shutil
import subprocess
import sys
import tempfile

import pytest

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
DESIGN_PATH = DEMO_DIR / "design" / "BackBond-Underwriting-Desk-3D.html"
DOCKERFILE_PATH = DEMO_DIR / "Dockerfile.demo"
DEPLOY_PATH = DEMO_DIR / "deploy.sh"
sys.path.insert(0, str(DEMO_DIR))

from lifecycle import RUBRIC_PATH, generate_final_report, rubric_display_label, run_preproduction, verify_certificate  # noqa: E402
from app import DemoHandler  # noqa: E402
from rubric.scoring import Rubric  # noqa: E402


def test_underwriting_desk_vault_geometry_and_readout_follow_live_dimensions() -> None:
    html = DESIGN_PATH.read_text(encoding="utf-8")
    assert "24 side panels plus three individually examinable top panels" in html
    assert "for (let N = 0; N < 3; N++)" in html
    assert "-f + (N + 0.5) * ((2 * f) / 3)" in html
    assert "(-2 * f) / 3 + (N + 0.5) * ((2 * f) / 3)" not in html
    assert "registry.dimension_count" in html
    assert "liveEngine.examine(panel.index)" in html
    assert "const TEST_REVEAL_MS = 2500" in html
    assert "await wait(TEST_REVEAL_MS)" in html
    assert 'event.panel.availability === "executable"' in html
    assert "No rating issued." in html
    assert "N/A - Not available" in html
    assert "measured exposures" not in html
    assert "overflow-wrap: anywhere" in html
    assert "grid-template-columns: minmax(190px, 1fr) 90px 126px auto" in html
    assert "white-space: normal" in html
    assert "width: max-content" in html
    assert 'panel.result === "held"' in html
    assert '? "clean"' in html
    assert "resolve to held" not in html
    assert "window.scrollTo(0, 0)" in html
    assert "height: max(320px, 70vw)" in html
    assert 'sessionStorage.getItem("backbondDemoToken")' in html
    assert 'history.replaceState(null, "", location.pathname)' in html
    assert "Partner access key required" in html
    assert "liveEngine.setUnavailable(panel.index)" in html
    assert "SCANNING RISK AREA ${testedIndex + 1} OF ${testedTotal}" in html
    assert "probes in this area · ${panelState(panel)}" in html
    assert "probes across all live areas" in html
    assert "available tests" not in html.lower()
    assert "LIVE TEST" not in html
    assert "Please contact your BackBond partner manager to continue." in html
    assert 'id="submitFeedback" role="status" aria-live="polite"' in html
    assert '$("#submitFeedback").textContent = error.message' in html
    assert "Please wait a moment before starting another scan." in html
    assert "showLatestCompleted" in html
    assert "Run limit reached · showing the latest completed assessment" in html
    assert "mention this notice to Eric" not in html
    assert "continue as a text-only assessment" in html
    for forbidden in (
        "application/x-backbond-reference",
        "1,184 probes",
        "Bondable today",
        "path to Preferred",
        "three disparate-treatment findings",
        "fail-closed",
        "fixture envelope",
        "synthetic envelope",
        "live-executable",
        "customer bind score",
        "customer verdict",
    ):
        assert forbidden.lower() not in html.lower()


def test_public_rate_limit_uses_validated_cloudflare_visitor_ip() -> None:
    source = inspect.getsource(DemoHandler._client_key)
    assert 'self.headers.get("CF-Connecting-IP"' in source
    assert "ipaddress.ip_address(forwarded)" in source
    assert "return self.client_address[0]" in source


def test_partner_quota_is_raised_per_visitor_with_plain_exhaustion_copy() -> None:
    source = inspect.getsource(sys.modules["app"])
    assert 'os.environ.get("DEMO_RUN_QUOTA", "100")' in source
    assert '"quota_scope": "visitor"' in source
    assert "Please contact your BackBond partner manager to continue." in source
    assert "HTTPStatus.TOO_MANY_REQUESTS" in source


def test_completed_quota_restore_reveals_all_executable_rows_as_clean() -> None:
    if shutil.which("node") is None or shutil.which("google-chrome") is None:
        pytest.skip("host-only browser regression requires Node and Google Chrome")
    subprocess.run(
        ["node", str(Path(__file__).with_name("quota_restore_state.cjs")), str(DESIGN_PATH)],
        check=True,
    )


def test_demo_image_includes_truthful_registry_runtime_dependency() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "COPY tools/probe-registry/active_registry.json /app/tools/probe-registry/active_registry.json" in dockerfile
    assert "COPY artifacts/beaaa-15436/BackBond-logo-on-white.png /app/artifacts/beaaa-15436/BackBond-logo-on-white.png" in dockerfile
    logo = Path(__file__).resolve().parents[3] / "artifacts" / "beaaa-15436" / "BackBond-logo-on-white.png"
    assert hashlib.sha256(logo.read_bytes()).hexdigest() == "f32f505a7cb3317003de42e4509a69ea9b758f08a3bb0f41b87a637d8c4a128f"
    assert 'approvedLogo.src = "/assets/backbond-logo-on-white.png"' in DESIGN_PATH.read_text(encoding="utf-8")


def test_deploy_reuses_an_active_token_gated_tunnel_without_privileged_restart() -> None:
    deploy = DEPLOY_PATH.read_text(encoding="utf-8")
    assert "systemctl is-active --quiet backbond-passive-demo-tunnel.service" in deploy
    assert 'DEMO_RUN_QUOTA=${DEMO_RUN_QUOTA:-100}' in deploy


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
        assert lifecycle["initial_scan"]["backbond_score"] is None
        assert lifecycle["updated_scan"]["backbond_score"] is None
        assert lifecycle["bind"]["admittance_class"] == "CONSTRAINED_BIND"
        assert lifecycle["bind"]["real_policy_bound"] is False
        assert lifecycle["installation"]["status"] == "INSTALLED"
        assert len(lifecycle["installation"]["components"]) == 4
        assert lifecycle["ci_gate"]["status"] == "passed"
        assert tests["status"] == "passed"
        assert lifecycle["certificate"]["certificate_scope"] == "scan_only"
        assert "valid_through" not in lifecycle["certificate"]
        assert lifecycle["certificate"]["rubric_label"] == rubric_display_label(lifecycle["certificate"]["rubric_version"])
        assert lifecycle["updated_scan"]["rubric_label"] == rubric_display_label(lifecycle["updated_scan"]["rubric_version"])
        registry_dimension_count = len(Rubric.load(RUBRIC_PATH).active_dim_ids)
        assert len(lifecycle["initial_scan"]["panels"]) == registry_dimension_count
        assert len(lifecycle["updated_scan"]["panels"]) == registry_dimension_count
        assert [panel["index"] for panel in lifecycle["updated_scan"]["panels"]] == list(range(registry_dimension_count))
        assert all(panel["name"][:1].isupper() for panel in lifecycle["updated_scan"]["panels"])
        assert {panel["pillar"] for panel in lifecycle["updated_scan"]["panels"]} == {
            "GOVERNANCE",
            "ROBUSTNESS",
            "BIAS_FAIRNESS",
            "OPS",
        }
        assert {panel["result"] for panel in lifecycle["updated_scan"]["panels"]} <= {"held", "exposure", None}
        assert lifecycle["updated_scan"]["registry"]["dimension_count"] == 27
        assert lifecycle["updated_scan"]["registry"]["measured_dimension_count"] == 4
        assert lifecycle["updated_scan"]["registry"]["unique_executable_count"] == 90
        dimension_events = [event for event in events if event["kind"] == "dimension"]
        assert len(dimension_events) == registry_dimension_count * 2
        assert all(event["panel"]["evidence_id"] or not event["panel"]["applicable"] for event in dimension_events)
        assert verify_certificate(lifecycle["certificate"])
        customer_projection = {key: value for key, value in lifecycle.items() if not key.startswith("_")}
        customer_json = json.dumps(customer_projection, sort_keys=True)
        assert '"backbond_score": null' in customer_json
        assert '"rating_status": "not_available"' in customer_json
        assert "only 4 of 27 risk areas tested; no rating can be issued." in customer_json
        assert '"score": 81' not in customer_json
        assert '"backbond_score": 81' not in customer_json
        diagnostics = json.loads((Path(tmp) / "run" / "internal-diagnostics.json").read_text(encoding="utf-8"))
        assert diagnostics["synthetic_ci_gate"]["score"] == 81
        assert lifecycle["certificate"]["backbond_score"] is None
        assert lifecycle["certificate"]["rating_status"] == "not_available"
        assert lifecycle["certificate"]["tested_area_count"] == 4
        assert lifecycle["certificate"]["risk_area_count"] == 27
        assert len(lifecycle["certificate"]["tested_areas"]) == 4
        assert {row["result"] for row in lifecycle["certificate"]["tested_areas"]} == {"clean"}
        assert lifecycle["certificate"]["not_tested_area_count"] == 23
        assert lifecycle["certificate"]["rating_issued"] is False
        certificate_html = (Path(tmp) / "run" / "certificate.html").read_text(encoding="utf-8")
        assert "N/A - Not available" in certificate_html
        assert "only 4 of 27 risk areas tested; no rating can be issued." in certificate_html
        assert certificate_html.count(">CLEAN<") == 4
        assert certificate_html.count("N/A - not tested yet") == 23
        visible_certificate_html = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", certificate_html)
        assert ">81<" not in visible_certificate_html
        assert "AGENT RISK RATINGS &amp; INSURANCE" in certificate_html
        tampered = {**lifecycle["certificate"], "tested_area_count": 5}
        assert not verify_certificate(tampered)
        assert (Path(tmp) / "run" / "certificate.html").is_file()
        assert {event["kind"] for event in events} >= {"scan", "bind", "install", "version", "cicd", "certificate"}


def test_generated_report_uses_backbond_canon_and_demo_boundary() -> None:
    events: list[dict] = []

    def emit(kind: str, message: str, **details) -> None:
        events.append({"kind": kind, "message": message, **details})

    passive = {
        "raw_events": [{} for _ in range(9)],
        "detections": [{} for _ in range(7)],
        "tracker_rows": [{} for _ in range(7)],
        "report": {
            "perils_detected": ["P1", "P2", "P3", "P4", "P5", "P6"],
            "continuity": {"continuity_status": "healthy"},
        },
    }
    with tempfile.TemporaryDirectory(prefix="scanner-lifecycle-report-") as tmp:
        output_dir = Path(tmp) / "run"
        lifecycle, _tests = run_preproduction(
            output_dir,
            "test-run",
            emit,
            lambda: {"status": "passed", "summary": "test callback passed", "elapsed_ms": 1},
        )
        generate_final_report(output_dir, lifecycle, passive, emit)
        html = (output_dir / "backbond-report.html").read_text(encoding="utf-8")
        assert "<title>BackBond Scanner Report" in html
        assert "BackBond Scanner Report" in html
        assert "ARE Spot-Scan" not in html
        assert "ARE Insurance" not in html
        assert "BOUND_DEMO and bind-threshold signals" in html
        assert "not evidence of bound insurance coverage" in html
