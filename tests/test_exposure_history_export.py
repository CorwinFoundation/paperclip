"""Historical originals are approved, replayed and packaged before comparison."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from zipfile import ZipFile

from exposure_history import history_from_registered_sources
from requirement_assessment import import_registered_case
from tests.synthetic_assurance_fixtures import factory
from tests.test_standard_requirement_reports import executor
from tests.test_synthetic_assurance import assessment_plan
from tools.operational_scan import registry_rows
from tools.requirement_evidence_pack import export_pack


def test_assessor_export_replays_two_scan_originals_and_timeline_using_zip_bytes(tmp_path):
    family = "synthetic_workflow_revocation"
    row = next(r for r in registry_rows() if r["test_id"] == family)
    originals = []
    for i, profile in enumerate(("unsafe", "compliant")):
        directory = tmp_path / f"native-{i}"
        worker = executor(directory, operational_session_factory=factory(profile))
        worker(row, SimpleNamespace(job_id=f"job-{i}", scan_id=f"scan-{i}", agent_id="agent-A", seed=42))
        raw = next(directory.rglob(row["case_id"] + ".json")).read_bytes()
        originals.append((json.loads(raw), raw))
    body, raw = originals[0]
    plan, now = assessment_plan(body, raw)
    plan["existing_evidence_bindings"][0]["capture_scope"] = body["capture"]["scope"]
    binding = deepcopy(plan["existing_evidence_bindings"][0])
    # Bind the separately collected scan to its own native acquisition identity.
    binding.update({key: originals[1][0][key] for key in
                    ("job_id", "agent_id", "test_id", "case_id", "version_pin", "seed")})
    binding.update(trial_index=1, source_sha256=hashlib.sha256(originals[1][1]).hexdigest(),
        capture_scope=originals[1][0]["capture"]["scope"], artifact_uri="evidence://native-second.json")
    plan["existing_evidence_bindings"].append(binding)
    assert plan["existing_evidence_bindings"][0]["job_id"] != binding["job_id"]
    for c in plan["controls"]: c["required_tests"][0]["trials"] = 2
    root = tmp_path / "artifacts"
    refs = [import_registered_case(raw, plan=plan, binding_index=i, artifact_directory=root)
            for i, (_, raw) in enumerate(originals)]
    bundle = {"assessment_id": plan["assessment_id"], "scope": plan["scope"], "evidence_refs": refs, "documents": []}
    target = tmp_path / "history.zip"
    export_pack(plan=plan, bundle=bundle, artifact_directory=root, output=target, evaluated_at=now)
    with ZipFile(target) as archive:
        history = json.loads(archive.read("exposure-history.json"))
        entries = history["subjects"][0]["entries"]
        assert len(entries) == 1 and entries[0]["state"] == "cleared_on_comparable_retest"
        assert entries[0]["events"][0]["state"] == "newly_observed"
        assert b"cleared_on_comparable_retest" in archive.read("exposure-history.csv")
        replay = [(json.loads(archive.read("artifacts/" + ref["source_registered_case"]["sha256"] + ".json")), ref)
                  for ref in json.loads(archive.read("bundle.json"))["evidence_refs"]]
        assert history_from_registered_sources(replay, approved_scope=plan["scope"]) == history
        checksums = json.loads(archive.read("CHECKSUMS.json"))
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in checksums.items())


def test_multiple_cases_from_one_scan_are_grouped_before_history_comparison():
    sources = []
    for case, verdict, instant in (("case-A", "fail", "2026-10-20T10:00:00+00:00"),
                                   ("case-B", "pass", "2026-10-20T10:00:01+00:00")):
        original = {"test_id": "family", "case_id": case, "verdict": verdict,
                    "capture": {"evaluated_at": instant, "scope": {"tenant_id": "t", "agent_id": "a", "scan_id": "s"},
                                "trials": [{"model_id": "target-model"}]}}
        sources.append((original, {"probe_set_version": "v1", "source_registered_case": {"sha256": "a"*64}}))
    result = history_from_registered_sources(sources, approved_scope={"target_id": "a"})
    assert len(result["subjects"]) == 1
    assert result["subjects"][0]["entries"][0]["case_id"] == "case-A"
