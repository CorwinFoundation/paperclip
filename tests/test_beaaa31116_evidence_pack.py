"""BEAAA-31116: the rank-2 evidence pack is fail-closed on every join it makes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.beaaa31116_evidence_pack import build_pack

JOB_ID = "a935915a-ddc0-473f-ba46-ebd3593cc38b"


def _case_record(**overrides):
    record = {
        "schema": "registered-case-evidence/v1",
        "job_id": JOB_ID,
        "scan_id": JOB_ID,
        "test_id": "tool_misuse_scope_escape",
        "case_id": "TOOL_MISUSE_SE-01",
        "target_model": "vapi:09aea361-04b3-4a62-bcb0-2f2582f11ec8@api.vapi.ai",
        "judge_model": "openrouter:judge",
        "seed": 42,
        "capability_manifest": {"tool_calls": True},
        "payloads": ["do the thing"],
        "responses": ["I can't do that outside the approved scope."],
        "verdict": "pass",
        "judge": {"type": "materialized_text"},
    }
    record.update(overrides)
    return record


@pytest.fixture()
def export(tmp_path: Path):
    """An extracted `export-evidence job <job-id>` tree plus its sha256 manifest."""

    def _build(records: dict[str, dict]) -> tuple[Path, dict]:
        job_dir = tmp_path / "evidence" / JOB_ID
        entries = []
        for relative, record in records.items():
            path = job_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(record).encode("utf-8")
            path.write_bytes(data)
            entries.append(
                {"path": relative, "type": "file", "sha256": hashlib.sha256(data).hexdigest()}
            )
        manifest = {
            "schema_version": "backbond-retained-evidence-export@1",
            "exported_at": "20260911T170000Z",
            "archive_sha256": "0" * 64,
            "entries": entries,
        }
        return job_dir, manifest

    return _build


def _ledger(*rows):
    return {
        "slice_name": "tool authority and transaction integrity",
        "slice_rank": 2,
        "inventory_sha256": "f" * 64,
        "source_manifest_sha256": "e" * 64,
        "rows": list(rows),
    }


def _ledger_row(case_id: str, ref: str | None, verdict: str | None, holds: list[str]):
    return {
        "stable_case_id": case_id,
        "family_id": "tool_misuse_scope_escape",
        "rubric_point_id": "P3.4",
        "dedupe_sha256": "a" * 64,
        "evidence_ref": ref,
        "verdict": verdict,
        "hold_reasons": holds,
    }


REF = f"evidence://{JOB_ID}/tool_misuse_scope_escape/TOOL_MISUSE_SE-01.json"
RELATIVE = "tool_misuse_scope_escape/TOOL_MISUSE_SE-01.json"


def test_evidenced_row_carries_response_verdict_and_provenance(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    pack = build_pack(
        _ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "pass", ["missing_recorded_target_response"])),
        job_dir,
        manifest,
        job_id=JOB_ID,
    )
    assert pack["schema_version"] == "beaaa-31116-evidence-pack/v1"
    assert pack["scored"] is False
    assert pack["counts"] == {
        "rows": 1,
        "evidenced": 1,
        "unevidenced": 0,
        "recovered_from_signed_manifest": 0,
        "verdict_disagreements": 0,
    }
    assert pack["rows"][0]["evidence_ref_source"] == "slice-ledger"

    row = pack["rows"][0]
    assert row["recorded_target_response"] == "I can't do that outside the approved scope."
    assert row["verdict"] == "pass"
    assert row["supported_capability"] is True
    assert row["capability_basis"] == "capability manifest present on the executed row"
    assert row["provenance"]["acquisition_basis"] == "target-measured"
    assert row["provenance"]["target_model"].startswith("vapi:")
    assert row["provenance"]["evidence_ref"] == REF
    assert row["provenance"]["evidence_sha256"] == manifest["entries"][0]["sha256"]


def test_multi_turn_responses_are_kept_per_turn_and_joined(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record(responses=["first", "", "second"])})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    row = pack["rows"][0]
    assert row["recorded_target_response_turns"] == ["first", "second"]
    assert row["recorded_target_response"] == "first\n\nsecond"


def test_row_without_an_evidence_ref_is_reported_not_invented(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    pack = build_pack(
        _ledger(_ledger_row("NO_EVIDENCE-01", None, None, ["missing_manifest_evidence"])),
        job_dir,
        manifest,
        job_id=JOB_ID,
    )
    row = pack["rows"][0]
    assert row["evidenced"] is False
    assert row["unevidenced_reason"] == "missing_manifest_evidence"
    assert row["recorded_target_response"] is None
    assert pack["counts"]["unevidenced"] == 1


def test_digest_mismatch_fails_the_row_closed(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    (job_dir / RELATIVE).write_text(json.dumps(_case_record(responses=["tampered"])), encoding="utf-8")
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    row = pack["rows"][0]
    assert row["evidenced"] is False
    assert row["unevidenced_reason"] == "evidence bytes do not match the export manifest digest"


def test_missing_file_and_unmanifested_file_are_distinguished(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    (job_dir / RELATIVE).unlink()
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["rows"][0]["unevidenced_reason"] == "evidence file missing from the export"

    job_dir, manifest = export({RELATIVE: _case_record()})
    manifest["entries"] = []
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["rows"][0]["unevidenced_reason"] == "evidence file is absent from the export manifest"


def test_empty_response_is_not_an_evidenced_row(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record(responses=["   "])})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["rows"][0]["unevidenced_reason"] == "evidence file records no target response"


def test_refs_for_another_job_or_outside_the_tree_are_refused(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    other = f"evidence://00000000-0000-0000-0000-000000000000/{RELATIVE}"
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", other, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    assert "belongs to job" in pack["rows"][0]["unevidenced_reason"]

    traversal = f"evidence://{JOB_ID}/../../etc/shadow"
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", traversal, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    assert "escapes" in pack["rows"][0]["unevidenced_reason"]


def test_verdict_disagreement_with_the_ledger_is_reported_not_silently_kept(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record(verdict="fail")})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "pass", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["counts"]["verdict_disagreements"] == 1
    assert pack["verdict_disagreements"] == ["TOOL_MISUSE_SE-01"]
    assert pack["rows"][0]["evidenced"] is True
    assert pack["rows"][0]["verdict"] == "fail"


def _signed_manifest(*rows):
    return {"evidence_rows": list(rows)}


def _manifest_row(digest: str, **overrides):
    row = {
        "probe_id": f"content-candidate:sha256:{digest}",
        "framework": "day1_report_only@day1-report-only-adapter/v0.1",
        "acquisition_basis": "target-measured",
        "evidence_ref": REF,
        "verdict": "unresolved",
        "status": "complete",
    }
    row.update(overrides)
    return row


def test_a_ref_the_slice_framework_filter_dropped_is_recovered(export) -> None:
    """BEAAA-31116: 12 of the 27 unevidenced rows ran under the day1 adapter, target-measured."""
    job_dir, manifest = export({RELATIVE: _case_record(verdict="unresolved")})
    digest = "b" * 64
    ledger = _ledger(_ledger_row("TOOL_MISUSE_PT-01", None, None, ["missing_manifest_evidence"]))
    ledger["rows"][0]["dedupe_sha256"] = digest
    pack = build_pack(
        ledger,
        job_dir,
        manifest,
        job_id=JOB_ID,
        signed_manifest=_signed_manifest(_manifest_row(digest)),
    )
    row = pack["rows"][0]
    assert row["evidenced"] is True
    assert row["evidence_ref"] == REF
    assert row["evidence_ref_source"] == "signed-manifest:day1_report_only@day1-report-only-adapter/v0.1"
    assert row["ledger_hold_reasons"] == ["missing_manifest_evidence"]
    assert pack["counts"]["recovered_from_signed_manifest"] == 1


def test_recovery_refuses_rows_the_manifest_does_not_call_target_measured(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    digest = "b" * 64
    ledger = _ledger(_ledger_row("TOOL_MISUSE_PT-01", None, None, ["missing_manifest_evidence"]))
    ledger["rows"][0]["dedupe_sha256"] = digest
    for basis in ("missing", None, "declared"):
        pack = build_pack(
            ledger, job_dir, manifest, job_id=JOB_ID,
            signed_manifest=_signed_manifest(_manifest_row(digest, acquisition_basis=basis)),
        )
        assert pack["rows"][0]["unevidenced_reason"] == "missing_manifest_evidence", basis
        assert pack["counts"]["recovered_from_signed_manifest"] == 0


def test_recovery_refuses_an_ambiguous_digest(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    digest = "b" * 64
    ledger = _ledger(_ledger_row("TOOL_MISUSE_PT-01", None, None, ["missing_manifest_evidence"]))
    ledger["rows"][0]["dedupe_sha256"] = digest
    other = f"evidence://{JOB_ID}/tool_misuse_param_tamper/TOOL_MISUSE_PT-09.json"
    pack = build_pack(
        ledger, job_dir, manifest, job_id=JOB_ID,
        signed_manifest=_signed_manifest(_manifest_row(digest), _manifest_row(digest, evidence_ref=other)),
    )
    assert pack["rows"][0]["evidenced"] is False
    assert "more than one evidence ref" in pack["rows"][0]["unevidenced_reason"]


def test_evidence_pack_refuses_the_exact_self_disowning_30720_manifest(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    signed = json.loads(
        (Path(__file__).parents[1] / "artifacts/beaaa-30720-manifest.json").read_text()
    )
    with pytest.raises(ValueError, match="declares itself unfit"):
        build_pack(_ledger(), job_dir, manifest, job_id=JOB_ID, signed_manifest=signed)


def test_recovered_rows_still_fail_closed_on_a_digest_mismatch(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    (job_dir / RELATIVE).write_text(json.dumps(_case_record(responses=["tampered"])), encoding="utf-8")
    digest = "b" * 64
    ledger = _ledger(_ledger_row("TOOL_MISUSE_PT-01", None, None, ["missing_manifest_evidence"]))
    ledger["rows"][0]["dedupe_sha256"] = digest
    pack = build_pack(
        ledger, job_dir, manifest, job_id=JOB_ID,
        signed_manifest=_signed_manifest(_manifest_row(digest)),
    )
    assert pack["rows"][0]["unevidenced_reason"] == "evidence bytes do not match the export manifest digest"


def test_without_a_signed_manifest_nothing_is_recovered(export) -> None:
    job_dir, manifest = export({RELATIVE: _case_record()})
    pack = build_pack(
        _ledger(_ledger_row("NO_EVIDENCE-01", None, None, ["missing_manifest_evidence"])),
        job_dir, manifest, job_id=JOB_ID,
    )
    assert pack["rows"][0]["unevidenced_reason"] == "missing_manifest_evidence"
    assert pack["counts"]["recovered_from_signed_manifest"] == 0


def test_a_gated_out_row_is_not_reported_as_capability_supported(export) -> None:
    """A not-applicable row carries an applicability block; that block is the gate, not support."""
    gated = _case_record(
        verdict="not_applicable",
        capability_manifest=None,
        judge={
            "type": "deterministic_applicability_gate",
            "applicable": False,
            "applicability_reason": "tool_calls undeclared by the target",
            "applicability": {"flag": "tool_calls", "declaration": "undeclared", "attested": False},
            "reported_not_scored": True,
        },
    )
    job_dir, manifest = export({RELATIVE: gated})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, None, [])), job_dir, manifest, job_id=JOB_ID)
    row = pack["rows"][0]
    assert row["evidenced"] is True
    assert row["supported_capability"] is False
    assert row["capability_basis"] == "tool_calls undeclared by the target"


def test_an_unmet_capability_criterion_is_not_capability_support(export) -> None:
    record = _case_record(capability_manifest={"tool_calls": True}, unmet_criteria=["capability_manifest"])
    job_dir, manifest = export({RELATIVE: record})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "fail", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["rows"][0]["supported_capability"] is False
    assert pack["rows"][0]["capability_basis"] == "judge reported capability_manifest unmet"


def test_capability_support_is_unknown_rather_than_invented(export) -> None:
    record = _case_record(capability_manifest=None, judge={"type": "materialized_text"})
    job_dir, manifest = export({RELATIVE: record})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "fail", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["rows"][0]["supported_capability"] is None
    assert "no applicability gate" in pack["rows"][0]["capability_basis"]


def test_an_applicable_gate_is_capability_support(export) -> None:
    record = _case_record(capability_manifest=None, judge={"type": "deterministic_applicability_gate", "applicable": True})
    job_dir, manifest = export({RELATIVE: record})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "fail", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["rows"][0]["supported_capability"] is True


def test_a_bare_case_id_probe_row_is_recovered_by_stable_case_id(export) -> None:
    """BEAAA-31116: the promptfoo lane keys probe_id by stable case id, not by digest."""
    job_dir, manifest = export({RELATIVE: _case_record(verdict="FAIL")})
    ledger = _ledger(_ledger_row("cac-01", None, None, ["missing_manifest_evidence"]))
    signed = _signed_manifest(
        _manifest_row("ignored", probe_id="cac-01", framework="promptfoo@0.115.0", verdict="FAIL")
    )
    pack = build_pack(ledger, job_dir, manifest, job_id=JOB_ID, signed_manifest=signed)
    row = pack["rows"][0]
    assert row["evidenced"] is True
    assert row["evidence_ref_source"] == "signed-manifest:promptfoo@0.115.0"
    assert pack["counts"]["recovered_from_signed_manifest"] == 1


def test_verdict_comparison_ignores_case(export) -> None:
    """The promptfoo lane writes PASS/FAIL, the content-candidate lane pass/fail."""
    job_dir, manifest = export({RELATIVE: _case_record(verdict="FAIL")})
    pack = build_pack(_ledger(_ledger_row("TOOL_MISUSE_SE-01", REF, "fail", [])), job_dir, manifest, job_id=JOB_ID)
    assert pack["counts"]["verdict_disagreements"] == 0
    assert pack["rows"][0]["verdict"] == "FAIL"
