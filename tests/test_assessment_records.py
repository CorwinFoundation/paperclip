"""Record reconciliation, privacy, real dispatch, native reuse and assessor ZIPs."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from evals.oss_pilots import assessment_records as records, log_challenges
from harness.runner import MockRunner
from scripts.run_operational_controls import FixtureLogCapture
from tools.assessment_test_catalog import RECORD_CASES, RECORD_FAMILIES, LOG_CHALLENGE_FAMILY, VALIDATION_METHODS, LEGACY_RECORD_CASES
from tools.operational_scan import registry_rows
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget
from requirement_assessment import import_registered_case, load_registry
from tests.test_requirement_evidence_import import approved, source
from tools.requirement_evidence_pack import export_pack

SCOPE = {"tenant_id": "tenant-A", "scan_id": "scan-A", "agent_id": "agent-A"}
AT = "2026-10-27T20:00:00+00:00"


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()


def blob(value):
    raw = encode(value)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "content_base64": base64.b64encode(raw).decode()}


def sample(case, scope=SCOPE):
    if case in VALIDATION_METHODS:
        from tests.assessment_validation_fixtures import sample as validation_sample
        return validation_sample(case, scope)
    artifacts = {"proof": blob({"reviewed_record": "private-customer-detail"})}
    if case == "EVID-RISK-TRACE":
        data = {"risks": [{"id": "r", "owner": "risk-owner", "status": "closed", "assessment_refs": ["proof"], "treatment_ids": ["t"]}],
                "treatments": [{"id": "t", "owner": "control-owner", "risk_id": "r", "status": "verified",
                    "verification_refs": ["proof"], "verification_outcome": "effective", "verified_at": AT}]}
    elif case == "EVID-DATA-LINEAGE":
        artifacts["dataset"] = blob({"rows": [{"id": "a", "label": "yes"}, {"id": "b", "label": "no"}]})
        data = {"datasets": [{"id": "dataset-v1", "version": "1", "source": "approved-source", "purpose": "evaluation",
            "permission_refs": ["proof"], "parent_ids": [], "artifact_ref": "dataset",
            "quality_rules": {"fields": {"id": "string", "label": "string"}, "unique_id": "id"}}]}
    elif case == "EVID-INCIDENT-CLOSURE":
        data = {"incidents": [{"id": "i", "owner": "incident-owner", "status": "closed", "action_ids": ["a"],
                              "opened_at": "2026-10-26T00:00:00Z", "closed_at": AT}],
                "actions": [{"id": "a", "owner": "action-owner", "incident_id": "i", "status": "verified",
                    "verification_outcome": "effective", "verified_at": AT, "verification_refs": ["proof"]}]}
    else:
        artifacts["dataset"] = blob({"rows": [{"id": "a", "expected": "yes", "group": "one"},
                                               {"id": "b", "expected": "no", "group": "two"}]})
        evaluation = {"target_id": scope.get("agent_id", scope.get("target_id")), "model_version": "model-v2",
            "dataset_sha256": artifacts["dataset"]["sha256"], "probe_version": "probe@1", "observed_at": AT,
            "declared_accuracy_ppm": 1_000_000, "results": [{"id": "a", "actual": "yes"}, {"id": "b", "actual": "no"}]}
        artifacts["current"] = blob(evaluation)
        artifacts["baseline"] = blob(evaluation | {"observed_at": "2026-10-26T00:00:00Z", "model_version": "model-v1"})
        data = {"dataset_ref": "dataset", "current_ref": "current", "baseline_ref": "baseline", "probe_version": "probe@1",
            "current_model_version": "model-v2", "baseline_model_version": "model-v1",
            "policy": {"minimum_accuracy_ppm": 900_000, "required_groups": ["one", "two"], "minimum_group_size": 1,
                       "maximum_gap_ppm": 100_000, "maximum_drop_ppm": 100_000}}
    return {"schema": records.SCHEMA, "scope": scope, "source_id": "reviewed-source-1", "recorded_at": AT,
            "review": {"reviewer": "assessor", "basis": "approved scoped source, quality rules and thresholds", "reviewed_at": AT},
            "artifacts": artifacts, "data": data}


class RecordAdapter:
    def __init__(self, document): self.document = document
    def read(self, case): return encode(self.document(case) if callable(self.document) else self.document)


def change_artifact(doc, key, mutate):
    value = json.loads(base64.b64decode(doc["artifacts"][key]["content_base64"]))
    mutate(value)
    doc["artifacts"][key] = blob(value)


@pytest.mark.parametrize("case", records.CASE_METHODS)
def test_original_bytes_reconcile_and_tampered_metrics_are_rejected(case):
    original = encode(sample(case))
    capture = records.run_case(case, SimpleNamespace(read=lambda _: original), scope=SCOPE)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "passed"
    assert base64.b64decode(capture["input_document"]["content_base64"]) == original
    capture["measurements"]["error_count"] += 1
    with pytest.raises(ValueError): records.verify(case, capture, scope=SCOPE)


@pytest.mark.parametrize("case", LEGACY_RECORD_CASES.values())
def test_contradictory_records_fail_even_when_source_hashes_are_correct(case):
    doc = sample(case)
    if case == "EVID-RISK-TRACE": doc["data"]["treatments"][0]["risk_id"] = "missing"
    elif case == "EVID-DATA-LINEAGE": doc["data"]["datasets"][0]["parent_ids"] = ["dataset-v1"]
    elif case == "EVID-INCIDENT-CLOSURE": doc["data"]["actions"][0]["verified_at"] = "2026-10-29T00:00:00Z"
    else: change_artifact(doc, "current", lambda value: value.update(declared_accuracy_ppm=0))
    capture = records.run_case(case, RecordAdapter(doc), scope=SCOPE)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "failed"
    assert capture["measurements"]["error_count"] > 0


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(scope={"tenant_id": "foreign-tenant"}),
    lambda d: d.pop("review"), lambda d: d.update(fixture=True),
    lambda d: d["review"].update(reviewed_at="2099-01-01T00:00:00Z"),
    lambda d: d.update(recorded_at="2026-10-28T00:00:00"),
])
def test_unreviewed_fixture_future_and_foreign_documents_do_not_persist_private_bytes(mutation):
    doc = sample("EVID-RISK-TRACE")
    mutation(doc)
    capture = records.run_case("EVID-RISK-TRACE", RecordAdapter(doc), scope=SCOPE)
    assert capture["verdict"] == "inconclusive" and "input_document" not in capture
    assert "foreign-tenant" not in json.dumps(capture) and "private-customer-detail" not in json.dumps(capture)


def test_empty_or_broken_adapter_and_ambiguous_json_never_pass():
    for raw in [b'{"schema":1,"schema":2}', b'{"value":NaN}', b'{"value":1e999}', b"x" * (records.MAX_BYTES + 1)]:
        assert records.run_case("EVID-RISK-TRACE", SimpleNamespace(read=lambda _, raw=raw: raw), scope=SCOPE)["verdict"] == "inconclusive"
    assert records.run_case("EVID-RISK-TRACE", None, scope=SCOPE)["verdict"] == "inconclusive"
    def broken(_): raise RuntimeError("private credential")
    assert "private credential" not in json.dumps(records.run_case("EVID-RISK-TRACE", SimpleNamespace(read=broken), scope=SCOPE))
    assert records.run_case("EVID-RISK-TRACE", RecordAdapter(sample("EVID-RISK-TRACE", {})), scope={})["verdict"] == "inconclusive"
    capture = records.run_case("EVID-RISK-TRACE", RecordAdapter(sample("EVID-RISK-TRACE")), scope=SCOPE)
    with pytest.raises(ValueError):
        records.verify("EVID-RISK-TRACE", capture, scope=SCOPE, observed_at=AT)


def test_dataset_quality_permissions_and_digest_are_checked():
    doc = sample("EVID-DATA-LINEAGE")
    change_artifact(doc, "dataset", lambda v: v["rows"].append({"id": "a", "label": False}))
    doc["data"]["datasets"][0]["permission_refs"] = ["missing"]
    capture = records.run_case("EVID-DATA-LINEAGE", RecordAdapter(doc), scope=SCOPE)
    assert capture["verdict"] == "failed"
    assert capture["measurements"]["identity_errors"] and capture["measurements"]["reference_errors"] and capture["measurements"]["policy_errors"]
    doc["artifacts"]["dataset"]["sha256"] = "0" * 64
    assert records.run_case("EVID-DATA-LINEAGE", RecordAdapter(doc), scope=SCOPE)["verdict"] == "failed"


def test_deep_lineage_is_checked_without_recursive_or_cubic_graph_traversal():
    doc = sample("EVID-DATA-LINEAGE")
    template = doc["data"]["datasets"][0]
    doc["data"]["datasets"] = [template | {"id": str(i), "parent_ids": [str(i - 1)] if i else []} for i in range(500)]
    capture = records.run_case("EVID-DATA-LINEAGE", RecordAdapter(doc), scope=SCOPE)
    assert capture["verdict"] == "passed" and capture["measurements"]["record_count"] == 500


@pytest.mark.parametrize("artifact,mutation", [
    ("dataset", lambda v: v.update(fixture=True)),
    ("current", lambda v: v.update(execution_kind="offline_fixture")),
    ("current", lambda v: v.update(dataset_sha256="0" * 64)),
    ("current", lambda v: v.update(model_version="unexpected-model-version")),
    ("current", lambda v: v["results"].append(v["results"][0])),
    ("current", lambda v: v["results"].pop()),
    ("current", lambda v: v.update(observed_at="2099-01-01T00:00:00Z")),
])
def test_label_and_observation_provenance_binding_is_required(artifact, mutation):
    doc = sample("EVID-EVAL-RESULTS")
    change_artifact(doc, artifact, mutation)
    assert records.run_case("EVID-EVAL-RESULTS", RecordAdapter(doc), scope=SCOPE)["verdict"] == "failed"


def test_foreign_embedded_target_is_not_retained_even_with_a_matching_wrapper_scope():
    for key in ("current", "baseline"):
        doc = sample("EVID-CHANGE-REGRESSION")
        change_artifact(doc, key, lambda v: v.update(target_id="foreign-target", private="foreign-private-response"))
        capture = records.run_case("EVID-CHANGE-REGRESSION", RecordAdapter(doc), scope=SCOPE)
        assert capture["verdict"] == "inconclusive" and "input_document" not in capture
        assert "foreign" not in json.dumps(capture)


def test_evaluation_requires_an_explicit_reviewed_model_version():
    doc = sample("EVID-EVAL-RESULTS")
    doc["data"].pop("current_model_version")
    assert records.run_case("EVID-EVAL-RESULTS", RecordAdapter(doc), scope=SCOPE)["verdict"] == "failed"


@pytest.mark.parametrize("case,mutate", [
    ("EVID-EVAL-RESULTS", lambda d: d["data"]["policy"].update(minimum_accuracy_ppm=1_000_000)),
    ("EVID-SUBGROUP-COVERAGE", lambda d: d["data"]["policy"].update(required_groups=["one", "absent"])),
    ("EVID-CHANGE-REGRESSION", lambda d: d["data"]["policy"].update(maximum_drop_ppm=0)),
])
def test_customer_thresholds_and_required_subgroups_apply_to_recomputed_results(case, mutate):
    doc = sample(case)
    change_artifact(doc, "current", lambda v: (v["results"][0].update(actual="wrong"), v.update(declared_accuracy_ppm=500_000)))
    mutate(doc)
    capture = records.run_case(case, RecordAdapter(doc), scope=SCOPE)
    assert capture["verdict"] == "failed" and capture["measurements"]["accuracy_ppm"] == 500_000
    assert capture["measurements"]["metric_errors"] == 0 and capture["measurements"]["policy_errors"] > 0


def test_no_closed_incidents_is_an_explicit_gap_and_open_risks_are_retained():
    doc = sample("EVID-INCIDENT-CLOSURE")
    doc["data"]["incidents"][0]["status"] = "open"
    assert records.run_case("EVID-INCIDENT-CLOSURE", RecordAdapter(doc), scope=SCOPE)["verdict"] == "inconclusive"
    doc = sample("EVID-RISK-TRACE")
    doc["data"]["risks"][0]["status"] = "accepted"
    capture = records.run_case("EVID-RISK-TRACE", RecordAdapter(doc), scope=SCOPE)
    assert capture["verdict"] == "passed" and capture["measurements"]["open_record_count"] == 1


def test_documented_risk_acceptance_does_not_require_a_fabricated_treatment():
    doc = sample("EVID-RISK-TRACE")
    doc["data"]["treatments"] = []
    doc["data"]["risks"][0].update(status="accepted", treatment_ids=[], acceptance_refs=["proof"], accepted_at=AT)
    result = records.run_case("EVID-RISK-TRACE", RecordAdapter(doc), scope=SCOPE)
    assert result["verdict"] == "passed" and result["measurements"]["open_record_count"] == 1
    doc["data"]["risks"][0].pop("acceptance_refs")
    assert records.run_case("EVID-RISK-TRACE", RecordAdapter(doc), scope=SCOPE)["verdict"] == "failed"


def test_records_only_operator_flow_uses_approved_plan_without_endpoint_or_model(tmp_path):
    from scripts.run_operational_controls import main
    from tests.test_requirement_evidence_import import plan as empty_plan
    plan = empty_plan()
    now = datetime.now(timezone.utc)
    plan.update(valid_from=(now - timedelta(minutes=5)).isoformat(), valid_until=(now + timedelta(hours=1)).isoformat())
    plan["approval"]["approved_at"] = (now - timedelta(minutes=4)).isoformat()
    eu = load_registry()["frameworks"][0]
    controls = {}
    directory = tmp_path / "inputs"
    directory.mkdir()
    for family, case in RECORD_CASES.items():
        control = next(c for c in eu["controls"] if family in c["candidate_test_ids"])
        declaration = controls.setdefault(control["control_id"], {"framework_id": "eu-ai-act", "control_id": control["control_id"],
            "mapping_version": eu["mapping_version"], "applicability": "applicable", "justification": "reviewed supplied records",
            "required_tests": []})
        declaration["required_tests"].append({"test_id": family, "case_id": case, "probe_set_version": records.case_version(case), "trials": 1})
        (directory / (case + ".json")).write_bytes(encode(sample(case, plan["scope"])))
    plan["controls"] = list(controls.values())
    path = tmp_path / "plan.json"
    path.write_bytes(encode(plan))
    output = tmp_path / "assessment"
    assert main(["--records-only", "--records-directory", str(directory), "--plan", str(path), "--output", str(output)]) == 0
    assert json.loads((output / "request-ledger.json").read_text())["reserved_requests"] == 0
    assessment = json.loads((output / "assessment.json").read_text())
    assert not assessment["rejected_evidence"]
    selected = [c for f in assessment["frameworks"] for c in f["controls"] if c["control_id"] in controls and f["framework_id"] == "eu-ai-act"]
    assert selected and all(c["technical_status"] == "passed" and c["missing_documents"] for c in selected)
    with pytest.raises(ValueError):
        from requirement_assessment import validate_plan
        plan["controls"][0]["required_tests"][0]["trials"] = 2
        validate_plan(plan, load_registry())


@pytest.mark.parametrize("case", log_challenges.CASE_IDS)
def test_diagnostic_challenges_have_positive_controls_and_independent_metrics(case):
    capture = log_challenges.run_case(case, FixtureLogCapture(), scope=SCOPE)
    assert capture["verdict"] == "passed"
    assert capture["measurements"] == log_challenges.measure(case, capture, scope=SCOPE)


def test_empty_lookup_false_matches_interleaving_mixup_and_boundary_clipping_fail():
    class Noisy(FixtureLogCapture):
        def read(self, batch): return super().read(batch) or self.events[:1]
    assert log_challenges.run_case("OPS-LOG-EMPTY-LOOKUP", Noisy(), scope=SCOPE)["verdict"] == "failed"
    class Mixed(FixtureLogCapture):
        def read(self, batch): return self.events
    assert log_challenges.run_case("OPS-LOG-INTERLEAVED", Mixed(), scope=SCOPE)["verdict"] == "failed"
    class Clipped(FixtureLogCapture):
        def read(self, batch):
            rows = deepcopy(super().read(batch))
            rows[0]["payload"]["text"] = rows[0]["payload"]["text"][:128]
            return rows
    assert log_challenges.run_case("OPS-LOG-BOUNDARY", Clipped(), scope=SCOPE)["verdict"] == "failed"


def worker(tmp_path, invalid_records=False):
    def forbidden(_): pytest.fail("model or judge called")
    def document(case, scope):
        doc = sample(case, scope)
        if invalid_records:
            doc["artifacts"]["proof"]["sha256"] = "0" * 64
        return doc
    return RegisteredCaseExecutor(subject=MockRunner("target", forbidden), judge=MockRunner("judge", forbidden),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=0, max_judge_calls=0), tenant_id="tenant-A",
        assessment_record_factory=lambda scope: RecordAdapter(lambda case: document(case, scope)),
        operational_log_capture_factory=lambda _: FixtureLogCapture())


@pytest.mark.parametrize("family,case,failed", [*[(f, c, failure) for f, c in RECORD_CASES.items() for failure in (False, True)],
                                              *[(LOG_CHALLENGE_FAMILY, c, False) for c in log_challenges.CASE_IDS]])
def test_native_zero_call_sources_import_and_export_original_bytes(tmp_path, family, case, failed):
    w = worker(tmp_path / "native", invalid_records=failed)
    row = next(r for r in registry_rows() if r["test_id"] == family and r["case_id"] == case)
    result = w(row, SimpleNamespace(job_id="job-A", scan_id="scan-A", agent_id="agent-A", seed=42))
    assert result.verdict == ("fail" if failed else "pass") and result.target_calls == result.judge_calls == 0 and result.numeric_result is None
    raw = next((tmp_path / "native").rglob("*.json")).read_bytes()
    native = json.loads(raw)
    _, plan = approved(native)
    now = datetime.now(timezone.utc)
    plan["scope"]["target_id"] = "agent-A"
    plan.update(valid_from=(now - timedelta(minutes=5)).isoformat(), valid_until=(now + timedelta(hours=1)).isoformat())
    plan["approval"]["approved_at"] = (now - timedelta(minutes=4)).isoformat()
    eu = load_registry()["frameworks"][0]
    control = next(c for c in eu["controls"] if family in c["candidate_test_ids"])
    plan["controls"][0].update(control_id=control["control_id"], mapping_version=eu["mapping_version"], required_tests=[{
        "test_id": family, "case_id": row["case_id"], "probe_set_version": row["version_pin"], "trials": 1}])
    plan["existing_evidence_bindings"][0].update(capture_scope=SCOPE, observed_at=now.isoformat(), source_sha256=hashlib.sha256(raw).hexdigest())
    root = tmp_path / "imported"
    ref = import_registered_case(raw, plan=plan, binding_index=0, artifact_directory=root)
    bundle = {"assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": [ref], "documents": []}
    output = tmp_path / "pack.zip"
    index = export_pack(plan=plan, bundle=bundle, artifact_directory=root, output=output, evaluated_at=now.isoformat())
    assert len(index["artifacts"]) == 2
    with ZipFile(output) as archive:
        assert archive.read("artifacts/" + hashlib.sha256(raw).hexdigest() + ".json") == raw
        report = json.loads(archive.read("assessment.json"))
        row = next(c for f in report["frameworks"] if f["framework_id"] == "eu-ai-act" for c in f["controls"] if c["control_id"] == control["control_id"])
        assert row["technical_status"] == ("failed" if failed else "passed") and row["requirement_conclusion"] == "not assessed"
        assert row["missing_documents"]
    # Even a newly reviewed native-file hash cannot substitute claimed metrics.
    native["capture"]["measurements"]["error_count" if family in RECORD_FAMILIES else "expected_event_count"] += 1
    tampered = encode(native)
    plan["existing_evidence_bindings"][0]["source_sha256"] = hashlib.sha256(tampered).hexdigest()
    with pytest.raises(ValueError):
        import_registered_case(tampered, plan=plan, binding_index=0, artifact_directory=tmp_path / "rejected")


def test_new_cases_complete_normal_scan_flow_reports_and_persistent_restart(tmp_path, monkeypatch):
    from api.full_scan_executor import FullScanJobStore
    from api.full_scan_job_persistence import FullScanJobPersistence
    from api.scan_handlers import ScanRuntime, _external_result_envelope
    from api.scan_log import ScanLog
    from scanners.live_probe_orchestrator import ScanRequest
    from report_card.requirements import render_requirements
    from tools.framework_workers import build_live_framework_workers
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "record-test-key")
    def forbidden(*_): pytest.fail("model or legacy walker called")
    def workers(request, secret):
        return build_live_framework_workers(request, secret, evidence_root=tmp_path / "native",
            budget=ScanBudget(max_target_calls=0, max_judge_calls=0),
            subject_factory=lambda *_: MockRunner("target", forbidden), judge_factory=lambda: MockRunner("judge", forbidden),
            assessment_record_factory=lambda scope: RecordAdapter(lambda case: sample(case, scope)),
            operational_log_capture_factory=lambda _: FixtureLogCapture())
    rows = [r for r in registry_rows() if r["test_id"] in RECORD_FAMILIES | {LOG_CHALLENGE_FAMILY}]
    db = tmp_path / "jobs.sqlite3"
    runtime = ScanRuntime(run_scan_fn=forbidden, full_scan_registry_rows=rows,
        full_scan_workers_factory=workers,
        full_scan_job_store=FullScanJobStore(persistence=FullScanJobPersistence(db)), scan_log=ScanLog(tmp_path / "scans.sqlite3"),
        score_envelope_factory=lambda *a: {"are_total": 72, "band": "B", "evidence_refs": []})
    request = ScanRequest(scan_id="scan-A", tenant_id="tenant-A", agent_id="agent-A", agent_endpoint="https://target.example",
        credential_ref="ephemeral", corpus_snapshot_hash="a" * 64, scanner_version="test", corpus_version="test", seed=42)
    done = Event()
    runtime.kickoff(request, "secret", agent_id="agent-A", on_terminal=done.set)
    assert done.wait(15)
    result = runtime.fetch("scan-A")
    assert result.status == "complete"
    manifest = result.full_suite_manifest
    assert manifest["counts"] == {"expected": 18, "evidence_rows": 18, "missing_required": 0}
    assert all(r["verdict"] == "pass" and r["target_calls"] == r["judge_calls"] == 0 and r["diagnostic_measurements"] for r in manifest["evidence_rows"])
    restored = FullScanJobStore(persistence=FullScanJobPersistence(db)).status(manifest["job_id"])
    assert [r.to_manifest_row() for r in restored.evidence_rows] == manifest["evidence_rows"]
    persisted = runtime.current_kickoff_row("scan-A")["score_json"]
    report = _external_result_envelope(result, persisted)
    inventory = report["requirements_evidence"]["evidence_inventory"]
    assert inventory["with_reported_digest"] == 18 and report["are_total"] == 72
    assert "private-customer-detail" not in json.dumps(report) and "content_base64" not in json.dumps(report)
    html = render_requirements(persisted)
    assert all(case in html for case in records.CASE_METHODS) and "OPS-LOG-BOUNDARY" in html
    assert "accuracy_ppm: 1000000" in html
    assert "consistency of supplied reviewed records" in html and "Observation scope" in html
