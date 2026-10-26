"""Read-only v1.1 framework projection (BEAAA-16995)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

FEATURE_FLAG = "BACKBOND_FRAMEWORK_ENVELOPE_V1_1"
REGISTRY_PATH = Path(__file__).resolve().parent / "rubric" / "framework_registry.v1.json"
ALLOWED_STATUSES = {"passed", "failed", "not evaluated", "not applicable"}
PROHIBITED_LABELS = ("aiuc-certified", "aiuc certified", "aiuc-compliant", "aiuc compliant", "official score")


def enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(FEATURE_FLAG, "").strip().lower() in {"1", "true", "yes"}


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("registry_version") != "1.0.0":
        raise ValueError("unsupported framework registry version")
    return registry


def _status(refs: list[Mapping[str, Any]]) -> str:
    """Summarize supplied observations without promoting partial evidence."""
    if not refs:
        return "not evaluated"
    evidenced = [ref for ref in refs if _reachable_ref(ref) is not None]
    verdicts = {str(ref.get("verdict", "")).strip().lower() for ref in evidenced}
    if verdicts & {"fail", "failed"}:
        return "failed"
    if len(evidenced) != len(refs):
        return "not evaluated"
    if not verdicts <= {"pass", "passed", "not_applicable", "not applicable", "n/a"}:
        return "not evaluated"
    if verdicts & {"pass", "passed"}:
        return "passed"
    if verdicts <= {"not_applicable", "not applicable", "n/a"}:
        return "not applicable"
    return "not evaluated"


def _reference_digest(ref: Mapping[str, Any]) -> str | None:
    digest = ref.get("sha256") or ref.get("artifact_sha256")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return digest.lower()
    return None


def _reachable_ref(ref: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate reference metadata; byte retrieval/verification stays explicit."""
    uri = ref.get("artifact_uri")
    digest = _reference_digest(ref)
    if not isinstance(uri, str) or not uri.strip() or digest is None:
        return None
    return {"rubric_point_id": ref.get("rubric_point_id"), "test_id": ref.get("test_id"),
            "artifact_uri": uri.strip(), "sha256": digest}


def verify_evidence_bytes(ref: Mapping[str, Any], artifact_bytes: bytes) -> None:
    """Fail closed unless retrieved artifact bytes match the evidence reference."""
    expected = _reference_digest(ref)
    if expected is None:
        raise ValueError("evidence reference is missing a valid SHA-256 digest")
    actual = hashlib.sha256(artifact_bytes).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError("evidence artifact SHA-256 mismatch")


def build_framework_envelope(score: Mapping[str, Any], *, evaluation_date: str | None = None,
                             registry: Mapping[str, Any] | None = None,
                             requirement_plan: Mapping[str, Any] | None = None,
                             artifact_reader: Any = None) -> dict[str, Any]:
    registry_data = dict(registry or load_registry())
    framework = registry_data["frameworks"][0]
    evidence = [ref for ref in score.get("evidence_refs", []) if isinstance(ref, Mapping)]
    mapped: set[int] = set()
    controls = []
    for control in framework["controls"]:
        prefixes = tuple(control["rubric_point_prefixes"])
        families = set(control.get("test_ids", []))
        matched = [ref for ref in evidence if str(ref.get("rubric_point_id", "")).startswith(prefixes)
                   or ref.get("test_id") in families]
        mapped.update(id(ref) for ref in matched)
        controls.append({"control_id": control["control_id"], "status": _status(matched),
                         "evidence_refs": [p for ref in matched if (p := _reachable_ref(ref)) is not None]})
    date = evaluation_date or str(score.get("run_timestamp") or "")
    if not date:
        raise ValueError("evaluation_date is required")
    result = {
        "framework_registry_version": registry_data["registry_version"],
        "backbond_score": {"total": score.get("are_total"), "band": score.get("band"),
                           "subscores": score.get("subscores"), "scanner_version": score.get("scanner_version"),
                           "corpus_version": score.get("corpus_version")},
        "aiuc_1_mapped_technical_assessment": {
            "label": framework["display_name"], "mapping_version": framework["mapping_version"],
            "evaluation_date": date, "certification_status": "not certification",
            "limitations": list(framework["limitations"]), "controls": controls},
        "backbond_extensions": {"label": "BackBond Extensions",
                                "evidence_refs": [p for ref in evidence if id(ref) not in mapped
                                                  if (p := _reachable_ref(ref)) is not None]},
    }
    validate_claim_labels(result)
    # Additive evidence-only crosswalk. Without an operator-approved plan and
    # verified artifact bytes every new requirement remains not evaluated.
    from requirement_assessment import build_requirement_assessment
    timestamp = date + "T00:00:00+00:00" if len(date) == 10 else date
    result["requirements_evidence"] = build_requirement_assessment(
        score, plan=requirement_plan, artifact_reader=artifact_reader,
        evaluated_at=timestamp,
    )
    return result


def validate_claim_labels(value: Any) -> None:
    rendered = json.dumps(value, sort_keys=True).lower()
    for label in PROHIBITED_LABELS:
        if label in rendered:
            raise ValueError(f"prohibited framework claim label: {label}")
    statuses = {row["status"] for row in value["aiuc_1_mapped_technical_assessment"]["controls"]}
    if not statuses <= ALLOWED_STATUSES:
        raise ValueError("invalid AIUC-1-mapped assessment status")
