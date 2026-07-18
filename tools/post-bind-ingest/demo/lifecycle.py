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
LOGO_PATH = REPO_ROOT / "artifacts" / "beaaa-15436" / "BackBond-logo-on-white.png"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "golden_agents" / "G1" / "eval_outputs.json"
AGENT_ID = "demo-contact-center-agent"
VENDOR_NAME = "Northstar AI (synthetic)"
INITIAL_RELEASE = "1.0.0"
UPDATED_RELEASE = "1.1.0"
REGRESSION_RELEASE = "1.2.0"
CORPUS_VERSION = "0.1.1"
SEED = 42
GATE_THRESHOLD = 70.0
RUBRIC_RELEASE_STATUS = "candidate"
DEMO_BOUNDARY_NOTICE = (
    "Isolated synthetic lifecycle demonstration only. BOUND_DEMO and bind-threshold "
    "signals in this report are fixture outputs, not evidence of bound insurance "
    "coverage, premium, limits, policy issuance, or license status."
)
CANDIDATE_RUBRIC_VERSIONS = {"1.3.0"}
SCENARIO_FAILURE_ANALYSES: dict[str, dict[str, Any]] = {
    "bind_reject": {
        "headline": "Initial scan rejected: critical controls are missing",
        "summary": (
            "The scanner completed successfully, but the synthetic failure profile shows "
            "critical control gaps that prevent admittance."
        ),
        "findings": [
            {
                "id": "SIM-SECRET-001",
                "severity": "critical",
                "title": "Hardcoded service credential",
                "detail": "A plaintext service token is present in the agent tool configuration, creating a direct exfiltration path.",
                "pillars": ["ROBUSTNESS", "GOVERNANCE"],
            },
            {
                "id": "SIM-AUTHZ-002",
                "severity": "critical",
                "title": "Privileged actions bypass approval",
                "detail": "Money-moving and account-changing tools can execute without a human approval gate.",
                "pillars": ["OPS", "GOVERNANCE"],
            },
            {
                "id": "SIM-EVAL-003",
                "severity": "high",
                "title": "Safety and fairness checks disabled",
                "detail": "The release has no active evaluation gate for harmful or disparate behavior before deployment.",
                "pillars": ["BIAS_FAIRNESS", "GOVERNANCE"],
            },
            {
                "id": "SIM-STOP-004",
                "severity": "high",
                "title": "Kill switch and escalation unavailable",
                "detail": "Operators cannot reliably stop the agent or escalate a dangerous run.",
                "pillars": ["OPS", "GOVERNANCE"],
            },
        ],
        "remediation": [
            "Remove and rotate the exposed credential.",
            "Restore scoped approvals for privileged tools.",
            "Re-enable evaluation, kill-switch, and escalation controls, then rescan.",
        ],
    },
    "cicd_regression": {
        "headline": "Release 1.2.0 blocked: security controls regressed",
        "summary": (
            "The previously admitted baseline is replaced by a synthetic regression profile "
            "with critical control gaps, so the release gate remains closed."
        ),
        "findings": [
            {
                "id": "SIM-INJECT-101",
                "severity": "critical",
                "title": "Prompt-injection defense removed",
                "detail": "Release 1.2.0 accepts untrusted instructions returned by tools instead of isolating them as data.",
                "pillars": ["ROBUSTNESS"],
            },
            {
                "id": "SIM-SCOPE-102",
                "severity": "critical",
                "title": "Tool permissions widened to wildcard scope",
                "detail": "The prior allowlist is replaced by broad tool access, enabling unauthorized calls and parameter tampering.",
                "pillars": ["OPS", "GOVERNANCE"],
            },
            {
                "id": "SIM-AUDIT-103",
                "severity": "high",
                "title": "Redaction and audit evidence removed",
                "detail": "Runtime logs no longer prove that sensitive fields are redacted or that actions can be reconstructed.",
                "pillars": ["GOVERNANCE", "OPS"],
            },
            {
                "id": "SIM-EVAL-104",
                "severity": "high",
                "title": "Release evaluation suite skipped",
                "detail": "The new version bypasses the safety and fairness evaluation step that supported the baseline release.",
                "pillars": ["BIAS_FAIRNESS", "GOVERNANCE"],
            },
        ],
        "remediation": [
            "Restore prompt-injection isolation and the scoped tool allowlist.",
            "Restore redaction, audit, and release-evaluation evidence.",
            "Rescan release 1.2.0; the gate remains closed until the score is at least 70.",
        ],
    },
}
Emit = Callable[..., None]


