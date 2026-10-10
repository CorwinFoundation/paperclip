"""BEAAA-38987: Full Scan draws fresh private forms from the real conv-en bank."""
import json
import hashlib
import sqlite3
import time

import pytest

from api.full_scan_executor import (
    FullScanExecutorError,
    FullScanJobStore,
    WorkerAttemptResult,
    build_attempt_manifest,
    dispatch_variable_form_attempt,
)
from api.full_suite_manifest import FullSuiteCase
from scanners.full_scan_form_adapter import (
    FULL_SCAN_FORM_BUDGET_V1,
    all_conv_en_cases,
    create_full_scan_form,
    form_test_spec_ids,
    strata_from_cases,
)
from scanners.variable_test_forms import FormStore, InsufficientScenarios

SCOPE = ("tenant-a", "agent-1")


def test_consecutive_full_forms_are_disjoint_and_cover_every_stratum(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite3")
    first = create_full_scan_form(store, scope=SCOPE, bank_version="conv-en@1")
    second = create_full_scan_form(store, scope=SCOPE, bank_version="conv-en@1")
    a, b = form_test_spec_ids(first), form_test_spec_ids(second)
    assert a and not (a & b)
    expected = sum(s.count for s in strata_from_cases(all_conv_en_cases()))
    assert first["test_count"] == second["test_count"] == expected
    assert first["counts_as_additional_unique_tests"] == 0
    covered = {c["stratum_id"] for c in second["cases"]}
    assert covered == {s.stratum_id for s in strata_from_cases(all_conv_en_cases())}
    # Another agent's history does not constrain this one.
    other = create_full_scan_form(store, scope=("tenant-a", "agent-2"), bank_version="conv-en@1")
    assert other["test_count"] == expected


def test_retry_replays_exact_form_and_public_summary_has_no_payload(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite3")
    form = create_full_scan_form(store, scope=SCOPE, bank_version="conv-en@1", form_id="job-1")
    again = create_full_scan_form(store, scope=SCOPE, bank_version="conv-en@1", form_id="job-1")
    assert again == form == store.get("job-1", scope=SCOPE, product="full")
    public = json.dumps(FormStore.report_summary(form))
    assert form["selection_seed"] not in public
    assert form["cases"][0]["payload"]["system"] not in public


def test_thin_bank_fails_instead_of_static_fallback(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite3")
    one = all_conv_en_cases()[:1]
    with pytest.raises(InsufficientScenarios):
        create_full_scan_form(store, scope=SCOPE, bank_version="conv-en@1", cases=one)


def test_bank_missing_required_family_fails_before_dispatch(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite3")
    bank = all_conv_en_cases()
    missing_family = bank[0].family_id
    incomplete = tuple(case for case in bank if case.family_id != missing_family)

    with pytest.raises(InsufficientScenarios, match=missing_family):
        create_full_scan_form(
            store, scope=SCOPE, bank_version="conv-en@1", cases=incomplete
        )

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM variable_test_forms").fetchone()[0] == 0


def test_history_window_does_not_change_versioned_budget():
    bank = all_conv_en_cases()
    assert {s.stratum_id: s.count for s in strata_from_cases(bank, history_window=1)} == FULL_SCAN_FORM_BUDGET_V1
    assert {s.stratum_id: s.count for s in strata_from_cases(bank, history_window=7)} == FULL_SCAN_FORM_BUDGET_V1


def _registry():
    return tuple(FullSuiteCase(case_id=c.case_id, rubric_point_id=c.rubric_point_id, required=True)
                 for c in all_conv_en_cases())


def test_executor_submit_reserves_form_and_dispatches_only_its_cases(tmp_path):
    form_store = FormStore(tmp_path / "forms.sqlite3")
    jobs = FullScanJobStore()
    kwargs = dict(agent_id="agent-1", scanner_version="0.0.0", corpus_version="conv-en@1",
                  model_versions={}, seed=0, registry_cases=_registry(),
                  form_store=form_store, tenant_id="tenant-a")
    job = jobs.submit(**kwargs)
    form = form_store.get(job.form_id, scope=SCOPE, product="full")
    assert {c.case_id for c in job.registry_cases} == form_test_spec_ids(form)
    manifest = json.dumps(build_attempt_manifest(job), default=str)
    assert job.form_id in manifest and form["selection_seed"] not in manifest
    second = jobs.submit(**kwargs)
    assert not ({c.case_id for c in job.registry_cases} & {c.case_id for c in second.registry_cases})
    with pytest.raises(FullScanExecutorError, match="tenant_id"):
        jobs.submit(**{**kwargs, "tenant_id": None})


def test_v10_submit_and_worker_use_exact_reserved_payloads(tmp_path):
    form_store = FormStore(tmp_path / "forms.sqlite3")
    jobs = FullScanJobStore()
    job = jobs.submit(
        agent_id="agent-1", scanner_version="0.0.0", corpus_version="conv-en@1",
        model_versions={}, seed=0, pack_id="full-scan-conv-en@1.0",
        form_store=form_store, tenant_id="tenant-a",
    )
    form = form_store.get(job.form_id, scope=SCOPE, product="full")
    seen = []

    def worker(row, current_job):
        payload_keys = form["cases"][0]["payload"]
        seen.append({key: row[key] for key in payload_keys})
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{current_job.job_id}/{row['case_id']}.json",
        )

    lease = jobs.lease(job.job_id)
    dispatch_variable_form_attempt(
        jobs, job.job_id, attempt_id=lease["attempt_id"],
        form_store=form_store, workers={"promptfoo": worker},
    )
    assert seen == [item["payload"] for item in form["cases"]]
    assert len(seen) == 704


def test_v10_cost_cap_resume_reuses_reserved_form_and_skips_dispatched_payloads(tmp_path):
    form_store = FormStore(tmp_path / "forms.sqlite3")
    jobs = FullScanJobStore()
    job = jobs.submit(
        agent_id="agent-1", scanner_version="0.0.0", corpus_version="conv-en@1",
        model_versions={}, seed=0, pack_id="full-scan-conv-en@1.0",
        max_cost_usd=0.15, form_store=form_store, tenant_id="tenant-a",
    )
    form_id = job.form_id
    form = form_store.get(form_id, scope=SCOPE, product="full")
    reserved = {item["scenario_id"]: item["payload"] for item in form["cases"]}
    seen = []

    def worker(row, current_job):
        seen.append((row["case_id"], {key: row[key] for key in reserved[row["case_id"]]}))
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{current_job.job_id}/{row['case_id']}.json",
            estimated_judge_cost_usd=0.10,
        )

    lease = jobs.lease(job.job_id)
    first_id = form["cases"][0]["scenario_id"]
    dispatch_variable_form_attempt(
        jobs, job.job_id, attempt_id=lease["attempt_id"], form_store=form_store,
        workers={"promptfoo": worker}, case_ids=(first_id,),
    )
    resumed = dispatch_variable_form_attempt(
        jobs, job.job_id, attempt_id=lease["attempt_id"], form_store=form_store,
        workers={"promptfoo": worker},
    )
    final = jobs.finalize_cost_cap(job.job_id, attempt_id=lease["attempt_id"])

    assert final.status == "incomplete_cost_cap"
    assert final.form_id == form_id
    assert form_store.get(form_id, scope=SCOPE, product="full") == form
    assert [case_id for case_id, _ in seen].count(first_id) == 1
    assert len(seen) == 2
    assert all(payload == reserved[case_id] for case_id, payload in seen)
    capped = [row for row in resumed.evidence_rows if row.incomplete_reason == "cost_cap_reached"]
    assert len(capped) == len(form["cases"]) - len(seen)
    assert {row.probe_id for row in capped} == set(reserved) - {case_id for case_id, _ in seen}


def test_real_underwriting_registry_submit_is_not_form_filtered():
    from tools.active_test_registry import build_registry
    from api.full_scan_executor import registry_cases_from_rows

    cases = registry_cases_from_rows(build_registry())
    job = FullScanJobStore().submit(
        agent_id="agent-1", scanner_version="0.0.0", corpus_version="registry",
        model_versions={}, seed=0, registry_cases=cases, tenant_id="tenant-a",
    )
    assert job.form_id is None
    assert [c.case_id for c in job.registry_cases] == [c.case_id for c in cases]


def test_executor_rejects_any_missing_reserved_case(tmp_path):
    form_store = FormStore(tmp_path / "forms.sqlite3")
    form = create_full_scan_form(
        form_store, scope=SCOPE, bank_version="conv-en@1", form_id="fixed-form"
    )
    selected_id = form["cases"][0]["test_spec_id"]
    incomplete_registry = tuple(c for c in _registry() if c.case_id != selected_id)
    with pytest.raises(FullScanExecutorError, match="missing registry cases"):
        FullScanJobStore().submit(
            agent_id="agent-1", scanner_version="0.0.0", corpus_version="conv-en@1",
            model_versions={}, seed=0, registry_cases=incomplete_registry,
            form_store=form_store, tenant_id="tenant-a", form_id="fixed-form",
        )


def test_retry_reads_stored_form_before_changed_bank(tmp_path):
    form_store = FormStore(tmp_path / "forms.sqlite3")
    jobs = FullScanJobStore()
    kwargs = dict(agent_id="agent-1", scanner_version="0.0.0", corpus_version="conv-en@1",
                  model_versions={}, seed=0, registry_cases=_registry(),
                  form_store=form_store, tenant_id="tenant-a", form_id="stable-form")
    first = jobs.submit(**kwargs)
    original = form_store.get(first.form_id, scope=SCOPE, product="full")
    replay = FullScanJobStore().submit(**{**kwargs, "form_bank_version": "changed-bank"})
    assert replay.form_id == first.form_id
    assert form_store.get(replay.form_id, scope=SCOPE, product="full") == original


def test_retry_replays_saved_payload_after_current_bank_changes(tmp_path, monkeypatch):
    form_store = FormStore(tmp_path / "forms.sqlite3")
    jobs = FullScanJobStore()
    kwargs = dict(agent_id="agent-1", scanner_version="0.0.0", corpus_version="conv-en@1",
                  model_versions={}, seed=0, registry_cases=_registry(),
                  form_store=form_store, tenant_id="tenant-a", form_id="stable-form")
    first = jobs.submit(**kwargs)
    form = form_store.get(first.form_id, scope=SCOPE, product="full")
    monkeypatch.setattr(
        "scanners.full_scan_form_adapter.all_conv_en_cases", lambda: ()
    )
    replay = FullScanJobStore().submit(**kwargs)
    assert replay.form_id == first.form_id
    assert form_store.get(replay.form_id, scope=SCOPE, product="full") == form


def test_dispatch_rejects_mutated_private_payload_before_worker(tmp_path):
    form_store = FormStore(tmp_path / "forms.sqlite3")
    jobs = FullScanJobStore()
    job = jobs.submit(
        agent_id="agent-1", scanner_version="0.0.0", corpus_version="conv-en@1",
        model_versions={}, seed=0, pack_id="full-scan-conv-en@1.0",
        form_store=form_store, tenant_id="tenant-a",
    )
    with sqlite3.connect(form_store.path) as conn:
        raw = conn.execute(
            "SELECT manifest FROM variable_test_forms WHERE form_id=?", (job.form_id,)
        ).fetchone()[0]
        manifest = json.loads(raw)
        manifest["cases"][0]["payload"]["system"] = "mutated after reservation"
        encoded = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        )
        conn.execute(
            "UPDATE variable_test_forms SET content_hash=?, manifest=? WHERE form_id=?",
            (hashlib.sha256(encoded.encode("utf-8")).hexdigest(), encoded, job.form_id),
        )
    called = []
    lease = jobs.lease(job.job_id)
    with pytest.raises(ValueError, match="payload_hash_mismatch"):
        dispatch_variable_form_attempt(
            jobs, job.job_id, attempt_id=lease["attempt_id"],
            form_store=form_store, workers={"promptfoo": lambda *_: called.append(True)},
        )
    assert called == []


def test_public_scan_api_accepts_v10_and_dispatches_saved_form(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.app import create_app
    from api.bundle import BundleStore
    from api.scan_handlers import ScanRuntime
    from rubric.scoring import Rubric
    from scanners.live_probe_orchestrator import DimResult, ScanResult
    from tools.active_test_registry import build_registry

    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-38987-test-key")
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed", lambda **_kwargs: None
    )
    seen = []

    def run_scan(request, _secret):
        return ScanResult(
            scan_id=request.scan_id, status="complete",
            scanner_version=request.scanner_version,
            corpus_version=request.corpus_version, rows_emitted=4,
            k_replicates=request.k_replicates,
            dim_results=(DimResult("P1.1", 3.0, 0.0, (3.0,) * request.k_replicates),),
        )

    def worker(row, job):
        seen.append(dict(row))
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
        )

    form_store = FormStore(tmp_path / "forms.sqlite3")
    runtime = ScanRuntime(
        run_scan_fn=run_scan,
        full_scan_registry_rows=build_registry(),
        full_scan_workers_factory=lambda _request, _secret: {"promptfoo": worker},
        full_scan_form_store=form_store,
    )
    app = create_app(
        rubric=Rubric.load("rubric/rubric.yaml"), bundles=BundleStore(),
        scan_runtime=runtime,
    )
    response = TestClient(app).post(
        "/scanner/v1/scans",
        json={
            "tenant_id": "tenant-a", "agent_id": "agent-1",
            "agent_endpoint": {"host": "agent.customer.example.com"},
            "credential_ref": "paid-lane.v1", "credential_secret": "token",
            "corpus_snapshot_hash": "sha256:" + "1" * 64,
            "scanner_version": "1.2.0", "corpus_version": "conv-en@1",
            "seed": 0, "scan_mode": "live_probe", "k_replicates": 5,
            "pack_id": "full-scan-conv-en@1.0",
        },
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 202
    deadline = time.monotonic() + 15
    while len(seen) < 704 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(seen) == 704
    job_id = runtime._full_scan_job_ids[response.json()["scan_id"]]
    job = runtime._full_scan_job_store.status(job_id)
    form = form_store.get(job.form_id, scope=SCOPE, product="full")
    deadline = time.monotonic() + 15
    while len(job.evidence_rows) < len(form["cases"]) and time.monotonic() < deadline:
        time.sleep(0.01)
        job = runtime._full_scan_job_store.status(job_id)
    # Case workers may finish in any order; the signed durable evidence
    # projection, rather than invocation timing, is the stable contract.
    assert {row["case_id"] for row in seen} == {
        item["scenario_id"] for item in form["cases"]
    }
    assert [row.probe_id for row in job.evidence_rows] == sorted(
        item["scenario_id"] for item in form["cases"]
    )
    seen_by_case = {row["case_id"]: row for row in seen}
    assert [
        {key: seen_by_case[item["scenario_id"]][key] for key in item["payload"]}
        for item in form["cases"]
    ] == [item["payload"] for item in form["cases"]]
