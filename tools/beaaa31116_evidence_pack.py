"""Build the BEAAA-31116 machine-readable evidence pack for the rank-2 roster.

The BEAAA-31092 rank-2 ledger carries verdicts and ``evidence://`` refs but no
recorded target responses.  The responses live in the per-case evidence files
written by ``tools/registered_case_executor.py`` under
``/var/lib/are-scanner/evidence/<job-id>/<test-id>/<case-id>.json`` and reach this
seat through ``scanner-deploy export-evidence job <job-id>``.

This tool joins the two and fails closed: a row is only reported as evidenced when
its evidence file is present, its bytes match the export manifest digest, the file
belongs to the expected job, and it carries a non-empty target response.  It never
scores and never touches weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tools.beaaa31092_content_slice import _assert_manifest_fit_to_bind

PACK_SCHEMA = "beaaa-31116-evidence-pack/v1"
CONTENT_CANDIDATE_PREFIX = "content-candidate:sha256:"
EVIDENCE_SCHEME = "evidence://"
ACQUISITION_BASIS = "target-measured"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _split_ref(evidence_ref: str) -> tuple[str, str]:
    if not evidence_ref.startswith(EVIDENCE_SCHEME):
        raise ValueError(f"unsupported evidence ref: {evidence_ref}")
    remainder = evidence_ref[len(EVIDENCE_SCHEME):]
    job_id, _, relative = remainder.partition("/")
    if not job_id or not relative:
        raise ValueError(f"evidence ref has no job-scoped path: {evidence_ref}")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"evidence ref escapes its job directory: {evidence_ref}")
    return job_id, relative


def _capability_support(record: dict[str, Any]) -> tuple[bool | None, str]:
    """Read capability support off the record's own gate, never off a guess.

    ``tools/registered_case_executor.py`` records a deterministic applicability gate
    (``judge.applicable`` plus ``judge.applicability`` from
    ``evals.aiuc_path_b.applicability.resolve``) and, for judged cases, an
    ``unmet_criteria`` list restricted to ``behavior`` and ``capability_manifest``.
    A gated-out row carries an ``applicability`` block too, so the presence of that
    block says the gate ran, not that the capability is supported.
    """
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    unmet = record.get("unmet_criteria")
    unmet = list(unmet) if isinstance(unmet, (list, tuple)) else []

    if judge.get("applicable") is False or record.get("verdict") == "not_applicable":
        reason = judge.get("applicability_reason") or "deterministic applicability gate: not applicable"
        return False, str(reason)
    if "capability_manifest" in unmet:
        return False, "judge reported capability_manifest unmet"
    if judge.get("applicable") is True:
        return True, "deterministic applicability gate: applicable"
    if record.get("capability_manifest"):
        return True, "capability manifest present on the executed row"
    return None, "no applicability gate or capability manifest in the record"


def _response_turns(record: dict[str, Any]) -> list[str]:
    responses = record.get("responses")
    if not isinstance(responses, list):
        return []
    return [turn for turn in (str(value or "").strip() for value in responses) if turn]


def _manifest_index(signed_manifest: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """Index the signed run manifest across every framework.

    Adapters key ``probe_id`` differently: the content-candidate lane writes
    ``content-candidate:sha256:<dedupe digest>``, the promptfoo lane writes the bare
    stable case id.  Indexing the stripped value covers both, and lookup tries the
    ledger row's digest first and then its stable case id.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for row in (signed_manifest or {}).get("evidence_rows", []):
        probe_id = str(row.get("probe_id") or "")
        digest = probe_id.removeprefix(CONTENT_CANDIDATE_PREFIX)
        if digest:
            index.setdefault(digest, []).append(row)
    return index


def _recover_ref(manifest_rows: list[dict[str, Any]]) -> tuple[str | None, str | None, str | None]:
    """Return (evidence_ref, source, refusal) for a ledger row the slice filter dropped.

    The BEAAA-31092 slice keeps only ``content_candidate@beaaa-18720-1.0.0`` rows, so a case
    executed under another adapter reads as unevidenced even though the signed manifest holds a
    target-measured ref for it.  Recovery is allowed only when the manifest itself says the row
    was target-measured and names exactly one ref.
    """
    measured = [
        row
        for row in manifest_rows
        if row.get("evidence_ref") and row.get("acquisition_basis") == "target-measured"
    ]
    if not measured:
        return None, None, None
    refs = {str(row["evidence_ref"]) for row in measured}
    if len(refs) > 1:
        return None, None, "signed manifest names more than one evidence ref for this digest"
    row = measured[0]
    return str(row["evidence_ref"]), f"signed-manifest:{row.get('framework')}", None


