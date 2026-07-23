"""Read-only v1.1 framework projection (BEAAA-16995)."""
from __future__ import annotations

import json
import os
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
    if not refs:
        return "not evaluated"
    verdicts = {str(ref.get("verdict", "")).lower() for ref in refs}
    if verdicts & {"fail", "failed"}:
        return "failed"
    if verdicts & {"pass", "passed"}:
        return "passed"
    if verdicts <= {"not_applicable", "not applicable", "n/a"}:
        return "not applicable"
    return "not evaluated"


def _reachable_ref(ref: Mapping[str, Any]) -> dict[str, Any] | None:
    uri = ref.get("artifact_uri")
    digest = ref.get("sha256") or ref.get("artifact_sha256")
    if not isinstance(uri, str) or not uri.strip() or not isinstance(digest, str) or not digest.strip():
        return None
    return {"rubric_point_id": ref.get("rubric_point_id"), "test_id": ref.get("test_id"),
            "artifact_uri": uri, "sha256": digest}


def build_framework_envelope(score: Mapping[str, Any], *, evaluation_date: str | None = None,
                             registry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    registry_data = dict(registry or load_registry())
    framework = registry_data["frameworks"][0]
    evidence = [ref for ref in score.get("evidence_refs", []) if isinstance(ref, Mapping)]
    mapped: set[int] = set()
    controls = []
    for control in framework["controls"]:
        prefixes = tuple(control["rubric_point_prefixes"])
        matched = [ref for ref in evidence if str(ref.get("rubric_point_id", "")).startswith(prefixes)]
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
    return result


def validate_claim_labels(value: Any) -> None:
    rendered = json.dumps(value, sort_keys=True).lower()
    for label in PROHIBITED_LABELS:
        if label in rendered:
            raise ValueError(f"prohibited framework claim label: {label}")
    statuses = {row["status"] for row in value["aiuc_1_mapped_technical_assessment"]["controls"]}
    if not statuses <= ALLOWED_STATUSES:
        raise ValueError("invalid AIUC-1-mapped assessment status")
