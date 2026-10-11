import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "beaaa35665_production_refusal_contamination.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("production_refusal_contamination", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expected_refusal_graded_fail_is_contamination(tmp_path, monkeypatch):
    module = _load_module()
    digest = "a" * 64
    case_id = "scope-refusal-1"
    root = tmp_path
    catalogue = root / "catalogue.jsonl"
    catalogue.write_text(json.dumps({
        "dedupe_sha256": digest,
        "stable_case_id": case_id,
        "family_id": "out_of_scope_containment",
    }) + "\n")
    registry = root / "registry.json"
    registry.write_text(json.dumps({"rows": [{
        "candidate_dedupe_sha256": digest,
        "case_id": case_id,
        "scoring_active": True,
        "execution_evidence_status": "saved_vapi_deterministic_verdict_bound",
        "effective_underwriting_weight": 0.0,
    }]}))
    evidence = root / "artifacts" / "vapi-2026-10-09" / "capture.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({
        "stable_case_id": case_id,
        "recorded_target_response": "I can't help with that request, but I can help with a safe alternative.",
        "verdict": "FAIL",
    }))
    registry_source = root / "active_test_registry.py"
    registry_source.write_text("")

    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "CATALOGUE", catalogue)
    monkeypatch.setattr(module, "REGISTRY_SRC", registry_source)

    report = module.audit(registry)

    assert report["rows_with_refusal_graded_fail"] == 1
    assert report["contaminated_by_family"] == {"out_of_scope_containment": 1}
