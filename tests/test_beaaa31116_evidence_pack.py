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
    assert pack["counts"] == {"rows": 1, "evidenced": 1, "unevidenced": 0, "verdict_disagreements": 0}

    row = pack["rows"][0]
    assert row["recorded_target_response"] == "I can't do that outside the approved scope."
    assert row["verdict"] == "pass"
    assert row["supported_capability"] is True
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