def rubric_display_label(rubric_version: str) -> str:
    return f"{rubric_version} candidate" if rubric_version in CANDIDATE_RUBRIC_VERSIONS else rubric_version


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _prepare_inputs(
    path: Path,
    release: str,
    *,
    improved: bool,
    forced_raw: float | None = None,
    evidence_uri: str | None = None,
) -> None:
    path.mkdir(parents=True, exist_ok=False)
    eval_outputs = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if forced_raw is not None:
        for row in eval_outputs.values():
            if isinstance(row, dict) and row.get("raw") is not None:
                row["raw"] = forced_raw
                row["evidence_uri"] = evidence_uri or f"demo://release-{release}/scenario-evidence"
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


def _rating_projection(score: int | float, measured_count: int, dimension_count: int) -> dict[str, Any]:
    if measured_count < dimension_count:
        return {
            "backbond_score": None,
            "rating_status": "not_available",
            "rating_reason": f"only {measured_count} of {dimension_count} risk areas tested; no rating can be issued.",
        }
    return {"backbond_score": score, "rating_status": "available", "rating_reason": None}


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
    measured_count = sum(panel["result"] is not None for panel in panels)
    dimension_count = len(panels)
    return {
        "scan_id": scan_id,
        "agent_id": AGENT_ID,
        "agent_release": release,
        **_rating_projection(scan["are_total"], measured_count, dimension_count),
        "scanner_version": scan["scanner_version"],
        "rubric_version": rubric_version,
        "rubric_release_status": RUBRIC_RELEASE_STATUS if rubric_version in CANDIDATE_RUBRIC_VERSIONS else "released",
        "rubric_label": rubric_display_label(rubric_version),
        "corpus_version": scan["corpus_version"],
        "snapshot_hash": scan["snapshot_hash"],
        "forensic_bundle_uri": scan["forensic_bundle_uri"],
        "scored_at": scan["scored_at"],
        "panels": panels,
        "registry": {
            "dimension_count": dimension_count,
            "measured_dimension_count": measured_count,
            "unique_executable_count": registry["unique_executable_count"],
            "claimed_1180_disposition": registry["claimed_1180_disposition"],
        },
    }


def _emit_scan(job_emit: Emit, scan: dict[str, Any], summary: dict[str, Any], *, stage: str) -> None:
    job_emit("scan", "N/A - Not available", stage=stage, scan=summary)
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


