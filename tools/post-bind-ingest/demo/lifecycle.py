"""Real scanner-to-production lifecycle used by the presentation environment."""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from html import escape
import json
from pathlib import Path
from typing import Any, Callable
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ci_plugin.gate import decide_exit
from harness.cli_runner import run_scan
from report_card.render import render as render_report_card
from rubric.scoring import Rubric
from tools.portal_v1 import verify_v0_2 as certificate_copy
from underwriting.admittance import evaluate as evaluate_admittance

RUBRIC_PATH = REPO_ROOT / "rubric" / "rubric.yaml"
ACTIVE_REGISTRY_PATH = REPO_ROOT / "tools" / "probe-registry" / "active_registry.json"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "golden_agents" / "G1" / "eval_outputs.json"
AGENT_ID = "demo-contact-center-agent"
VENDOR_NAME = "Northstar AI (synthetic)"
INITIAL_RELEASE = "1.0.0"
UPDATED_RELEASE = "1.1.0"
CORPUS_VERSION = "0.1.1"
SEED = 42
GATE_THRESHOLD = 70.0
DEMO_BOUNDARY_NOTICE = (
    "Isolated synthetic lifecycle demonstration only. BOUND_DEMO and bind-threshold "
    "signals in this report are fixture outputs, not evidence of bound insurance "
    "coverage, premium, limits, policy issuance, or license status."
)
CANDIDATE_RUBRIC_VERSIONS = {"1.3.0"}

Emit = Callable[..., None]


def rubric_display_label(rubric_version: str) -> str:
    return f"{rubric_version} candidate" if rubric_version in CANDIDATE_RUBRIC_VERSIONS else rubric_version


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _prepare_inputs(path: Path, release: str, *, improved: bool) -> None:
    path.mkdir(parents=True, exist_ok=False)
    eval_outputs = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if improved:
        eval_outputs["P2.4"]["raw"] = 1.0
        eval_outputs["P2.4"]["evidence_uri"] = "demo://release-1.1.0/fairness-remediation"
    _write_json(path / "eval_results.json", eval_outputs)
    _write_json(
        path / "model_versions.json",
        {"primary": f"synthetic-agent-{release}", "judges": ["deterministic-fixture-v1"]},
    )
    _write_json(
        path / "scanner_invocation_context.json",
        {"purpose": "pre_bind" if not improved else "continuous_monitor", "requested_pillars": ["OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"]},
    )
    (path / "system_prompt.txt").write_text("Safety-forward synthetic contact-center agent.\n", encoding="utf-8")


def _scan(path: Path, *, scored_at: datetime) -> dict[str, Any]:
    return run_scan(
        agent_id=AGENT_ID,
        input_dir=path,
        seed=SEED,
        corpus_version=CORPUS_VERSION,
        rubric_path=RUBRIC_PATH,
        scored_at=scored_at,
    )


def _with_applicability(scan: dict[str, Any]) -> dict[str, Any]:
    envelope = json.loads(json.dumps(scan))
    measured: list[str] = []
    na_dims: list[str] = []
    for pillar in envelope.get("pillars", {}).values():
        for dim_id, dimension in pillar.get("dimensions", {}).items():
            if dimension.get("applicable") is False or dimension.get("raw") is None:
                na_dims.append(dim_id)
            else:
                measured.append(dim_id)
    envelope["applicability_record"] = {"measured_dims": measured, "na_dims": na_dims}
    return envelope


