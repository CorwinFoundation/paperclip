"""BEAAA-33741: the evidence export a ledger binds from must be the job the refs name.

``_resolve_response`` documented that "the ref's job id must match the exported
job", but the only comparison was against a caller-supplied ``job_id`` that
``build_ledger`` defaults to ``None``. A root exported from a different job
therefore resolved every ref by its relative path and bound another run's
recorded responses. These tests pin the export's identity as the control.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.beaaa31092_content_slice import _resolve_response

REF_JOB = "e1332067-ac21-4711-9462-8abeeb0e2254"
OTHER_JOB = "16306e78-d18f-4c2d-b0c2-495e2d2256f4"
RELATIVE = "approval_preview_mutation_binding/bb18742.approval_preview_mutation_binding.01.json"
REF = f"evidence://{REF_JOB}/{RELATIVE}"


def _export(tmp_path: Path, job: str) -> Path:
    root = tmp_path / "evidence" / job
    source = root / RELATIVE
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"responses": ["the target said this"]}), encoding="utf-8")
    return root


def test_matching_export_binds_the_recorded_response(tmp_path: Path) -> None:
    response, refusal = _resolve_response(_export(tmp_path, REF_JOB), None, REF)
    assert refusal is None
    assert response == "the target said this"


def test_export_from_another_job_refuses_even_though_the_file_is_there(tmp_path: Path) -> None:
    """The regression. The file resolves by relative path; the job does not match."""
    root = _export(tmp_path, OTHER_JOB)
    assert (root / RELATIVE).is_file(), "the bug needs a readable file at the relative path"
    response, refusal = _resolve_response(root, None, REF)
    assert response is None
    assert refusal == f"evidence root is job {OTHER_JOB}, ref belongs to job {REF_JOB}"


def test_a_root_that_is_not_a_job_export_does_not_bind(tmp_path: Path) -> None:
    root = tmp_path / "evidence" / "latest"
    (root / RELATIVE).parent.mkdir(parents=True)
    (root / RELATIVE).write_text(json.dumps({"responses": ["stale"]}), encoding="utf-8")
    response, refusal = _resolve_response(root, None, REF)
    assert response is None
    assert refusal == f"evidence root is job latest, ref belongs to job {REF_JOB}"


def test_declared_job_id_is_still_checked_first(tmp_path: Path) -> None:
    """The pre-existing ref-versus-declaration check keeps its own refusal text."""
    response, refusal = _resolve_response(_export(tmp_path, REF_JOB), OTHER_JOB, REF)
    assert response is None
    assert refusal == f"evidence ref belongs to job {REF_JOB}"