def build_pack(
    ledger: dict[str, Any],
    job_dir: Path,
    export_manifest: dict[str, Any],
    *,
    job_id: str,
    signed_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if signed_manifest is not None:
        _assert_manifest_fit_to_bind(signed_manifest)
    digests = {
        entry["path"]: entry["sha256"]
        for entry in export_manifest.get("entries", [])
        if entry.get("type") == "file"
    }
    root = job_dir.resolve(strict=True)
    manifest_index = _manifest_index(signed_manifest)
    rows: list[dict[str, Any]] = []

    for ledger_row in ledger["rows"]:
        row: dict[str, Any] = {
            "stable_case_id": ledger_row["stable_case_id"],
            "family_id": ledger_row["family_id"],
            "rubric_point_id": ledger_row["rubric_point_id"],
            "dedupe_sha256": ledger_row["dedupe_sha256"],
            "evidence_ref": ledger_row.get("evidence_ref"),
            "evidence_ref_source": "slice-ledger" if ledger_row.get("evidence_ref") else None,
            "ledger_verdict": ledger_row.get("verdict"),
            "ledger_hold_reasons": list(ledger_row.get("hold_reasons") or []),
            "recorded_target_response": None,
            "recorded_target_response_turns": None,
            "verdict": None,
            "supported_capability": None,
            "capability_basis": None,
            "provenance": None,
            "evidenced": False,
            "unevidenced_reason": None,
        }
        ref = ledger_row.get("evidence_ref")
        if not ref:
            candidates = manifest_index.get(ledger_row["dedupe_sha256"]) or manifest_index.get(
                ledger_row["stable_case_id"], []
            )
            ref, source, refusal = _recover_ref(candidates)
            if refusal:
                row["unevidenced_reason"] = refusal
                rows.append(row)
                continue
            if not ref:
                row["unevidenced_reason"] = "missing_manifest_evidence"
                rows.append(row)
                continue
            row["evidence_ref"] = ref
            row["evidence_ref_source"] = source
        try:
            ref_job, relative = _split_ref(ref)
        except ValueError as error:
            row["unevidenced_reason"] = str(error)
            rows.append(row)
            continue
        if ref_job != job_id:
            row["unevidenced_reason"] = f"evidence ref belongs to job {ref_job}, exported job is {job_id}"
            rows.append(row)
            continue

        source = (root / relative).resolve()
        if root not in source.parents:
            row["unevidenced_reason"] = "evidence path escapes the exported job directory"
            rows.append(row)
            continue
        if not source.is_file() or source.is_symlink():
            row["unevidenced_reason"] = "evidence file missing from the export"
            rows.append(row)
            continue

        data = source.read_bytes()
        actual = _sha256(data)
        expected = digests.get(relative)
        if expected is None:
            row["unevidenced_reason"] = "evidence file is absent from the export manifest"
            rows.append(row)
            continue
        if expected != actual:
            row["unevidenced_reason"] = "evidence bytes do not match the export manifest digest"
            rows.append(row)
            continue

        record = json.loads(data.decode("utf-8"))
        turns = _response_turns(record)
        if not turns:
            row["unevidenced_reason"] = "evidence file records no target response"
            rows.append(row)
            continue

        supported, capability_basis = _capability_support(record)
        row.update(
            {
                "recorded_target_response": "\n\n".join(turns),
                "recorded_target_response_turns": turns,
                "verdict": record.get("verdict"),
                "supported_capability": supported,
                "capability_basis": capability_basis,
                "evidenced": True,
                "provenance": {
                    "acquisition_basis": ACQUISITION_BASIS,
                    "job_id": record.get("job_id"),
                    "scan_id": record.get("scan_id"),
                    "target_model": record.get("target_model"),
                    "judge_model": record.get("judge_model"),
                    "seed": record.get("seed"),
                    "evidence_ref": ref,
                    "evidence_sha256": actual,
                    "export_archive_sha256": export_manifest.get("archive_sha256"),
                    "exported_at": export_manifest.get("exported_at"),
                },
            }
        )
        if record.get("job_id") not in (None, job_id):
            row["evidenced"] = False
            row["unevidenced_reason"] = "evidence file reports a different job id"
        rows.append(row)

    evidenced = [row for row in rows if row["evidenced"]]
    recovered = [row for row in evidenced if row["evidence_ref_source"] != "slice-ledger"]
    verdict_disagreements = [
        row["stable_case_id"]
        for row in evidenced
        if row["ledger_verdict"] is not None
        and str(row["verdict"]).casefold() != str(row["ledger_verdict"]).casefold()
    ]
    return {
        "schema_version": PACK_SCHEMA,
        "slice_name": ledger.get("slice_name"),
        "slice_rank": ledger.get("slice_rank"),
        "source_ledger_sha256": ledger.get("inventory_sha256"),
        "source_manifest_sha256": ledger.get("source_manifest_sha256"),
        "job_id": job_id,
        "scored": False,
        "counts": {
            "rows": len(rows),
            "evidenced": len(evidenced),
            "unevidenced": len(rows) - len(evidenced),
            "recovered_from_signed_manifest": len(recovered),
            "verdict_disagreements": len(verdict_disagreements),
        },
        "verdict_disagreements": sorted(verdict_disagreements),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True, help="extracted evidence/<job-id> directory")
    parser.add_argument("--export-manifest", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument(
        "--signed-manifest",
        type=Path,
        default=None,
        help="BEAAA-30720 run manifest, used to recover refs the slice framework filter dropped",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--require-evidenced",
        type=int,
        default=None,
        help="fail closed unless at least this many rows are evidenced",
    )
    args = parser.parse_args()

    pack = build_pack(
        json.loads(args.ledger.read_text(encoding="utf-8")),
        args.job_dir,
        json.loads(args.export_manifest.read_text(encoding="utf-8")),
        job_id=args.job_id,
        signed_manifest=(
            json.loads(args.signed_manifest.read_text(encoding="utf-8"))
            if args.signed_manifest is not None
            else None
        ),
    )
    if args.require_evidenced is not None and pack["counts"]["evidenced"] < args.require_evidenced:
        raise SystemExit(
            f"evidenced {pack['counts']['evidenced']} rows, required {args.require_evidenced}"
        )
    args.out.write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(pack["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
