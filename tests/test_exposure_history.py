"""Exposure continuity, conservative clearing, scope and export evidence."""
from copy import deepcopy
import json

from exposure_history import build_exposure_history, fingerprint
from report_card.exposures import render_exposure_history

CONTEXT = {"target_fingerprint": "a"*64, "configuration_fingerprint": "b"*64}
SUBJECT = fingerprint(["tenant-A", "agent-A"])


def report(day, verdict=None, version="probe@1"):
    observations = [] if verdict is None else [{"test_id": "synthetic_workflow_revocation", "case_id": "SYN-WORKFLOW-REVOCATION",
        "reported_verdict": verdict, "status": "complete", "case_version": version, "artifact_uri": "evidence://scan/case.json", "sha256": "c"*64,
        "diagnostic_measurements": {"unsafe_attempt_count": int(verdict == "fail")}}]
    return {"scan_id": f"scan-{day}", "evaluation_date": f"2026-10-{day:02}T10:00:00+00:00",
            "evidence_inventory": {"artifacts": observations}}


def history(day, verdict=None, *, prior=None, context=CONTEXT, subject=SUBJECT, version="probe@1"):
    return build_exposure_history(report(day, verdict, version), prior=prior, context=context, subject_key=subject)


def test_new_persistent_cleared_reopened_preserve_first_last_seen_and_events():
    first = history(20, "fail")
    assert first["entries"][0]["exposure_id"] == "exposure.unapproved_consequential_action"
    assert first["entries"][0]["state"] == "newly_observed"
    persistent = history(21, "fail", prior=first)
    assert persistent["entries"][0]["state"] == "persistent"
    cleared = history(22, "pass", prior=persistent)
    assert cleared["entries"][0]["state"] == "cleared_on_comparable_retest"
    assert cleared["entries"][0]["exposure_open"] is False
    reopened = history(23, "fail", prior=cleared)
    entry = reopened["entries"][0]
    assert entry["state"] == "reopened" and entry["exposure_open"]
    assert entry["first_seen"] == first["evaluated_at"] and entry["last_seen"] == reopened["evaluated_at"]
    assert len(entry["events"]) == 4
    assert first["entries"][0]["state"] == "newly_observed"


def test_missing_unresolved_and_changed_conditions_cannot_clear_exposure():
    first = history(20, "fail")
    for verdict in (None, "unresolved", "skipped", "inconclusive"):
        current = history(21, verdict, prior=first)
        assert current["entries"][0]["state"] == "unobserved" and current["entries"][0]["exposure_open"]
    for context in ({}, {**CONTEXT, "configuration_fingerprint": "d"*64}):
        current = history(21, "pass", prior=first, context=context)
        assert current["entries"][0]["state"] == "not_comparable" and current["entries"][0]["exposure_open"]
    current = history(21, "pass", prior=first, version="probe@2")
    assert current["entries"][0]["state"] == "not_comparable"
    # A later matching retest can clear the original exposure after lost coverage.
    unknown = history(21, prior=first)
    assert history(22, "pass", prior=unknown)["entries"][0]["state"] == "cleared_on_comparable_retest"


def test_tenant_subject_future_and_same_scan_cannot_supply_prior_history():
    first = history(20, "fail")
    assert history(21, "pass", prior=first, subject=fingerprint(["tenant-B", "agent-A"]))["entries"] == []
    assert history(19, "pass", prior=first)["prior_scan_id"] is None
    assert history(20, "pass", prior=first)["prior_scan_id"] is None
    assert history(21, "pass", prior=first, subject=None)["prior_scan_id"] is None


def test_skipped_or_incomplete_case_cannot_clear_even_if_it_reports_pass():
    first = history(20, "fail")
    for status in (None, "skipped", "incomplete", "failed"):
        current = report(21, "pass")
        current["evidence_inventory"]["artifacts"][0]["status"] = status
        entry = build_exposure_history(current, prior=first, context=CONTEXT, subject_key=SUBJECT)["entries"][0]
        assert entry["exposure_open"] is True and entry["state"] == "unobserved"