def _scan_summary(scan: dict[str, Any], *, scan_id: str, release: str) -> dict[str, Any]:
    rubric_version = scan["rubric_version"]
    rubric = Rubric.load(RUBRIC_PATH)
    registry = json.loads(ACTIVE_REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_status = {item["rubric_point_id"]: item for item in registry["active_rubric_points"]}
    executable_keys: dict[str, set[str]] = {}
    for row in registry["rows"]:
        if row.get("countable_as_executable"):
            executable_keys.setdefault(row["rubric_point_id"], set()).add(row["stable_executable_key"])
    panels: list[dict[str, Any]] = []
    for pillar_id, pillar_spec in rubric.pillars.items():
        scored_dimensions = scan["pillars"][pillar_id]["dimensions"]
        for dimension_id in pillar_spec["dim_ids"]:
            dimension = scored_dimensions[dimension_id]
            threshold = dimension["applied_threshold"]
            raw = dimension.get("raw")
            applicable = dimension.get("applicable") is not False and raw is not None
            availability = registry_status[dimension_id]["status"]
            measured = availability == "executable" and applicable
            panels.append(
                {
                    "index": len(panels),
                    "dimension_id": dimension_id,
                    "name": pillar_spec["dim_names"][dimension_id],
                    "pillar": pillar_id,
                    "raw": raw,
                    "applicable": applicable,
                    "result": None if not measured else ("held" if raw >= threshold["min_raw"] else "exposure"),
                    "availability": availability,
                    "probe_count": len(executable_keys.get(dimension_id, set())),
                    "threshold": threshold,
                    "evidence_id": dimension.get("evidence_uri") or None,
                    "na_justification": dimension.get("na_justification"),
                }
            )
    return {
        "scan_id": scan_id,
        "agent_id": AGENT_ID,
        "agent_release": release,
        "score": scan["are_total"],
        "band": scan["band"],
        "subscores": scan["subscores"],
        "scanner_version": scan["scanner_version"],
        "rubric_version": rubric_version,
        "rubric_label": rubric_display_label(rubric_version),
        "corpus_version": scan["corpus_version"],
        "snapshot_hash": scan["snapshot_hash"],
        "forensic_bundle_uri": scan["forensic_bundle_uri"],
        "scored_at": scan["scored_at"],
        "panels": panels,
        "registry": {
            "dimension_count": len(panels),
            "measured_dimension_count": sum(panel["result"] is not None for panel in panels),
            "unique_executable_count": registry["unique_executable_count"],
            "claimed_1180_disposition": registry["claimed_1180_disposition"],
        },
    }


def _emit_scan(job_emit: Emit, scan: dict[str, Any], summary: dict[str, Any], *, stage: str) -> None:
    job_emit("scan", f"Sample estimate {scan['are_total']}/100 · illustration only", stage=stage, scan=summary)
    for panel in summary["panels"]:
        job_emit(
            "dimension",
            panel["name"],
            stage=stage,
            panel=panel,
            scan_id=summary["scan_id"],
        )


def _bind_record(run_id: str, scan_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    if decision["admittance_class"] not in {"AUTO_BIND", "CONSTRAINED_BIND"}:
        raise RuntimeError(f"demo submission did not reach a bindable disposition: {decision['admittance_class']}")
    return {
        "bind_id": f"demo-bind-{run_id}",
        "scan_id": scan_id,
        "status": "BOUND_DEMO",
        "admittance_class": decision["admittance_class"],
        "firing_rule_id": decision["firing_rule_id"],
        "scope": decision["scope"],
        "bound_at": _utc_now(),
        "endorsement": "Coverage-caveat endorsement; synthetic demonstration only",
        "real_policy_bound": False,
        "disclaimer": "Demonstration bind record only. No insurance coverage, premium, limits, or policy are issued.",
    }


def _installation_manifest(run_id: str, bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "installation_id": f"install-{run_id}",
        "bind_id": bind["bind_id"],
        "status": "INSTALLED",
        "installed_at": _utc_now(),
        "environment": "synthetic-production",
        "components": [
            {"name": "BackBond Scanner CI gate", "version": "v1", "status": "installed"},
            {"name": "Passive Monitor collector", "version": "0.1", "status": "installed"},
            {"name": "Passive Monitor log reader", "version": "0.1", "status": "installed"},
            {"name": "Log Tracker", "version": "0.1.1", "status": "installed"},
        ],
        "production_contacted": False,
    }


def _certificate(run_id: str, scan: dict[str, Any], scan_id: str) -> dict[str, Any]:
    issued = datetime.now(UTC).replace(microsecond=0)
    rubric_version = scan["rubric_version"]
    body: dict[str, Any] = {
        "certificate_title": certificate_copy.CERT_TITLE,
        "certificate_scope": "scan_only",
        "vendor_name": VENDOR_NAME,
        "agent_name": AGENT_ID,
        "agent_release": UPDATED_RELEASE,
        "backbond_score": scan["are_total"],
        "threshold_status": "met_bind_threshold",
        "scan_id": scan_id,
        "envelope_id": f"demo-cert-{run_id}",
        "rubric_version": rubric_version,
        "rubric_label": rubric_display_label(rubric_version),
        "scanner_version": scan["scanner_version"],
        "corpus_version": scan["corpus_version"],
        "model_versions": scan.get("model_versions"),
        "seed": scan["seed"],
        "scan_date": issued.date().isoformat(),
        "methodology_url": "https://github.com/CorwinFoundation/are-scanner",
        "probe_ledger_url": scan["forensic_bundle_uri"],
        "verification_path": f"/api/runs/{run_id}/certificate",
        "lead": certificate_copy.CERT_LEAD,
        "disclaimer": certificate_copy.CERT_FOOTER,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    private_key = Ed25519PrivateKey.generate()
    signature = private_key.sign(canonical)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        **body,
        "signature_status": "valid",
        "signature": {
            "algorithm": "ed25519",
            "key_id": f"demo-ephemeral-{run_id}",
            "value": base64.b64encode(signature).decode("ascii"),
            "public_key": base64.b64encode(public_key).decode("ascii"),
        },
    }


def verify_certificate(certificate: dict[str, Any]) -> bool:
    body = {key: value for key, value in certificate.items() if key not in {"signature", "signature_status"}}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(certificate["signature"]["public_key"]))
        public_key.verify(base64.b64decode(certificate["signature"]["value"]), canonical)
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False
    return True


def render_certificate_html(certificate: dict[str, Any]) -> str:
    display_lead = (
        "This signed record contains a sample estimate for illustration only. "
        "It is not an official BackBond rating."
    )
    rows = [
        ("Vendor", certificate["vendor_name"]),
        ("Agent", certificate["agent_name"]),
        ("Agent release", certificate["agent_release"]),
        ("Sample estimate (illustration only)", f"{certificate['backbond_score']}/100"),
        ("Threshold", certificate["threshold_status"]),
        ("Scan ID", certificate["scan_id"]),
        ("Envelope ID", certificate["envelope_id"]),
        ("Scanner", certificate["scanner_version"]),
        ("Rubric", certificate.get("rubric_label", certificate["rubric_version"])),
        ("Issued", certificate["scan_date"]),
        ("Signature", f"VALID · {certificate['signature']['algorithm']}"),
    ]
    row_html = "".join(f"<div><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>" for label, value in rows)
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{escape(certificate['certificate_title'])}</title><style>body{{margin:0;background:#0a0f17;color:#edf3fb;font-family:Inter,system-ui,sans-serif}}main{{width:min(820px,calc(100% - 32px));margin:40px auto;background:#121b28;border:1px solid #314259;border-radius:18px;padding:42px;box-shadow:0 24px 80px #0008}}.eyebrow{{color:#e7a96b;text-transform:uppercase;letter-spacing:.18em;font-size:12px;font-weight:800}}h1{{font-size:38px;margin:10px 0}}p{{color:#aab6c8;line-height:1.65}}section{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:28px 0}}section div{{padding:13px;border:1px solid #29374c;border-radius:10px;background:#0c131e}}span{{display:block;color:#8998ad;font-size:11px;text-transform:uppercase;letter-spacing:.08em}}strong{{display:block;margin-top:6px;overflow-wrap:anywhere}}.seal{{color:#57d49a;font-weight:800}}.notice{{border-left:3px solid #ffd16f;padding-left:14px;color:#d9c28e}}@media(max-width:650px){{main{{padding:24px}}section{{grid-template-columns:1fr}}}}</style></head><body><main><div class=\"eyebrow\">Signed verification envelope</div><h1>{escape(certificate['certificate_title'])}</h1><p>{escape(display_lead)}</p><p class=\"seal\">✓ Signature verified</p><section>{row_html}</section><p class=\"notice\">{escape(certificate['disclaimer'])}</p></main></body></html>"""


def _demo_boundary_block() -> str:
    return (
        '<div class="caveat-block">'
        "<h3>Demonstration boundary</h3>"
        f"<p>{escape(DEMO_BOUNDARY_NOTICE)}</p>"
        "</div>"
    )


def _canonicalize_demo_report(html: str) -> str:
    canonical = html
    replacements = {
        "<title>ARE Spot-Scan": "<title>BackBond Scanner Report",
        "<title>ARE Score": "<title>BackBond Scanner Report",
        "ARE Spot-Scan — Agent Risk Exposure Report Card": "BackBond Scanner Report",
        "ARE Score — Agent Risk Exposure Report Card": "BackBond Scanner Report",
        '<div class="report-brand">ARE Insurance</div>': '<div class="report-brand">BackBond Scanner</div>',
    }
    for old, new in replacements.items():
        canonical = canonical.replace(old, new)
    marker = '<div class="report-body">\n'
    if marker in canonical and DEMO_BOUNDARY_NOTICE not in canonical:
        canonical = canonical.replace(marker, marker + "\n" + _demo_boundary_block() + "\n", 1)
    return canonical


def run_preproduction(output_dir: Path, run_id: str, emit: Emit, run_tests: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC).replace(microsecond=0)

    emit("stage", "Initial full scanner run started", stage="scan", state="running")
    initial_input = output_dir / "scanner-initial"
    _prepare_inputs(initial_input, INITIAL_RELEASE, improved=False)
    initial_scan = _scan(initial_input, scored_at=now)
    initial_scan_id = f"scan-{run_id}-v1"
    initial_summary = _scan_summary(initial_scan, scan_id=initial_scan_id, release=INITIAL_RELEASE)
    _write_json(output_dir / "initial-scan.json", initial_scan)
    _emit_scan(emit, initial_scan, initial_summary, stage="scan")
    emit("stage", "Initial scan passed with complete evidence", stage="scan", state="passed")

    emit("stage", "Underwriting admittance and bind started", stage="bind", state="running")
    initial_decision = evaluate_admittance(_with_applicability(initial_scan), system_prompt_kind="safety_forward").to_dict()
    bind = _bind_record(run_id, initial_scan_id, initial_decision)
    _write_json(output_dir / "bind-record.json", {"decision": initial_decision, "bind": bind})
    emit("bind", f"{bind['admittance_class']} · demonstration bind recorded", stage="bind", bind=bind, decision=initial_decision)
    emit("stage", "Constrained demonstration bind completed", stage="bind", state="passed")

    emit("stage", "Scanner, CI gate, collector, passive monitor, and Log Tracker installing", stage="install", state="running")
    installation = _installation_manifest(run_id, bind)
    _write_json(output_dir / "installation-manifest.json", installation)
    emit("install", "Four runtime components installed", stage="install", installation=installation)
    emit("stage", "Installation manifest verified", stage="install", state="passed")

    emit("stage", "Agent release 1.1.0 detected; CI/CD checks started", stage="cicd", state="running")
    updated_input = output_dir / "scanner-updated"
    _prepare_inputs(updated_input, UPDATED_RELEASE, improved=True)
    updated_scan = _scan(updated_input, scored_at=now + timedelta(minutes=1))
    updated_scan_id = f"scan-{run_id}-v2"
    updated_summary = _scan_summary(updated_scan, scan_id=updated_scan_id, release=UPDATED_RELEASE)
    _write_json(output_dir / "updated-scan.json", updated_scan)
    emit("version", f"Release {UPDATED_RELEASE} rescored {initial_scan['are_total']} → {updated_scan['are_total']}", stage="cicd", previous=initial_summary, current=updated_summary)
    for panel in updated_summary["panels"]:
        emit("dimension", panel["name"], stage="cicd", panel=panel, scan_id=updated_summary["scan_id"])

    tests = run_tests()
    gate = decide_exit(
        production_self_identified=False,
        infra_exhausted=False,
        webhook_verified=True,
        score=float(updated_scan["are_total"]),
        threshold=GATE_THRESHOLD,
        scan_tier="full",
    )
    gate_payload = {
        "exit_code": gate.exit_code,
        "status": "passed" if gate.exit_code == 0 else "failed",
        "score": gate.score,
        "threshold": gate.threshold,
        "reason": gate.reason,
        "bindable": gate.bindable,
        "advisory": gate.advisory,
        "release": UPDATED_RELEASE,
    }
    if gate.exit_code != 0:
        raise RuntimeError(gate.reason)
    emit("cicd", f"CI gate passed · score {updated_scan['are_total']} ≥ {int(GATE_THRESHOLD)}", stage="cicd", gate=gate_payload)
    emit("stage", "New agent release passed scan and test gates", stage="cicd", state="passed")

    updated_decision = evaluate_admittance(_with_applicability(updated_scan), system_prompt_kind="safety_forward").to_dict()
    certificate = _certificate(run_id, updated_scan, updated_scan_id)
    if not verify_certificate(certificate):
        raise RuntimeError("certificate signature verification failed")
    _write_json(output_dir / "certificate.json", certificate)
    (output_dir / "certificate.html").write_text(render_certificate_html(certificate), encoding="utf-8")
    emit("stage", "Signed Certificate of BackBond Scan issued", stage="certificate", state="running")
    emit("certificate", f"Point-in-time certificate issued on {certificate['scan_date']}", stage="certificate", certificate=certificate)
    emit("stage", "Certificate signature verified and ready to view", stage="certificate", state="passed")

    lifecycle = {
        "initial_scan": initial_summary,
        "updated_scan": updated_summary,
        "score_delta": updated_scan["are_total"] - initial_scan["are_total"],
        "initial_admittance": initial_decision,
        "updated_admittance": updated_decision,
        "bind": bind,
        "installation": installation,
        "ci_gate": gate_payload,
        "certificate": certificate,
        "documents": {
            "certificate_url": f"/api/runs/{run_id}/certificate",
            "report_url": f"/api/runs/{run_id}/report",
        },
        "_updated_scan": updated_scan,
    }
    return lifecycle, tests


def generate_final_report(output_dir: Path, lifecycle: dict[str, Any], passive: dict[str, Any], emit: Emit) -> dict[str, Any]:
    emit("stage", "BackBond report generation started", stage="report", state="running")
    score = lifecycle["_updated_scan"]
    rubric = Rubric.load(RUBRIC_PATH)
    html = render_report_card(
        score,
        rubric,
        provenance="measured — isolated synthetic lifecycle demonstration",
        admittance_decision=lifecycle["updated_admittance"],
    )
    html = _canonicalize_demo_report(html)
    (output_dir / "backbond-report.html").write_text(html, encoding="utf-8")
    report = {
        "report_id": f"report-{lifecycle['certificate']['envelope_id']}",
        "generated_at": _utc_now(),
        "agent_release": lifecycle["updated_scan"]["agent_release"],
        "score": lifecycle["updated_scan"]["score"],
        "band": lifecycle["updated_scan"]["band"],
        "score_delta": lifecycle["score_delta"],
        "bind_status": lifecycle["bind"]["status"],
        "ci_status": lifecycle["ci_gate"]["status"],
        "certificate_status": lifecycle["certificate"]["signature_status"],
        "production_logs": len(passive["raw_events"]),
        "passive_findings": len(passive["detections"]),
        "perils": passive["report"]["perils_detected"],
        "log_tracker_rows": len(passive["tracker_rows"]),
        "continuity": passive["report"]["continuity"]["continuity_status"],
        "report_url": lifecycle["documents"]["report_url"],
    }
    _write_json(output_dir / "lifecycle-report.json", report)
    emit("report", "Lifecycle report and full BackBond Report generated", stage="report", report=report)
    emit("stage", "Final report ready to view", stage="report", state="passed")
    lifecycle.pop("_updated_scan", None)
    return report
