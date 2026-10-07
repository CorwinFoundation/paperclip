"""Human annotation queue exchange for Full Scan calibration (BEAAA-38923)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

SCHEMA_VERSION = "full-scan-human-annotation/0.1.0"
HUMAN_LABELS = {"pass", "fail", "uncertain", "not_applicable"}


class AnnotationError(ValueError):
    pass


@dataclass(frozen=True)
class AnnotationItem:
    item_id: str
    case_id: str
    family: str
    response: str
    evidence_ref: str
    model_suggestion: str | None = None


def build_queue(rows: Iterable[Mapping[str, object]]) -> list[AnnotationItem]:
    """Build stable queue items without representing model output as human gold."""
    items: list[AnnotationItem] = []
    for row in rows:
        case_id = str(row.get("case_id") or "").strip()
        family = str(row.get("family") or "").strip()
        response = str(row.get("response") or "").strip()
        evidence_ref = str(row.get("evidence_ref") or "").strip()
        if not all((case_id, family, response, evidence_ref)):
            raise AnnotationError("queue row requires case_id, family, response, and evidence_ref")
        item_id = hashlib.sha256(
            json.dumps([case_id, family, evidence_ref], separators=(",", ":")).encode()
        ).hexdigest()
        items.append(
            AnnotationItem(
                item_id=item_id,
                case_id=case_id,
                family=family,
                response=response,
                evidence_ref=evidence_ref,
                model_suggestion=(str(row["verdict"]) if row.get("verdict") else None),
            )
        )
    return items


def export_queue(items: Iterable[AnnotationItem], path: Path) -> None:
    records = [
        {"schema_version": SCHEMA_VERSION, "status": "pending_human", **asdict(item)}
        for item in items
    ]
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in records), encoding="utf-8")


def import_labels(path: Path, *, expected_item_ids: set[str]) -> list[dict[str, str]]:
    labels: list[dict[str, str]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("schema_version") != SCHEMA_VERSION:
            raise AnnotationError(f"line {line_number}: schema_version")
        if row.get("label_source") != "human":
            raise AnnotationError(f"line {line_number}: label_source must be human")
        item_id = str(row.get("item_id") or "")
        annotator_id = str(row.get("annotator_id") or "").strip()
        label = str(row.get("human_label") or "").strip().lower()
        if item_id not in expected_item_ids:
            raise AnnotationError(f"line {line_number}: unknown item_id")
        if not annotator_id:
            raise AnnotationError(f"line {line_number}: annotator_id")
        if label not in HUMAN_LABELS:
            raise AnnotationError(f"line {line_number}: human_label")
        labels.append({"item_id": item_id, "annotator_id": annotator_id, "human_label": label})
    return labels


def resolve_independent_labels(labels: Iterable[Mapping[str, str]]) -> dict[str, dict[str, object]]:
    """Require two independent humans; disagreement stays unresolved."""
    grouped: dict[str, dict[str, str]] = {}
    for row in labels:
        grouped.setdefault(row["item_id"], {})[row["annotator_id"]] = row["human_label"]
    resolved: dict[str, dict[str, object]] = {}
    for item_id, by_annotator in grouped.items():
        values = list(by_annotator.values())
        if len(values) < 2:
            resolved[item_id] = {"status": "pending_second_human", "human_label": None}
        elif len(set(values)) != 1:
            resolved[item_id] = {"status": "unresolved_disagreement", "human_label": None}
        else:
            resolved[item_id] = {"status": "resolved", "human_label": values[0]}
    return resolved