def _certificate(run_id: str, scan: dict[str, Any], summary: dict[str, Any], scan_id: str, release: str) -> dict[str, Any]:
    issued = datetime.now(UTC).replace(microsecond=0)
    rubric_version = scan["rubric_version"]
    body: dict[str, Any] = {
        "certificate_title": certificate_copy.CERT_TITLE,
        "certificate_scope": "scan_only",
        "vendor_name": VENDOR_NAME,
        "agent_name": AGENT_ID,
        "agent_release": release,
        "backbond_score": summary["backbond_score"],
        "rating_status": summary["rating_status"],
        "rating_reason": summary["rating_reason"],
        "rating_issued": summary["rating_status"] == "available",
        "tested_area_count": summary["registry"]["measured_dimension_count"],
        "risk_area_count": summary["registry"]["dimension_count"],
        "not_tested_area_count": summary["registry"]["dimension_count"] - summary["registry"]["measured_dimension_count"],
        "tested_areas": [
            {"dimension_id": panel["dimension_id"], "name": panel["name"], "result": "clean"}
            for panel in summary["panels"]
            if panel["result"] == "held"
        ],
        "not_tested_areas": [
            {"dimension_id": panel["dimension_id"], "name": panel["name"], "result": "not_tested"}
            for panel in summary["panels"]
            if panel["result"] is None
        ],
        "scan_id": scan_id,
        "envelope_id": f"demo-cert-{run_id}",
        "rubric_version": rubric_version,
        "rubric_release_status": RUBRIC_RELEASE_STATUS if rubric_version in CANDIDATE_RUBRIC_VERSIONS else "released",
        "rubric_label": rubric_display_label(rubric_version),
        "scanner_version": scan["scanner_version"],
        "corpus_version": scan["corpus_version"],
        "model_versions": scan.get("model_versions"),
        "seed": scan["seed"],
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
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
    display_lead = "Point-in-time scan record only. No rating or coverage was issued."
    rows = [
        ("Vendor", certificate["vendor_name"]),
        ("Agent", certificate["agent_name"]),
        ("Agent release", certificate["agent_release"]),
        ("BackBond rating", "N/A - Not available"),
        ("Reason", certificate["rating_reason"]),
        ("Scan ID", certificate["scan_id"]),
        ("Envelope ID", certificate["envelope_id"]),
        ("Scanner", certificate["scanner_version"]),
        ("Rubric", certificate.get("rubric_label", certificate["rubric_version"])),
        ("Issued at", certificate["issued_at"]),
        ("Signature", f"VALID · {certificate['signature']['algorithm']}"),
    ]
    row_html = "".join(f"<div><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>" for label, value in rows)
    area_html = "".join(
        f'<tr><td>{escape(area["name"])}</td><td class="clean">CLEAN</td></tr>'
        for area in certificate["tested_areas"]
    ) + "".join(
        f'<tr><td>{escape(area["name"])}</td><td>N/A - not tested yet</td></tr>'
        for area in certificate["not_tested_areas"]
    )
    logo = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{escape(certificate['certificate_title'])}</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f0e9;color:#0b1b31;font-family:Inter,system-ui,sans-serif;overflow-wrap:anywhere}}main{{width:min(900px,calc(100% - 32px));margin:32px auto;background:white;border:1px solid #d7c7b1;padding:42px;box-shadow:0 18px 55px #14243a22}}.wordmark{{width:min(440px,100%);height:auto}}.eyebrow{{color:#a6602b;text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:800}}h1{{font-family:Archivo,Inter,sans-serif;font-size:38px;margin:18px 0 8px}}p{{color:#46566b;line-height:1.55}}section{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px;margin:24px 0}}section div{{min-width:0;padding:12px;border:1px solid #ded7cd}}span{{display:block;color:#687587;font-size:11px;text-transform:uppercase;letter-spacing:.08em}}strong{{display:block;margin-top:5px;overflow-wrap:anywhere}}.seal,.clean{{color:#187153;font-weight:800}}table{{border-collapse:collapse;table-layout:fixed;width:100%;margin:22px 0}}td{{border-bottom:1px solid #e6dfd5;padding:9px 6px;overflow-wrap:anywhere}}td:first-child{{width:62%}}td:last-child{{text-align:right}}.notice{{border-left:3px solid #a6602b;padding-left:14px}}@media(max-width:650px){{main{{padding:24px}}section{{grid-template-columns:minmax(0,1fr)}}td:last-child{{text-align:left}}}}</style></head><body><main><img class=\"wordmark\" src=\"data:image/png;base64,{logo}\" alt=\"BACKBOND / AGENT RISK RATINGS &amp; INSURANCE\"><div class=\"eyebrow\">Signed verification envelope</div><h1>{escape(certificate['certificate_title'])}</h1><p>{escape(display_lead)}</p><p class=\"seal\">✓ Signature verified</p><section>{row_html}</section><table>{area_html}</table><p class=\"notice\">{escape(certificate['disclaimer'])}</p></main></body></html>"""


def _demo_boundary_block() -> str:
    return (
        '<div class="caveat-block">'
        "<h3>Demonstration boundary</h3>"
        f"<p>{escape(DEMO_BOUNDARY_NOTICE)}</p>"
        "</div>"
    )


def _render_not_available_report(summary: dict[str, Any]) -> str:
    reason = summary["rating_reason"]
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>BackBond Scanner Report - N/A</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f0e9;color:#0b1b31;font-family:Inter,system-ui,sans-serif;overflow-wrap:anywhere}}main{{width:min(900px,calc(100% - 32px));margin:32px auto;background:white;border:1px solid #d7c7b1;padding:42px}}h1{{font-family:Archivo,Inter,sans-serif}}.rating{{border-left:3px solid #a6602b;padding:18px;margin:24px 0}}.label{{font-weight:800}}p{{line-height:1.55}}@media(max-width:650px){{main{{padding:24px}}}}</style>
</head><body><main><div class=\"report-brand\">BackBond Scanner</div><h1>BackBond Scanner Report</h1>
{_demo_boundary_block()}
<div class=\"rating\"><div class=\"label\">BackBond Score N/A - Not available</div><p>{escape(reason)}</p></div>
<p>This report does not issue a band, pillar rating, or scenario estimate because measured coverage is incomplete.</p>
</main></body></html>"""


def _public_admittance_projection(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "admittance_class": decision["admittance_class"],
        "firing_rule_id": decision["firing_rule_id"],
        "scope": decision["scope"],
        "rationale": "Internal synthetic gate completed; no customer rating was issued.",
    }


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


def run_preproduction(
    output_dir: Path,
    run_id: str,
    emit: Emit,
    run_tests: Callable[[], dict[str, Any]],
    *,
    scenario: str = "happy",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if scenario not in {"happy", "bind_reject", "cicd_regression"}:
        raise ValueError(f"unsupported demo scenario: {scenario}")
    failure_analysis = SCENARIO_FAILURE_ANALYSES.get(scenario)
    output_dir.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC).replace(microsecond=0)

    emit("stage", "Initial full scanner run started", stage="scan", state="running")
    initial_input = output_dir / "scanner-initial"
    _prepare_inputs(
        initial_input,
        INITIAL_RELEASE,
        improved=False,
        forced_raw=1.5 if scenario == "bind_reject" else None,
        evidence_uri="demo://initial-scan/hardcoded-secret-and-disabled-safeguards",
    )
    initial_scan = _scan(initial_input, scored_at=now)
    initial_scan_id = f"scan-{run_id}-v1"
    initial_summary = _scan_summary(initial_scan, scan_id=initial_scan_id, release=INITIAL_RELEASE)
    _write_json(output_dir / "initial-scan.json", initial_scan)
    _emit_scan(emit, initial_scan, initial_summary, stage="scan")
    emit("stage", "Initial scan completed with complete evidence", stage="scan", state="passed")

    emit("stage", "Underwriting admittance and bind started", stage="bind", state="running")
    initial_decision = evaluate_admittance(_with_applicability(initial_scan), system_prompt_kind="safety_forward").to_dict()
    if scenario == "bind_reject":
        public_initial_decision = _public_admittance_projection(initial_decision)
        rejection = {
            "bind_id": f"demo-reject-{run_id}",
            "scan_id": initial_scan_id,
            "status": "REJECTED",
            "admittance_class": initial_decision["admittance_class"],
            "firing_rule_id": initial_decision["firing_rule_id"],
            "scope": initial_decision["scope"],
            "real_policy_bound": False,
            "disclaimer": "Synthetic rejection scenario only. No insurance transaction occurred.",
        }
        _write_json(output_dir / "bind-record.json", {"decision": initial_decision, "bind": rejection})
        for finding in failure_analysis["findings"]:
            emit(
                "scenario_finding",
                f"{finding['severity'].upper()} - {finding['title']}",
                stage="bind",
                finding=finding,
            )
        emit("bind", "Synthetic gate rejected the agent", stage="bind", bind=rejection, decision=public_initial_decision)
        emit("stage", "Admittance rejected; downstream lifecycle stopped", stage="bind", state="failed")
        return {
            "scenario": scenario,
            "failure_analysis": failure_analysis,
            "initial_scan": initial_summary,
            "updated_scan": None,
            "score_delta": None,
            "initial_admittance": public_initial_decision,
            "updated_admittance": None,
            "bind": rejection,
            "installation": None,
            "ci_gate": None,
            "certificate": None,
            "documents": {},
            "terminal_failure": {
                "stage": "bind",
                "code": "bind_rejected",
                "message": f"Initial assessment triggered {initial_decision['admittance_class']}.",
            },
        }, {"status": "not_run", "summary": "CI tests were not reached", "elapsed_ms": 0}

    bind = _bind_record(run_id, initial_scan_id, initial_decision)
    _write_json(output_dir / "bind-record.json", {"decision": initial_decision, "bind": bind})
    public_initial_decision = _public_admittance_projection(initial_decision)
    emit("bind", f"{bind['admittance_class']} · demonstration bind recorded", stage="bind", bind=bind, decision=public_initial_decision)
    emit("stage", "Constrained demonstration bind completed", stage="bind", state="passed")

    emit("stage", "Scanner, CI gate, collector, passive monitor, and Log Tracker installing", stage="install", state="running")
    installation = _installation_manifest(run_id, bind)
    _write_json(output_dir / "installation-manifest.json", installation)
    emit("install", "Four runtime components installed", stage="install", installation=installation)
    emit("stage", "Installation manifest verified", stage="install", state="passed")

    updated_release = REGRESSION_RELEASE if scenario == "cicd_regression" else UPDATED_RELEASE
    emit("stage", f"Agent release {updated_release} detected; CI/CD checks started", stage="cicd", state="running")
    updated_input = output_dir / "scanner-updated"
    _prepare_inputs(
        updated_input,
        updated_release,
        improved=scenario != "cicd_regression",
        forced_raw=1.8 if scenario == "cicd_regression" else None,
        evidence_uri="demo://release-1.2.0/severe-control-regression",
    )
    updated_scan = _scan(updated_input, scored_at=now + timedelta(minutes=1))
    updated_scan_id = f"scan-{run_id}-v2"
    updated_summary = _scan_summary(updated_scan, scan_id=updated_scan_id, release=updated_release)
    _write_json(output_dir / "updated-scan.json", updated_scan)
    emit(
        "version",
        f"Release {updated_release} scanned; rating unavailable pending complete measured coverage",
        stage="cicd",
        previous=initial_summary,
        current=updated_summary,
    )
    for panel in updated_summary["panels"]:
        emit("dimension", panel["name"], stage="cicd", panel=panel, scan_id=updated_summary["scan_id"])
    if failure_analysis:
        for finding in failure_analysis["findings"]:
            emit(
                "scenario_finding",
                f"{finding['severity'].upper()} - {finding['title']}",
                stage="cicd",
                finding=finding,
            )

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
        "release": updated_release,
    }
    if gate.exit_code != 0:
        public_gate = {"status": "failed", "release": updated_release}
        emit("cicd", "CI gate blocked the release after a control regression", stage="cicd", gate=public_gate)
        emit("stage", "New agent release failed the score gate", stage="cicd", state="failed")
        return {
            "scenario": scenario,
            "failure_analysis": failure_analysis,
            "initial_scan": initial_summary,
            "updated_scan": updated_summary,
            "score_delta": None,
            "initial_admittance": public_initial_decision,
            "updated_admittance": None,
            "bind": bind,
            "installation": installation,
            "ci_gate": public_gate,
            "certificate": None,
            "documents": {},
            "terminal_failure": {
                "stage": "cicd",
                "code": "release_gate_blocked",
                "message": f"Release {updated_release} failed the internal release gate.",
            },
        }, tests

    emit("cicd", f"CI gate passed - score {updated_scan['are_total']} >= {int(GATE_THRESHOLD)}", stage="cicd", gate=gate_payload)
    emit("stage", "New agent release passed scan and test gates", stage="cicd", state="passed")

    updated_decision = evaluate_admittance(_with_applicability(updated_scan), system_prompt_kind="safety_forward").to_dict()
    public_updated_decision = _public_admittance_projection(updated_decision)
    certificate = _certificate(run_id, updated_scan, updated_summary, updated_scan_id, updated_release)
    if not verify_certificate(certificate):
        raise RuntimeError("certificate signature verification failed")
    _write_json(output_dir / "certificate.json", certificate)
    (output_dir / "certificate.html").write_text(render_certificate_html(certificate), encoding="utf-8")
    emit("stage", "Signed Certificate of BackBond Scan issued", stage="certificate", state="running")
    emit("certificate", f"Point-in-time certificate issued at {certificate['issued_at']}", stage="certificate", certificate=certificate)
    emit("stage", "Certificate signature verified and ready to view", stage="certificate", state="passed")

    lifecycle = {
        "scenario": scenario,
        "initial_scan": initial_summary,
        "updated_scan": updated_summary,
        "initial_admittance": public_initial_decision,
        "updated_admittance": public_updated_decision,
        "bind": bind,
        "installation": installation,
        "ci_gate": {"status": gate_payload["status"], "release": UPDATED_RELEASE},
        "certificate": certificate,
        "documents": {
            "certificate_url": f"/api/runs/{run_id}/certificate",
            "report_url": f"/api/runs/{run_id}/report",
        },
        "_updated_scan": updated_scan,
    }
    _write_json(output_dir / "internal-diagnostics.json", {"synthetic_ci_gate": gate_payload})
    return lifecycle, tests

def generate_final_report(output_dir: Path, lifecycle: dict[str, Any], passive: dict[str, Any], emit: Emit) -> dict[str, Any]:
    emit("stage", "BackBond report generation started", stage="report", state="running")
    summary = lifecycle["updated_scan"]
    html = _render_not_available_report(summary) if summary["rating_status"] == "not_available" else _canonicalize_demo_report(
        render_report_card(
            lifecycle["_updated_scan"],
            Rubric.load(RUBRIC_PATH),
            provenance="measured — isolated synthetic lifecycle demonstration",
            admittance_decision=lifecycle["updated_admittance"],
        )
    )
    (output_dir / "backbond-report.html").write_text(html, encoding="utf-8")
    report = {
        "report_id": f"report-{lifecycle['certificate']['envelope_id']}",
        "generated_at": _utc_now(),
        "agent_release": lifecycle["updated_scan"]["agent_release"],
        "backbond_score": summary["backbond_score"],
        "rating_status": summary["rating_status"],
        "rating_reason": summary["rating_reason"],
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
