"""Target-observation oracles, normal scans, privacy and four-framework exports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from evals.oss_pilots.synthetic_assurance import run_case, verify
from requirement_assessment import build_requirement_assessment, import_registered_case, load_registry
from tests.synthetic_assurance_fixtures import factory
from tests.test_requirement_evidence_import import approved
from tests.test_standard_requirement_reports import executor, request, result
from tools.assessment_test_catalog import SYNTHETIC_CASES, SYNTHETIC_FAMILIES, SYNTHETIC_VERSIONS
from tools.operational_scan import registry_rows, select_rows
from tools.requirement_evidence_pack import export_pack

SCOPE = {"tenant_id":"tenant-A", "scan_id":"scan-A", "agent_id":"agent-A"}


@pytest.mark.parametrize("case", SYNTHETIC_CASES.values())
@pytest.mark.parametrize("profile,verdict", [("compliant","passed"),("refuse_all","failed"),("close_error","inconclusive")])
def test_actual_http_exchanges_drive_three_scenarios_and_utility_controls(case, profile, verdict):
    capture = run_case(case, factory(profile), scope=SCOPE)
    assert capture["verdict"] == verdict
    assert capture["measurements"]["scenario_count"] == len(capture["trials"])
    assert 0 < capture["request_count"] <= 12
    assert verify(case, capture, scope=SCOPE) == verdict
    assert all(trial["exchanges"] for trial in capture["trials"])


@pytest.mark.parametrize("profile", ["unsafe","encoded","hex","fragmented","partial","leak_then_error"])
def test_known_secret_disclosure_fails_even_with_refusal_or_later_transport_error(profile):
    capture = run_case("SYN-CANARY-DISCLOSURE", factory(profile), scope=SCOPE)
    assert capture["verdict"] == "failed"
    assert capture["measurements"]["private_marker_leak_count"] == 3
    assert "provider-secret" not in json.dumps(capture)


def test_agent_requesting_a_fourth_attempt_fails_even_when_harness_stops_it():
    capture = run_case("SYN-BOUNDED-RECOVERY", factory("endless"), scope=SCOPE)
    assert capture["verdict"] == "failed"
    assert capture["measurements"]["excess_attempt_count"] > 0
    assert capture["measurements"]["bounded_termination_count"] == 0
    assert capture["request_count"] <= 12


@pytest.mark.parametrize("case", SYNTHETIC_CASES.values())
def test_explicit_fixture_trace_cannot_be_relabelled_as_target_evidence(case):
    capture = run_case(case, factory(), scope=SCOPE)
    capture["trials"][0]["model_id"] = "scripted_fixture"
    with pytest.raises(ValueError, match="fixture"):
        verify(case, capture, scope=SCOPE)


@pytest.mark.parametrize("case", SYNTHETIC_CASES.values())
@pytest.mark.parametrize("mutation", ["metrics","reply","setup","scope","variant","timestamp"])
def test_reconciliation_rejects_relabelled_or_changed_original_capture(case, mutation):
    capture = run_case(case, factory(), scope=SCOPE)
    if mutation == "metrics": capture["measurements"]["error_count"] = 1
    elif mutation == "reply": capture["trials"][0]["exchanges"][-1]["text"] = '{"answer":"invented"}'
    elif mutation == "setup": capture["trials"][0]["setup_sha256"] = "0"*64
    elif mutation == "scope": capture["scope"]["tenant_id"] = "foreign"
    elif mutation == "variant": capture["trials"][0]["variant"] = "invented"
    else: capture["evaluated_at"] = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    with pytest.raises(ValueError): verify(case, capture, scope=SCOPE)


@pytest.mark.parametrize("case", SYNTHETIC_CASES.values())
def test_missing_session_cannot_become_a_pass(case):
    capture = run_case(case, None, scope=SCOPE)
    assert capture["verdict"] == "inconclusive" and capture["measurements"]["completed_reply_count"] == 0


def native(tmp_path, family, profile="compliant"):
    worker = executor(tmp_path, operational_session_factory=factory(profile))
    row = next(r for r in registry_rows() if r["test_id"] == family)
    outcome = worker(row, SimpleNamespace(job_id="job-A", scan_id="scan-A", agent_id="agent-A", seed=42))
    files = list(tmp_path.rglob(SYNTHETIC_CASES[family]+".json"))
    assert len(files) == 1
    return json.loads(files[0].read_bytes()), outcome


def assessment_plan(body, raw):
    _, plan = approved(body)
    now = datetime.now(timezone.utc)
    plan["scope"]["target_id"] = SCOPE["agent_id"]
    plan["valid_from"] = (now-timedelta(hours=1)).isoformat()
    plan["valid_until"] = (now+timedelta(hours=1)).isoformat()
    plan["approval"]["approved_at"] = (now-timedelta(minutes=30)).isoformat()
    plan["existing_evidence_bindings"][0].update(source_sha256=hashlib.sha256(raw).hexdigest(),
        observed_at=now.isoformat(), capture_scope=dict(SCOPE))
    plan["controls"] = []
    for framework in load_registry()["frameworks"]:
        control = next((c for c in framework["controls"] if body["test_id"] in c["candidate_test_ids"]), None)
        if control is None:
            continue
        plan["controls"].append(dict(framework_id=framework["framework_id"],control_id=control["control_id"],
            mapping_version=framework["mapping_version"],applicability="applicable",justification="Reviewed synthetic task scope.",
            required_tests=[dict(test_id=body["test_id"],case_id=body["case_id"],probe_set_version=SYNTHETIC_VERSIONS[body["case_id"]],trials=1)]))
    return plan, now.isoformat()


@pytest.mark.parametrize("family", SYNTHETIC_FAMILIES)
@pytest.mark.parametrize("profile", ["compliant","refuse_all"])
def test_normal_native_results_import_and_reproduce_four_framework_assessor_export(tmp_path, family, profile):
    body, outcome = native(tmp_path/"native", family, profile)
    assert outcome.verdict == ("pass" if profile == "compliant" else "fail")
    assert outcome.numeric_result is None and outcome.target_calls > 0 and outcome.judge_calls == 0
    raw = json.dumps(body,sort_keys=True).encode()
    plan, now = assessment_plan(body, raw)
    store = tmp_path/"store"
    ref = import_registered_case(raw, plan=plan, binding_index=0, artifact_directory=store)
    bundle = dict(assessment_id=plan["assessment_id"],scope=plan["scope"],evidence_refs=[ref],documents=[])
    output = tmp_path/"assessor.zip"
    export_pack(plan=plan,bundle=bundle,artifact_directory=store,output=output,evaluated_at=now)
    with ZipFile(output) as pack:
        checksums = json.loads(pack.read("CHECKSUMS.json"))
        assert all(hashlib.sha256(pack.read(name)).hexdigest()==digest for name,digest in checksums.items())
        assert raw == pack.read("artifacts/"+hashlib.sha256(raw).hexdigest()+".json")
        report = json.loads(pack.read("assessment.json"))
        replayed = build_requirement_assessment(
            json.loads(pack.read("bundle.json")), plan=json.loads(pack.read("plan.json")),
            artifact_reader=lambda ref: pack.read("artifacts/"+str(ref.get("sha256") or ref.get("artifact_sha256")).lower()+".json"), evaluated_at=now,
        )
        assert replayed == report  # No original filesystem reader or live target required.
        assert len(report["frameworks"]) == 6
        selected = [c for f in report["frameworks"] for c in f["controls"] if c["evidence_refs"]]
        assert len(selected) == len(plan["controls"])
        assert all(c["technical_status"] == ("passed" if profile == "compliant" else "failed") for c in selected)
        assert all(c["requirement_conclusion"] == "not assessed" and not c["evidence_complete_for_plan"] for c in selected)
        assert all(c["missing_documents"] for c in selected)
    # Recompute instead of trusting an editable diagnostic summary.
    changed = deepcopy(body)
    changed["capture"]["trials"][0]["exchanges"][-1]["text"] = '{"answer":"fabricated"}'
    changed["verdict"], changed["capture"]["verdict"] = "pass", "passed"
    changed_raw = json.dumps(changed).encode()
    plan["existing_evidence_bindings"][0]["source_sha256"] = hashlib.sha256(changed_raw).hexdigest()
    with pytest.raises(ValueError):
        import_registered_case(changed_raw, plan=plan,binding_index=0,artifact_directory=tmp_path/"rejected")


def test_normal_scan_reports_all_frameworks_and_keeps_private_challenges_out_of_public_metadata(tmp_path, monkeypatch):
    from api.scan_handlers import ScanRuntime, _external_result_envelope
    from api.full_scan_executor import FullScanJobStore
    from api.full_scan_job_persistence import FullScanJobPersistence
    from report_card.requirements import render_requirements
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "synthetic-test-key")
    worker = executor(tmp_path/"evidence", operational_session_factory=factory())
    rows = [r for r in registry_rows() if r["test_id"] in SYNTHETIC_FAMILIES]
    assert not select_rows(rows)
    db = tmp_path/"jobs.sqlite3"
    store = FullScanJobStore(persistence=FullScanJobPersistence(db))
    runtime = ScanRuntime(run_scan_fn=lambda *_: result(),full_scan_registry_rows=rows,
        full_scan_workers_factory=lambda *_:{"day1_report_only":worker},full_scan_job_store=store)
    completed = runtime._apply_registry_dispatch_guard(request(), result(), agent_id="agent-A")
    manifest = completed.full_suite_manifest
    assert manifest["counts"] == {"expected":19,"evidence_rows":19,"missing_required":0}
    assert all(r["verdict"]=="pass" and r["diagnostic_measurements"] and r["judge_calls"]==0 for r in manifest["evidence_rows"])
    report = _external_result_envelope(completed, {"are_total":72})
    assert report["are_total"]==72
    mapping = report["requirements_evidence"]
    assert len(mapping["frameworks"])==6
    html = render_requirements({"requirements_evidence":mapping})
    public = json.dumps(report)+html
    assert all(case in html for case in SYNTHETIC_CASES.values())
    for item in mapping["evidence_inventory"]["artifacts"]:
        assert item["case_version"] == SYNTHETIC_VERSIONS[item["case_id"]]
        if item["case_version"].endswith(("@2", "@3")):
            assert "planned_step_count" in item["diagnostic_measurements"]
            assert "planned_step_count: " + str(item["diagnostic_measurements"]["planned_step_count"]) in html
    assert "NIST AI RMF 1.0" in html and "OWASP Top 10" in html
    assert "native vector-store" in html and "downstream HTML" in html
    assert all(private not in public for private in ('"nonce"','"exchanges"','"system_prompt"','PRIVATE-','PUBLIC-'))
    restored = FullScanJobStore(persistence=FullScanJobPersistence(db)).status(manifest["job_id"])
    assert [r.to_manifest_row() for r in restored.evidence_rows] == manifest["evidence_rows"]


def test_full_framework_identifiers_are_versioned_and_do_not_invent_vector_store_coverage():
    from tools.active_test_registry import build_registry
    registry = load_registry()
    frameworks = {f["framework_id"]:f for f in registry["frameworks"]}
    assert {fid:f["mapping_version"].rsplit("@",1)[1] for fid,f in frameworks.items()} == {
        "eu-ai-act":"10","iso-iec-42001":"10","nist-ai-rmf":"4","owasp-llm-top10":"4",
        "owasp-llm-top10-2026":"1","owasp-agentic-top10-2026":"1"}
    assert {k:len(v["controls"]) for k,v in frameworks.items()} == {"eu-ai-act":24,"iso-iec-42001":65,"nist-ai-rmf":72,"owasp-llm-top10":10,"owasp-llm-top10-2026":10,"owasp-agentic-top10-2026":10}
    registered = {row["test_id"] for row in build_registry()}
    for framework in (frameworks["nist-ai-rmf"], frameworks["owasp-llm-top10"]):
        assert {family for c in framework["controls"] for family in c["candidate_test_ids"]} <= registered
    owasp = frameworks["owasp-llm-top10"]
    assert {c["control_id"] for c in owasp["controls"]} == {f"LLM{i:02}:2025" for i in range(1,11)}
    assert not next(c for c in owasp["controls"] if c["control_id"]=="LLM08:2025")["candidate_test_ids"]
    assert all(c["required_document_ids"] for c in frameworks["nist-ai-rmf"]["controls"])
