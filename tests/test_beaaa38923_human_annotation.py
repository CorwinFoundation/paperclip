import json

import pytest

from harness.human_annotation import (
    AnnotationError,
    SCHEMA_VERSION,
    build_queue,
    export_queue,
    import_labels,
    resolve_independent_labels,
)


def _item():
    return build_queue([{"case_id": "c1", "family": "prompt_leak", "response": "answer", "evidence_ref": "evidence://s/c1.json", "verdict": "fail"}])[0]


def test_export_keeps_model_suggestion_separate_from_human_label(tmp_path):
    item = _item()
    path = tmp_path / "queue.jsonl"
    export_queue([item], path)
    row = json.loads(path.read_text())
    assert row["status"] == "pending_human"
    assert row["model_suggestion"] == "fail"
    assert "human_label" not in row


def test_import_requires_explicit_human_provenance(tmp_path):
    item = _item()
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "item_id": item.item_id, "annotator_id": "h1", "label_source": "model", "human_label": "fail"}) + "\n")
    with pytest.raises(AnnotationError, match="label_source must be human"):
        import_labels(path, expected_item_ids={item.item_id})


def test_independent_disagreement_is_unresolved_and_na_is_not_pass(tmp_path):
    item = _item()
    path = tmp_path / "labels.jsonl"
    rows = [
        {"schema_version": SCHEMA_VERSION, "item_id": item.item_id, "annotator_id": "h1", "label_source": "human", "human_label": "pass"},
        {"schema_version": SCHEMA_VERSION, "item_id": item.item_id, "annotator_id": "h2", "label_source": "human", "human_label": "not_applicable"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    labels = import_labels(path, expected_item_ids={item.item_id})
    assert resolve_independent_labels(labels)[item.item_id] == {"status": "unresolved_disagreement", "human_label": None}


def test_two_matching_independent_humans_resolve():
    item = _item()
    result = resolve_independent_labels([
        {"item_id": item.item_id, "annotator_id": "h1", "human_label": "fail"},
        {"item_id": item.item_id, "annotator_id": "h2", "human_label": "fail"},
    ])
    assert result[item.item_id] == {"status": "resolved", "human_label": "fail"}
