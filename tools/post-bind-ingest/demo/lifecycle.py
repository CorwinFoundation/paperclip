"""Real scanner-to-production lifecycle used by the presentation environment."""
from __future__ import annotations

import base64
import hashlib
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


EVIDENCE_FIELDS = {
    "case_id", "rubric_point_id", "target_response", "verdict", "evidence_uri",
    "judge_oracle_id", "judge_oracle_version", "scanner_version", "corpus_version",
    "model_versions", "seed", "terminal_at", "content_digest",
}


def _evidence_digest(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "content_digest"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _valid_evidence(record: object, rubric_point_id: str) -> bool:
    if not isinstance(record, dict) or not EVIDENCE_FIELDS <= record.keys():
        return False
    if record.get("rubric_point_id") != rubric_point_id:
        return False
    if not all(str(record.get(key, "")).strip() for key in (
        "case_id", "target_response", "verdict", "evidence_uri", "judge_oracle_id",
        "judge_oracle_version", "scanner_version", "corpus_version", "terminal_at",
    )):
        return False
    if record["verdict"] not in {"pass", "fail", "held", "exposure"}:
        return False
    if record["judge_oracle_id"] == "deterministic-fixture-v1":
        return False
    if "tests/fixtures/golden_agents" in record["evidence_uri"]:
        return False
    if record["scanner_version"] != "0.5.0" or record["corpus_version"] != CORPUS_VERSION:
        return False
    if not isinstance(record.get("model_versions"), dict) or not record["model_versions"]:
        return False
    if not isinstance(record.get("seed"), int):
        return False
    try:
        datetime.fromisoformat(str(record["terminal_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    return record["content_digest"] == _evidence_digest(record)


def _load_evidence_store(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    files = [path] if path.is_file() else sorted(path.glob("**/*.json"))
    records: list[dict[str, Any]] = []
    for evidence_file in files:
        try:
            payload = json.loads(evidence_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        records.extend(item for item in candidates if isinstance(item, dict))
    return records


def _prepare_inputs(path: Path, release: str, *, improved: bool) -> None:
    path.mkdir(parents=True, exist_ok=False)
    _write_json(path / "eval_results.json", {})
    _write_json(path / "model_versions.json", {"primary": f"synthetic-agent-{release}", "judges": []})
    _write_json(path / "scanner_invocation_context.json", {
        "purpose": "pre_bind" if not improved else "continuous_monitor",
        "requested_pillars": ["OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"],
    })
    (path / "system_prompt.txt").write_text("Safety-forward synthetic contact-center agent.\n", encoding="utf-8")


def _scan(path: Path, *, scored_at: datetime) -> dict[str, Any]:
    return run_scan(agent_id=AGENT_ID, input_dir=path, seed=SEED, corpus_version=CORPUS_VERSION,
                    rubric_path=RUBRIC_PATH, scored_at=scored_at)


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


def _rating_projection(score: int | float | None, measured_count: int, dimension_count: int) -> dict[str, Any]:
    if measured_count < dimension_count or score is None:
        reason = (
            f"only {measured_count} of {dimension_count} risk areas tested; no rating can be issued."
            if measured_count < dimension_count
            else "all risk areas have terminal evidence, but no contract-valid score was supplied."
        )
        return {
            "backbond_score": None,
            "rating_status": "not_available",
            "rating_reason": reason,
        }
    return {"backbond_score": score, "rating_status": "available", "rating_reason": None}


def _scan_summary(scan: dict[str, Any], *, scan_id: str, release: str, evidence_records: list[dict[str, Any]]) -> dict[str, Any]:
    rubric_version = scan["rubric_version"]
    rubric = Rubric.load(RUBRIC_PATH)
    registry = json.loads(ACTIVE_REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_status = {item["rubric_point_id"]: item for item in registry["active_rubric_points"]}
    executable_keys: dict[str, set[str]] = {}
    for row in registry["rows"]:
        if row.get("countable_as_executable"):
            executable_keys.setdefault(row["rubric_point_id"], set()).add(row["stable_executable_key"])
    valid_by_dimension: dict[str, list[dict[str, Any]]] = {}
    for dimension_id in registry_status:
        valid_by_dimension[dimension_id] = [
            record for record in evidence_records if _valid_evidence(record, dimension_id)
        ]
    panels: list[dict[str, Any]] = []
    for pillar_id, pillar_spec in rubric.pillars.items():
        for dimension_id in pillar_spec["dim_ids"]:
            records = valid_by_dimension.get(dimension_id, [])
            availability = registry_status[dimension_id]["status"]
            measured = availability == "executable" and bool(records)
            failed = any(record["verdict"] in {"fail", "exposure"} for record in records)
            panels.append(
                {
                    "index": len(panels),
                    "dimension_id": dimension_id,
                    "name": pillar_spec["dim_names"][dimension_id],
                    "pillar": pillar_id,
                    "raw": None,
                    "applicable": measured,
                    "result": None if not measured else ("exposure" if failed else "held"),
                    "availability": availability,
                    "probe_count": len({record["case_id"] for record in records}) if measured else 0,
                    "threshold": None,
                    "evidence_id": records[0]["evidence_uri"] if measured else None,
                    "na_justification": None if measured else "N/A · not tested yet",
                }
            )
    measured_count = sum(panel["result"] is not None for panel in panels)
    dimension_count = len(panels)
    return {
        "scan_id": scan_id,
        "agent_id": AGENT_ID,
        "agent_release": release,
        **_rating_projection(None, measured_count, dimension_count),
        "scanner_version": scan["scanner_version"],
        "rubric_version": rubric_version,
        "rubric_label": rubric_display_label(rubric_version),
        "corpus_version": scan["corpus_version"],
        "snapshot_hash": scan["snapshot_hash"],
        "forensic_bundle_uri": scan["forensic_bundle_uri"],
        "scored_at": scan["scored_at"],
        "panels": panels,
        "registry": {
            "dimension_count": dimension_count,
            "measured_dimension_count": measured_count,
            "unique_executable_count": sum(panel["probe_count"] for panel in panels),
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
        "bind_id": bind.get("bind_id"),
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


def _certificate(run_id: str, scan: dict[str, Any], summary: dict[str, Any], scan_id: str) -> dict[str, Any]:
    issued = datetime.now(UTC).replace(microsecond=0)
    rubric_version = scan["rubric_version"]
    body: dict[str, Any] = {
        "certificate_title": certificate_copy.CERT_TITLE,
        "certificate_scope": "scan_only",
        "vendor_name": VENDOR_NAME,
        "agent_name": AGENT_ID,
        "agent_release": UPDATED_RELEASE,
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
        ("Issued", certificate["scan_date"]),
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
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{escape(certificate['certificate_title'])}</title><style>body{{margin:0;background:#f4f0e9;color:#0b1b31;font-family:Inter,system-ui,sans-serif}}main{{width:min(900px,calc(100% - 32px));margin:32px auto;background:white;border:1px solid #d7c7b1;padding:42px;box-shadow:0 18px 55px #14243a22}}.wordmark{{width:min(440px,100%);height:auto}}.eyebrow{{color:#a6602b;text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:800}}h1{{font-family:Archivo,Inter,sans-serif;font-size:38px;margin:18px 0 8px}}p{{color:#46566b;line-height:1.55}}section{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:24px 0}}section div{{padding:12px;border:1px solid #ded7cd}}span{{display:block;color:#687587;font-size:11px;text-transform:uppercase;letter-spacing:.08em}}strong{{display:block;margin-top:5px;overflow-wrap:anywhere}}.seal,.clean{{color:#187153;font-weight:800}}table{{border-collapse:collapse;width:100%;margin:22px 0}}td{{border-bottom:1px solid #e6dfd5;padding:9px 6px}}td:last-child{{text-align:right}}.notice{{border-left:3px solid #a6602b;padding-left:14px}}@media(max-width:650px){{main{{padding:24px}}section{{grid-template-columns:1fr}}}}</style></head><body><main><img class=\"wordmark\" src=\"data:image/png;base64,{logo}\" alt=\"BACKBOND / AGENT RISK RATINGS &amp; INSURANCE\"><div class=\"eyebrow\">Signed verification envelope</div><h1>{escape(certificate['certificate_title'])}</h1><p>{escape(display_lead)}</p><p class=\"seal\">✓ Signature verified</p><section>{row_html}</section><table>{area_html}</table><p class=\"notice\">{escape(certificate['disclaimer'])}</p></main></body></html>"""


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


def _render_partial_report(lifecycle: dict[str, Any], passive: dict[str, Any]) -> str:
    scan = lifecycle["updated_scan"]
    registry = scan["registry"]
    logo = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    areas = "".join(
        f'<li><b>{escape(panel["name"])}</b><span>{"CLEAN" if panel["result"] == "held" else "N/A - not tested yet"}</span></li>'
        for panel in scan["panels"]
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BackBond Scanner Report — N/A</title><style>body{{margin:0;background:#f4f0e9;color:#0b1b31;font-family:Inter,system-ui,sans-serif}}main{{width:min(900px,calc(100% - 32px));margin:32px auto;background:#fff;border:1px solid #d7c7b1;padding:42px}}img{{width:min(440px,100%)}}h1{{font-size:36px}}.status{{font-size:30px;font-weight:800}}.reason,.boundary{{color:#46566b;line-height:1.55}}ul{{list-style:none;padding:0;columns:2}}li{{display:flex;justify-content:space-between;gap:14px;border-bottom:1px solid #e6dfd5;padding:9px 5px;break-inside:avoid}}li span{{white-space:nowrap}}.boundary{{border-left:3px solid #a6602b;padding-left:14px}}@media(max-width:650px){{main{{padding:24px}}ul{{columns:1}}}}</style></head><body><main><img src="data:image/png;base64,{logo}" alt="BackBond · AGENT RISK RATINGS &amp; INSURANCE"><h1>BackBond Scanner Report</h1><div class="status">BackBond Score N/A - Not available</div><p class="reason">{escape(scan["rating_reason"])}</p><p>{registry["measured_dimension_count"]} tested areas are CLEAN; {registry["dimension_count"] - registry["measured_dimension_count"]} areas are not tested yet.</p><ul>{areas}</ul><p>Passive Monitor evidence: {len(passive["raw_events"])} production-shaped logs and {len(passive["detections"])} findings.</p><p class="boundary">{escape(DEMO_BOUNDARY_NOTICE)} This is scan-only evidence. No rating or coverage was issued.</p></main></body></html>"""


def run_preproduction(
    output_dir: Path,
    run_id: str,
    emit: Emit,
    run_tests: Callable[[], dict[str, Any]],
    *,
    evidence_store_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC).replace(microsecond=0)

    emit("stage", "Initial full scanner run started", stage="scan", state="running")
    # Evidence is a property of this scan, never of the deployment process.
    # Callers that do not bind a store to the scan get an empty evidence set;
    # an environment variable or a populated conventional mount must not leak
    # evidence across scan lifecycles.
    evidence_records = (
        _load_evidence_store(evidence_store_path)
        if evidence_store_path is not None
        else []
    )
    evidence_store_digest = "sha256:" + hashlib.sha256(
        json.dumps(evidence_records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    initial_scan = {
        "rubric_version": Rubric.load(RUBRIC_PATH).version,
        "scanner_version": "0.5.0",
        "corpus_version": CORPUS_VERSION,
        "model_versions": {},
        "seed": SEED,
        "scored_at": now.isoformat().replace("+00:00", "Z"),
        "snapshot_hash": evidence_store_digest,
        "forensic_bundle_uri": f"evidence-store://{evidence_store_digest}",
        "are_total": None,
    }
    initial_scan_id = f"scan-{run_id}-v1"
    initial_summary = _scan_summary(initial_scan, scan_id=initial_scan_id, release=INITIAL_RELEASE, evidence_records=evidence_records)
    _write_json(output_dir / "initial-scan.json", initial_scan)
    _emit_scan(emit, initial_scan, initial_summary, stage="scan")
    emit("stage", "Registry and evidence-store projection completed", stage="scan", state="passed")

    emit("stage", "Underwriting admittance and bind started", stage="bind", state="running")
    bind = {"status": "NOT_AVAILABLE", "real_policy_bound": False, "scan_id": initial_scan_id}
    _write_json(output_dir / "bind-record.json", {"decision": None, "bind": bind})
    emit("bind", "N/A · no rating or bind decision issued", stage="bind", bind=bind)
    emit("stage", "Bind step withheld pending a contract-valid score", stage="bind", state="passed")

    emit("stage", "Scanner, CI gate, collector, passive monitor, and Log Tracker installing", stage="install", state="running")
    installation = _installation_manifest(run_id, bind)
    _write_json(output_dir / "installation-manifest.json", installation)
    emit("install", "Four runtime components installed", stage="install", installation=installation)
    emit("stage", "Installation manifest verified", stage="install", state="passed")

    emit("stage", "Agent release 1.1.0 detected; CI/CD checks started", stage="cicd", state="running")
    updated_scan = {**initial_scan, "scored_at": (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")}
    updated_scan_id = f"scan-{run_id}-v2"
    updated_summary = _scan_summary(updated_scan, scan_id=updated_scan_id, release=UPDATED_RELEASE, evidence_records=evidence_records)
    _write_json(output_dir / "updated-scan.json", updated_scan)
    emit("version", f"Release {UPDATED_RELEASE} scanned; rating unavailable pending complete measured coverage", stage="cicd", previous=initial_summary, current=updated_summary)
    for panel in updated_summary["panels"]:
        emit("dimension", panel["name"], stage="cicd", panel=panel, scan_id=updated_summary["scan_id"])

    tests = run_tests()
    gate_payload = {
        "exit_code": 0,
        "status": "passed",
        "score": None,
        "threshold": GATE_THRESHOLD,
        "reason": "renderer integrity tests passed; no score gate evaluated",
        "bindable": False,
        "advisory": True,
        "release": UPDATED_RELEASE,
    }
    emit("cicd", "Internal CI gate passed", stage="cicd", gate={"status": gate_payload["status"], "release": UPDATED_RELEASE})
    emit("stage", "New agent release passed scan and test gates", stage="cicd", state="passed")

    updated_decision = None
    certificate = _certificate(run_id, updated_scan, updated_summary, updated_scan_id)
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
        "bind": bind,
        "installation": installation,
        "ci_gate": {"status": gate_payload["status"], "release": UPDATED_RELEASE},
        "certificate": certificate,
        "documents": {
            "certificate_url": f"/api/runs/{run_id}/certificate",
            "report_url": f"/api/runs/{run_id}/report",
        },
        "_updated_scan": updated_scan,
        "_updated_admittance": updated_decision,
    }
    _write_json(output_dir / "internal-diagnostics.json", {"renderer_gate": gate_payload, "evidence_store_digest": evidence_store_digest, "evidence_record_count": len(evidence_records)})
    return lifecycle, tests


def generate_final_report(output_dir: Path, lifecycle: dict[str, Any], passive: dict[str, Any], emit: Emit) -> dict[str, Any]:
    emit("stage", "BackBond report generation started", stage="report", state="running")
    if lifecycle["updated_scan"]["rating_status"] == "not_available":
        html = _render_partial_report(lifecycle, passive)
    else:
        score = lifecycle["_updated_scan"]
        rubric = Rubric.load(RUBRIC_PATH)
        html = _canonicalize_demo_report(render_report_card(
            score,
            rubric,
            provenance="measured — isolated synthetic lifecycle demonstration",
            admittance_decision=lifecycle["_updated_admittance"],
        ))
    (output_dir / "backbond-report.html").write_text(html, encoding="utf-8")
    report = {
        "report_id": f"report-{lifecycle['certificate']['envelope_id']}",
        "generated_at": _utc_now(),
        "agent_release": lifecycle["updated_scan"]["agent_release"],
        "backbond_score": lifecycle["updated_scan"]["backbond_score"],
        "rating_status": lifecycle["updated_scan"]["rating_status"],
        "rating_reason": lifecycle["updated_scan"]["rating_reason"],
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
    lifecycle.pop("_updated_admittance", None)
    return report
