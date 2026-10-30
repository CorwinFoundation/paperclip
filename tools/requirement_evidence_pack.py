"""Protected local assessor packs, reusing approved-plan reconciliation.

Only integrity-checked, plan-bound observation/document bytes are copied.
No network, target calls, automatic applicability or certification decisions.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from requirement_assessment import _artifact, build_requirement_assessment, directory_reader

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_PACK_BYTES = 64 * 1024 * 1024


def _json(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()


def _csv(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def export_pack(*, plan, bundle, artifact_directory, output, evaluated_at=None):
    """Create a new ZIP; preserve failures, missing evidence and original bytes."""
    reader = directory_reader(Path(artifact_directory))
    assessment = build_requirement_assessment(bundle, plan=plan, artifact_reader=reader, evaluated_at=evaluated_at)
    if assessment["scope_status"] != "matched":
        raise ValueError("a currently approved, matching scope is required")
    artifacts, roles, controls, documents = {}, {}, [], []
    history_sources = []

    def include(ref, role):
        raw = _artifact(ref, reader)  # recheck bytes after reconciliation
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds pack limit")
        digest = hashlib.sha256(raw).hexdigest()
        artifacts[digest] = raw
        roles.setdefault(digest, set()).add(role)
        if sum(map(len, artifacts.values())) > MAX_PACK_BYTES:
            raise ValueError("pack exceeds uncompressed artifact limit")

    rejected_indexes = {r["index"] for r in assessment["rejected_evidence"] if "index" in r}
    for i, ref in enumerate(bundle.get("evidence_refs", [])):
        if i not in rejected_indexes:
            include(ref, "scoped_observation")
            if "source_registered_case" in ref:
                include(ref["source_registered_case"], "original_registered_case")
                history_sources.append((json.loads(_artifact(ref["source_registered_case"], reader)), ref))

    for framework in assessment["frameworks"]:
        for c in framework["controls"]:
            controls.append({"framework_id": framework["framework_id"], "mapping_version": framework["mapping_version"],
                "control_id": c["control_id"], "applicability": c["applicability"], "technical_status": c["technical_status"],
                "requirement_conclusion": c["requirement_conclusion"], "evidence_complete_for_plan": c["evidence_complete_for_plan"],
                "missing_trials": len(c["missing_tests"]), "issues": "; ".join(c["issues"])})
            for ref in c["evidence_refs"]:
                include(ref, "observation")
                if "source_registered_case" in ref:
                    include(ref["source_registered_case"], "original_registered_case")
            for document_id in c["required_document_ids"]:
                accepted = c["applicability"] == "applicable" and document_id not in c["missing_documents"]
                if accepted:
                    matches = [d for d in bundle.get("documents", []) if d.get("document_id") == document_id]
                    # Scope, digest, reviewer and review time were checked above.
                    if len(matches) != 1:
                        raise ValueError("document changed during export")
                    include(matches[0], "reviewed_document")
                documents.append({"framework_id": framework["framework_id"], "control_id": c["control_id"],
                    "document_id": document_id, "applicability": c["applicability"],
                    "status": "reviewed bytes included" if accepted else
                              "excluded by declared applicability" if c["applicability"] == "not applicable" else "review required"})

    # Verify the assessment again using only the bytes being exported. The ZIP
    # cannot claim a result that depends on an omitted or subsequently changed file.
    def copied_reader(ref):
        return artifacts[str(ref.get("sha256") or ref.get("artifact_sha256")).lower()]
    copied = build_requirement_assessment(bundle, plan=plan, artifact_reader=copied_reader,
                                          evaluated_at=assessment["evaluation_date"])
    # Rejected/tampered bytes are deliberately absent from the pack. Their
    # diagnostic exception class can differ from a missing-file rejection;
    # every accepted reference, control decision and rejected index must agree.
    copied_rejections = {r.get("index") for r in copied["rejected_evidence"]}
    original_rejections = {r.get("index") for r in assessment["rejected_evidence"]}
    if ({k: v for k, v in copied.items() if k != "rejected_evidence"}
            != {k: v for k, v in assessment.items() if k != "rejected_evidence"}
            or copied_rejections != original_rejections):
        raise ValueError("assessment is not reproducible from exported bytes")
    index = {"schema": "requirement-evidence-pack/v1", "assessment_id": plan["assessment_id"],
        "scope": plan["scope"], "evaluated_at": assessment["evaluation_date"],
        "conformity_status": "not assessed", "certification_status": "not certification",
        "approval_identity_status": assessment["approval_identity_status"],
        "artifact_integrity": "local bytes verified; collection authenticity and documentary sufficiency require assessor review",
        "reproduction": "Control decisions, accepted references and rejected indexes reproduce from packaged bytes; original rejection diagnostics are retained.",
        "artifacts": [{"path": f"artifacts/{digest}.json", "sha256": digest,
                       "bytes": len(raw), "roles": sorted(roles[digest])} for digest, raw in sorted(artifacts.items())],
        "rejected_reference_count": len(assessment["rejected_evidence"]),
        "limits": {"max_artifact_bytes": MAX_ARTIFACT_BYTES, "max_pack_artifact_bytes": MAX_PACK_BYTES},
        "disclosure": "Operator export may contain private transcripts and documents; share through the customer's authorized assessment channel."}
    members = {"plan.json": _json(plan), "bundle.json": _json(bundle), "assessment.json": _json(assessment),
               "index.json": _json(index), "controls.csv": _csv(list(controls[0]), controls),
               "documents.csv": _csv(["framework_id", "control_id", "document_id", "applicability", "status"], documents)}
    from exposure_history import history_from_registered_sources
    history = history_from_registered_sources(history_sources, approved_scope=plan["scope"])
    members["exposure-history.json"] = _json(history)
    members["exposure-history.csv"] = _csv(
        ["exposure_id", "test_id", "case_id", "state", "first_seen", "last_seen", "comparison_status"],
        [{k: entry.get(k) for k in ("exposure_id", "test_id", "case_id", "state", "first_seen", "last_seen", "comparison_status")}
         for subject in history["subjects"] for entry in subject["entries"]],
    )
    members.update({f"artifacts/{digest}.json": raw for digest, raw in artifacts.items()})
    members["CHECKSUMS.json"] = _json({name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(members.items())})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".evidence-pack-", suffix=".partial", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
                for name, raw in sorted(members.items()):
                    archive.writestr(name, raw)
        # Atomic no-overwrite publication, including when another export races.
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return index
