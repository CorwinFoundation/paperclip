"""Measured diagnostic challenges, including real local persistence failures."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from evals.oss_pilots.log_quality import CASE_IDS, run_case, measure, passed
from scripts.run_operational_controls import FixtureLogCapture
from tools.operational_scan import registry_rows
from tools.registered_case_executor import RegisteredCaseExecutor, EvidenceStore, ScanBudget
from harness.runner import MockRunner
from standard_requirement_report import build_standard_requirement_report

SCOPE = {"tenant_id": "tenant-A", "scan_id": "scan-A", "agent_id": "agent-A"}


class JsonLog:
    recipient_id = "reviewer-A"

    def __init__(self, path, change=lambda events: events):
        self.path, self.change = path, change

    def emit(self, event):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")

    def read(self, batch):
        events = [e for e in map(json.loads, self.path.read_text().splitlines()) if e["batch_id"] == batch]
        return self.change(events)

    def read_deliveries(self, batch):
        return [{"event_id": e["event_id"], "batch_id": batch, "scope": e["scope"],
                 "recipient_id": self.recipient_id, "status": "delivered"}
                for e in self.read(batch) if e["kind"] == "failed"]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_real_jsonl_roundtrip_records_measurements_without_model_calls(tmp_path, case_id):
    result = run_case(case_id, JsonLog(tmp_path / "log.jsonl"), scope=SCOPE, recipient_id="reviewer-A")
    assert result["verdict"] == "passed"
    assert passed(result["measurements"])
    assert result["measurements"] == measure(case_id, result, scope=SCOPE, recipient_id="reviewer-A")
    assert "retention" in result["observation_scope"]


@pytest.mark.parametrize("change", [
    lambda e: e[0]["payload"].update(amount="1.25"),
    lambda e: e[0]["payload"].update(active=0),
    lambda e: e[0]["payload"].update(text="diagnostic cafe"),
    lambda e: e[0].pop("payload"),
])
def test_payload_coercion_loss_and_unicode_corruption_are_failures(tmp_path, change):
    def corrupt(events):
        change(events)
        return events
    result = run_case("OPS-LOG-PAYLOAD", JsonLog(tmp_path / "log", corrupt), scope=SCOPE)
    assert result["verdict"] == "failed"
    assert result["measurements"]["payload_errors"] == 1


@pytest.mark.parametrize("value", [None, "2026-10-27T12:00:00", "not-a-time", "2000-01-01T00:00:00Z"])
def test_missing_naive_invalid_and_changed_timestamps_fail(tmp_path, value):
    def corrupt(events):
        events[0]["occurred_at"] = value
        return events
    result = run_case("OPS-LOG-TIME", JsonLog(tmp_path / "log", corrupt), scope=SCOPE)
    assert result["verdict"] == "failed" and result["measurements"]["timestamp_errors"] == 1


def test_utc_spelling_change_is_not_clock_corruption(tmp_path):
    def normalize(events):
        for event in events:
            event["occurred_at"] = event["occurred_at"].replace("+00:00", "Z")
        return events
    assert run_case("OPS-LOG-TIME", JsonLog(tmp_path / "log", normalize), scope=SCOPE)["verdict"] == "passed"


def test_cross_batch_reader_cannot_pass_even_with_valid_event_scopes(tmp_path):
    class Leaky(JsonLog):
        def read(self, _):
            return list(map(json.loads, self.path.read_text().splitlines()))
    result = run_case("OPS-LOG-BATCH", Leaky(tmp_path / "log"), scope=SCOPE)
    assert result["verdict"] == "failed" and result["measurements"]["identity_errors"] > 0


def test_consumptive_read_and_changed_second_snapshot_fail(tmp_path):
    class Destructive(JsonLog):
        def read(self, batch):
            result = super().read(batch)
            self.path.write_text("")
            return result
    result = run_case("OPS-LOG-REPEAT", Destructive(tmp_path / "log"), scope=SCOPE)
    assert result["verdict"] == "failed" and result["measurements"]["repeat_difference_count"] == 1


def test_foreign_scope_records_fail_without_copying_private_bytes(tmp_path):
    def misroute(events):
        events[0].update(scope={"tenant_id": "other-tenant"}, payload={"private": "foreign-secret"})
        return events
    result = run_case("OPS-LOG-BATCH", JsonLog(tmp_path / "log", misroute), scope=SCOPE)
    assert result["verdict"] == "failed" and result["measurements"]["identity_errors"] > 0
    assert "foreign-secret" not in json.dumps(result) and "other-tenant" not in json.dumps(result)


def test_alert_selectivity_needs_positive_and_negative_controls():
    class Silent(FixtureLogCapture):
        def read_deliveries(self, batch): return []
    class Noisy(FixtureLogCapture):
        def read_deliveries(self, batch):
            return [{"event_id": e["event_id"], "batch_id": batch, "scope": e["scope"],
                     "recipient_id": self.recipient_id, "status": "delivered"} for e in self.read(batch)]
    for adapter, metric in [(Silent("reviewer-A"), "missing_alert_count"), (Noisy("reviewer-A"), "false_alert_count"),
                            (FixtureLogCapture("wrong-recipient"), "missing_alert_count")]:
        result = run_case("OPS-ALERT-SELECTIVITY", adapter, scope=SCOPE, recipient_id="reviewer-A")
        assert result["verdict"] == "failed" and result["measurements"][metric] > 0


def test_absent_broken_and_oversized_adapters_cannot_pass_or_leak_errors():
    class Broken:
        def emit(self, _): raise ValueError("private credential and backend URL")
    class Oversized(FixtureLogCapture):
        def read(self, batch): return super().read(batch) * 10
    for adapter in [None, Broken(), Oversized()]:
        result = run_case("OPS-LOG-PAYLOAD", adapter, scope=SCOPE)
        assert result["verdict"] == "inconclusive"
        assert "private credential" not in json.dumps(result)


def test_registered_quality_cases_produce_hashed_artifacts_and_report_measurements(tmp_path):
    def forbidden(_): pytest.fail("model or judge was called")
    worker = RegisteredCaseExecutor(subject=MockRunner("target", forbidden), judge=MockRunner("judge", forbidden),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=0, max_judge_calls=0),
        tenant_id="tenant-A", operational_log_capture_factory=lambda _: FixtureLogCapture("reviewer-A"))
    rows = [r for r in registry_rows() if r["test_id"] == "operational_logging_quality"]
    observations = []
    for row in rows:
        outcome = worker(row, SimpleNamespace(job_id="job-A", scan_id="scan-A", agent_id="agent-A", seed=42))
        assert outcome.verdict == "pass" and outcome.numeric_result is None
        raw = next(tmp_path.rglob(row["case_id"] + ".json")).read_bytes()
        assert outcome.evidence_sha256 == hashlib.sha256(raw).hexdigest()
        observations.append({"test_id": row["test_id"], "probe_id": row["case_id"], "verdict": outcome.verdict,
            "evidence_ref": outcome.evidence_ref, "sha256": outcome.evidence_sha256,
            "case_version": row["version_pin"], "diagnostic_measurements": outcome.diagnostic_measurements})
    assert len(rows) == 5 and worker.ledger.target_calls == worker.ledger.judge_calls == 0
    report = build_standard_requirement_report({}, manifest={"evidence_rows": observations})
    inventory = report["evidence_inventory"]
    assert inventory["reported_case_count"] == inventory["with_reported_digest"] == 5
    assert all(o["method"] and o["diagnostic_measurements"] for o in inventory["artifacts"])
    assert all(c["requirement_conclusion"] == "not assessed" for f in report["frameworks"] for c in f["controls"])