def test_conflicting_results_do_not_clear_and_missing_version_cannot_clear():
    first = history(20, "fail")
    current = report(21, "pass")
    current["evidence_inventory"]["artifacts"].append({**current["evidence_inventory"]["artifacts"][0], "reported_verdict": "fail"})
    result = build_exposure_history(current, prior=first, context=CONTEXT, subject_key=SUBJECT)
    assert result["entries"][0]["state"] == "persistent"
    assert history(21, "pass", prior=first, version=None)["entries"][0]["state"] == "not_comparable"


def test_format_or_utility_failure_does_not_become_a_secret_egress_finding():
    source = report(20, "fail")
    row = source["evidence_inventory"]["artifacts"][0]
    row.update(test_id="synthetic_auxiliary_disclosure", case_id="SYN-AUXILIARY-DISCLOSURE",
        diagnostic_measurements={"private_marker_leak_count": 0, "unsupported_answer_count": 1})
    entry = build_exposure_history(source)["entries"][0]
    assert entry["exposure_id"] is None
    assert entry["candidate_exposure_id"] == "exposure.secret_egress"
    row["diagnostic_measurements"]["private_marker_leak_count"] = 1
    assert build_exposure_history(source)["entries"][0]["exposure_id"] == "exposure.secret_egress"


def test_public_history_contains_only_metadata_and_html_escapes_fields():
    source = report(20, "fail")
    source["evidence_inventory"]["artifacts"][0].update(response="PRIVATE-provider-secret", diagnostic_measurements={"private": "secret"})
    original = deepcopy(source)
    result = build_exposure_history(source, context=CONTEXT, subject_key=SUBJECT)
    assert source == original and "PRIVATE-provider-secret" not in json.dumps(result)
    result["entries"][0]["case_id"] = "<script>private</script>"
    assert "<script>" not in render_exposure_history(result)
    assert "&lt;script&gt;" in render_exposure_history(result)
    result["entries"][0]["events"][0]["scan_id"] = "<script>event</script>"
    html = render_exposure_history(result)
    assert "<script>" not in html and "&lt;script&gt;event&lt;/script&gt;" in html
    assert "newly observed" in html and "Recent observations" in html


def test_normal_scan_log_persists_scoped_history_and_manifest_refresh_keeps_prior():
    from dataclasses import replace
    from api.scan_handlers import ScanRuntime, _external_result_envelope
    from api.scan_log import ScanLog
    from tests.test_standard_requirement_reports import request, result
    manifests = [
        {"manifest_hash": "1"*64, "evidence_rows": [{"test_id": "synthetic_workflow_revocation",
            "probe_id": "SYN-WORKFLOW-REVOCATION", "verdict": verdict, "status": "complete",
            "case_version": "synthetic-target-exposure@1", "evidence_ref": "evidence://scan/case.json"}]}
        for verdict in ("fail", "pass")]
    times = iter(["2026-10-20T10:00:00+00:00", "2026-10-21T10:00:00+00:00"])
    def run(req, *_args):
        index = 0 if req.scan_id == "scan-first" else 1
        return replace(result(manifests[index]), scan_id=req.scan_id)
    runtime = ScanRuntime(run_scan_fn=run, scan_log=ScanLog(), run_timestamp_factory=lambda: next(times),
        score_envelope_factory=lambda *_: {"are_total": 72, "band": "B", "evidence_refs": []})
    config = {"transport": "openai_chat", "model": "qualification-target"}
    for identifier in ("scan-first", "scan-second"):
        runtime.kickoff(replace(request(), scan_id=identifier, endpoint_config=config), "secret", agent_id="agent-A")
    first = runtime.current_kickoff_row("scan-first")["score_json"]
    second = runtime.current_kickoff_row("scan-second")["score_json"]
    assert first["exposure_history"]["entries"][0]["state"] == "newly_observed"
    assert second["exposure_history"]["entries"][0]["state"] == "cleared_on_comparable_retest"
    changed = {**manifests[1], "manifest_hash": "2"*64}
    response = _external_result_envelope(replace(result(changed), scan_id="scan-second"), second)
    assert response["exposure_history"]["entries"][0]["state"] == "cleared_on_comparable_retest"
    assert "_exposure_history_prior" not in response
