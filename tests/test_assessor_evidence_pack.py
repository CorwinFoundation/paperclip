"""Scope-bound reuse/export of original scanner evidence, including zero-model logs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from zipfile import ZipFile

import pytest

from evals.oss_pilots.log_quality import run_case
from requirement_assessment import build_requirement_assessment, directory_reader, import_registered_case, load_registry, plan_digest
from scripts.run_operational_controls import FixtureLogCapture
from tests.test_requirement_evidence_import import approved, source, NOW, SCOPE
from tools.requirement_evidence_pack import export_pack


def imported(tmp_path, native=None):
    raw, plan = approved(source() if native is None else native)
    ref = import_registered_case(raw, plan=plan, binding_index=0, artifact_directory=tmp_path)
    bundle = {"assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": [ref], "documents": []}
    return raw, plan, bundle


def test_pack_retains_original_bytes_checksums_and_missing_document_worksheet(tmp_path):
    artifacts = tmp_path / "source"
    raw, plan, bundle = imported(artifacts)
    target = tmp_path / "assessment.zip"
    index = export_pack(plan=plan, bundle=bundle, artifact_directory=artifacts, output=target, evaluated_at=NOW)
    with ZipFile(target) as archive:
        checksums = json.loads(archive.read("CHECKSUMS.json"))
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in checksums.items())
        assert archive.read("artifacts/" + hashlib.sha256(raw).hexdigest() + ".json") == raw
        assert b"risk-register" in archive.read("documents.csv")
        assessment = json.loads(archive.read("assessment.json"))
        row = next(c for f in assessment["frameworks"] if f["framework_id"] == "eu-ai-act"
                   for c in f["controls"] if c["control_id"] == "ART9")
        assert row["technical_status"] == "passed" and row["evidence_complete_for_plan"] is False
        assert row["requirement_conclusion"] == "not assessed"
    assert len(index["artifacts"]) == 2
    assert index["certification_status"] == "not certification"


def test_failed_original_is_packaged_without_rejudging_or_disappearing(tmp_path):
    raw, plan, bundle = imported(tmp_path / "source", source() | {"verdict": "fail"})
    target = tmp_path / "failed.zip"
    export_pack(plan=plan, bundle=bundle, artifact_directory=tmp_path / "source", output=target, evaluated_at=NOW)
    with ZipFile(target) as archive:
        assert archive.read("artifacts/" + hashlib.sha256(raw).hexdigest() + ".json") == raw
        assert any(c["technical_status"] == "failed" for f in json.loads(archive.read("assessment.json"))["frameworks"] for c in f["controls"])


def test_tampered_sources_are_rejected_and_not_copied_into_pack(tmp_path):
    root = tmp_path / "source"
    raw, plan, bundle = imported(root)
    (root / (hashlib.sha256(raw).hexdigest() + ".json")).write_bytes(b"substituted")
    target = tmp_path / "rejected.zip"
    index = export_pack(plan=plan, bundle=bundle, artifact_directory=root, output=target, evaluated_at=NOW)
    assert index["rejected_reference_count"] == 1 and index["artifacts"] == []
    with ZipFile(target) as archive:
        assert not any(name.startswith("artifacts/") for name in archive.namelist())
        assert not any(c["technical_status"] == "passed" for f in json.loads(archive.read("assessment.json"))["frameworks"] for c in f["controls"])


def test_pack_never_overwrites_an_existing_file_or_follows_artifact_uris(tmp_path):
    _, plan, bundle = imported(tmp_path / "source")
    # The source URI is attribution metadata; directory_reader resolves only hashes.
    target = tmp_path / "existing.zip"
    target.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        export_pack(plan=plan, bundle=bundle, artifact_directory=tmp_path / "source", output=target, evaluated_at=NOW)
    assert target.read_bytes() == b"keep me"
    assert not list(tmp_path.glob(".evidence-pack-*.partial"))
    bundle["scope"] = bundle["scope"] | {"deployment_id": "other-deployment"}
    with pytest.raises(ValueError):
        export_pack(plan=plan, bundle=bundle, artifact_directory=tmp_path / "source", output=tmp_path / "invalid.zip", evaluated_at=NOW)
    assert not (tmp_path / "invalid.zip").exists()


def native_quality(case_id="OPS-LOG-PAYLOAD", corrupt=False):
    context = {"tenant_id": "tenant-1", "scan_id": "scan-1", "agent_id": SCOPE["target_id"]}
    capture = run_case(case_id, FixtureLogCapture("reviewer-1"), scope=context, recipient_id="reviewer-1")
    if corrupt:
        capture["snapshots"][0][0]["payload"]["active"] = 0
    native = source() | {"test_id": "operational_logging_quality", "case_id": case_id,
        "version_pin": "operational-log-quality@1", "scan_id": context["scan_id"],
        "capture": capture, "reported_not_scored": True, "responses": [],
        "budget": {"case_delta": {"target_calls": 0, "judge_calls": 0}}, "execution_mode": "conditional_operational"}
    raw, plan = approved(native)
    now = datetime.now(timezone.utc)
    plan.update(valid_from=(now - timedelta(minutes=5)).isoformat(), valid_until=(now + timedelta(hours=1)).isoformat())
    plan["approval"]["approved_at"] = (now - timedelta(minutes=4)).isoformat()
    eu = next(f for f in load_registry()["frameworks"] if f["framework_id"] == "eu-ai-act")
    plan["controls"][0].update(control_id="ART12", mapping_version=eu["mapping_version"],
        required_tests=[{"test_id": native["test_id"], "case_id": case_id, "probe_set_version": native["version_pin"], "trials": 1}])
    plan["existing_evidence_bindings"][0].update(capture_scope=context, observed_at=now.isoformat())
    plan["alert_recipient_id"] = "reviewer-1"
    return raw, plan, now.isoformat()


def test_zero_model_logging_source_can_supply_plan_scoped_verified_evidence(tmp_path):
    raw, plan, now = native_quality()
    ref = import_registered_case(raw, plan=plan, binding_index=0, artifact_directory=tmp_path / "source")
    bundle = {"assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": [ref], "documents": []}
    assessment = build_requirement_assessment(bundle, plan=plan, artifact_reader=directory_reader(tmp_path / "source"), evaluated_at=now)
    row = next(c for f in assessment["frameworks"] if f["framework_id"] == "eu-ai-act" for c in f["controls"] if c["control_id"] == "ART12")
    assert row["technical_status"] == "passed" and row["missing_documents"]
    index = export_pack(plan=plan, bundle=bundle, artifact_directory=tmp_path / "source", output=tmp_path / "logs.zip", evaluated_at=now)
    assert len(index["artifacts"]) == 2


def test_cli_exports_verified_pack_and_refuses_to_overwrite(tmp_path, capsys):
    from tools.requirement_evidence import main
    _, plan, bundle = imported(tmp_path / "source")
    for name, value in [("plan", plan), ("bundle", bundle)]:
        (tmp_path / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
    target = tmp_path / "cli.zip"
    args = ["--export-pack", "--plan", str(tmp_path / "plan.json"), "--bundle", str(tmp_path / "bundle.json"),
            "--artifacts", str(tmp_path / "source"), "--evaluated-at", NOW, "--output", str(target)]
    assert main(args) == 0
    original = target.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2 and target.read_bytes() == original
    assert "no assessment was issued" in capsys.readouterr().err


def test_native_failed_diagnostic_capture_is_preserved_in_export(tmp_path):
    raw, plan, now = native_quality()
    native = json.loads(raw)
    native["capture"]["snapshots"][0][0]["payload"]["active"] = 0
    from evals.oss_pilots.log_quality import measure
    scope = plan["existing_evidence_bindings"][0]["capture_scope"]
    native["capture"].update(verdict="failed", measurements=measure(native["case_id"], native["capture"], scope=scope))
    native["verdict"] = "fail"
    raw = json.dumps(native, sort_keys=True).encode()
    binding = plan["existing_evidence_bindings"][0]
    binding["source_sha256"] = hashlib.sha256(raw).hexdigest()
    ref = import_registered_case(raw, plan=plan, binding_index=0, artifact_directory=tmp_path / "source")
    bundle = {"assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": [ref], "documents": []}
    target = tmp_path / "failed-diagnostic.zip"
    export_pack(plan=plan, bundle=bundle, artifact_directory=tmp_path / "source", output=target, evaluated_at=now)
    with ZipFile(target) as archive:
        assert archive.read("artifacts/" + hashlib.sha256(raw).hexdigest() + ".json") == raw
        row = next(c for f in json.loads(archive.read("assessment.json"))["frameworks"] if f["framework_id"] == "eu-ai-act"
                   for c in f["controls"] if c["control_id"] == "ART12")
        assert row["technical_status"] == "failed"


@pytest.mark.parametrize("family", ["operational_oversight", "operational_logging"])
def test_existing_native_operational_artifacts_are_reused_without_verdict_reinterpretation(tmp_path, monkeypatch, family):
    from types import SimpleNamespace
    from tests.test_standard_requirement_reports import mock_customer_http
    from harness.customer_runner import customer_agent_http_runner_factory
    from harness.runner import MockRunner
    from tools.registered_case_executor import RegisteredCaseExecutor, EvidenceStore, ScanBudget
    from tools.operational_scan import registry_rows
    mock_customer_http(monkeypatch)
    endpoint = "https://target.example/v1/chat/completions"
    config = {"transport": "openai_chat", "model": "customer-model"}
    worker = RegisteredCaseExecutor(
        subject=customer_agent_http_runner_factory(endpoint, "secret", SCOPE["target_id"], endpoint_config=config),
        judge=MockRunner("judge", lambda _: pytest.fail("judge called")),
        evidence_store=EvidenceStore(tmp_path / "native"), budget=ScanBudget(), tenant_id="tenant-A",
        operational_log_capture_factory=lambda _: FixtureLogCapture("reviewer-1"),
        operational_target=(endpoint, "secret", config))
    row = next(r for r in registry_rows() if r["test_id"] == family and
               (family != "operational_oversight" or r["case_id"] == "OPS-APPROVAL-DENIED"))
    outcome = worker(row, SimpleNamespace(job_id="original-job", scan_id="original-scan", agent_id=SCOPE["target_id"], seed=0))
    assert outcome.verdict == "pass"
    raw = next((tmp_path / "native").rglob(row["case_id"] + ".json")).read_bytes()
    _, plan = approved(json.loads(raw))
    case = {"test_id": family, "case_id": row["case_id"], "probe_set_version": row["version_pin"], "trials": 1}
    plan["controls"][0].update(control_id="ART14" if family == "operational_oversight" else "ART12", required_tests=[case])
    binding = plan["existing_evidence_bindings"][0]
    binding.update(source_sha256=hashlib.sha256(raw).hexdigest(), artifact_uri=outcome.evidence_ref,
        capture_scope={"tenant_id": "tenant-A", "scan_id": "original-scan", "agent_id": SCOPE["target_id"]})
    ref = import_registered_case(raw, plan=plan, binding_index=0, artifact_directory=tmp_path / "source")
    bundle = {"assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": [ref], "documents": []}
    assessment = build_requirement_assessment(bundle, plan=plan, artifact_reader=directory_reader(tmp_path / "source"), evaluated_at=NOW)
    control = next(c for f in assessment["frameworks"] if f["framework_id"] == "eu-ai-act" for c in f["controls"]
                   if c["control_id"] == plan["controls"][0]["control_id"])
    assert control["technical_status"] == "passed" and ref["verdict"] == "pass"
    if family == "operational_oversight":
        fixture = json.loads(raw) | {"target_model": "mock:fixture-target"}
        fixture_raw = json.dumps(fixture).encode()
        fixture_plan = deepcopy(plan)
        fixture_plan["existing_evidence_bindings"][0]["source_sha256"] = hashlib.sha256(fixture_raw).hexdigest()
        with pytest.raises(ValueError, match="fixture"):
            import_registered_case(fixture_raw, plan=fixture_plan, binding_index=0, artifact_directory=tmp_path / "rejected")


@pytest.mark.parametrize("mutation", ["scope", "measurements", "recipient"])
def test_reviewed_scope_metrics_and_recipient_cannot_be_substituted(tmp_path, mutation):
    raw, plan, _ = native_quality("OPS-ALERT-SELECTIVITY" if mutation == "recipient" else "OPS-LOG-PAYLOAD", corrupt=mutation == "measurements")
    if mutation == "scope": plan["existing_evidence_bindings"][0]["capture_scope"]["scan_id"] = "other-scan"
    if mutation == "recipient": plan["alert_recipient_id"] = "other-recipient"
    with pytest.raises(ValueError):
        import_registered_case(raw, plan=plan, binding_index=0, artifact_directory=tmp_path)


def test_reviewed_document_original_bytes_are_included_with_attribution(tmp_path):
    root = tmp_path / "source"
    _, plan, bundle = imported(root)
    for document_id in ["risk-register", "risk-treatment-and-residual-acceptance"]:
        raw = ("Reviewed synthetic document: " + document_id).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (root / (digest + ".json")).write_bytes(raw)
        bundle["documents"].append({"document_id": document_id, "assessment_id": plan["assessment_id"],
            "plan_sha256": plan_digest(plan), "scope": deepcopy(plan["scope"]), "artifact_uri": "evidence://" + digest,
            "sha256": digest, "review_status": "accepted", "reviewer": "document-reviewer", "reviewed_at": NOW})
    target = tmp_path / "complete-for-plan.zip"
    index = export_pack(plan=plan, bundle=bundle, artifact_directory=root, output=target, evaluated_at=NOW)
    assert sum("reviewed_document" in item["roles"] for item in index["artifacts"]) == 2
    with ZipFile(target) as archive:
        row = next(c for f in json.loads(archive.read("assessment.json"))["frameworks"] if f["framework_id"] == "eu-ai-act"
                   for c in f["controls"] if c["control_id"] == "ART9")
        assert row["evidence_complete_for_plan"] and row["requirement_conclusion"] == "not assessed"
