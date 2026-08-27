"""Immutable input contract for the bounded BEAAA-25646 C10 capture slice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness import c10_capture


ROOT = Path(__file__).resolve().parents[1]
ROSTER = ROOT / "artifacts" / "BEAAA-25646" / "c10-roster-v1.0.json"
LEDGER = ROOT / "artifacts" / "BEAAA-25646" / "c10-mapping-ledger-v1.0.jsonl"
ROSTER_SHA256 = "e0046d6698aaa2ae44418a8d2bdf91098e87d19f31500a2d86500adde7402976"
LEDGER_SHA256 = "c0be24afea3ed424be9d9bddad96815dcf17ff3e58ef5f5f238839ff9af35184"


@pytest.fixture(autouse=True)
def authoritative_target_metadata(monkeypatch, tmp_path) -> None:
    """Give every unit test an independently provisioned symbolic binding."""
    metadata = tmp_path / "BEAAA-25646.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "consumer_ticket": "BEAAA-25646",
                "binding_kind": "symbolic_reference",
                "target_source_revision": c10_capture.TARGET_SOURCE_REVISION,
                "values_disclosed": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(c10_capture, "TARGET_METADATA", metadata)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exact_c10_roster_and_mapping_are_immutably_bound() -> None:
    assert _sha256(ROSTER) == ROSTER_SHA256
    assert _sha256(LEDGER) == LEDGER_SHA256

    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines()]
    assert len(roster) == len(ledger) == 172

    roster_ids = [row["stable_case_id"] for row in roster]
    ledger_ids = [row["stable_case_id"] for row in ledger]
    assert roster_ids == ledger_ids
    assert len(set(roster_ids)) == 172
    assert all(row["rubric_point_id"] for row in ledger)
    assert all(row["reporting_status"] == "reported_not_scored" for row in ledger)
    assert all(row["score_input"] is False for row in ledger)


def test_c10_preflight_fails_closed_without_scoped_target(monkeypatch) -> None:
    for name in c10_capture.REQUIRED_TARGET_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(c10_capture.CaptureError, match="missing production-like target"):
        c10_capture.preflight()


def _evidence() -> dict[str, object]:
    return {
        "stable_case_id": "c10-case-1",
        "recorded_target_response": "real target response",
        "verdict": "OBSERVED_REPORTED_NOT_SCORED",
        "verdict_provenance": "candidate-worker:v1",
        "runner_identity": "eval_framework_capture",
        "target_identity": c10_capture.TARGET_IDENTITY,
        "target_source_revision": c10_capture.TARGET_SOURCE_REVISION,
        "target_metadata": {
            "target_identity": c10_capture.TARGET_IDENTITY,
            "target_source_revision": c10_capture.TARGET_SOURCE_REVISION,
            "target_model": "qwen2.5:3b",
            "target_model_immutable_id": c10_capture.TARGET_MODEL_IMMUTABLE_ID,
            "target_runtime": c10_capture.TARGET_RUNTIME,
            "response_id": "chatcmpl-structural-test",
            "response_model": "qwen2.5:3b",
        },
        "executed_at": "2026-08-23T16:00:00Z",
        "artifact_ref": "sha256:" + "a" * 64,
    }


@pytest.mark.parametrize("field", ["recorded_target_response", "verdict_provenance"])
def test_validator_rejects_missing_execution_proof(field) -> None:
    evidence = _evidence()
    evidence[field] = ""
    with pytest.raises(c10_capture.CaptureError, match="malformed execution evidence"):
        c10_capture.validate_execution_evidence(
            evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
        )


@pytest.mark.parametrize("field,value", [
    ("target_identity", "fixture"),
    ("target_source_revision", "sha256:fixture"),
    ("target_model_immutable_id", "fixture"),
    ("target_runtime", "fixture"),
    ("response_id", ""),
    ("response_model", "fixture-model"),
])
def test_validator_rejects_fixture_or_mismatched_target_provenance(field, value) -> None:
    evidence = _evidence()
    evidence["target_metadata"][field] = value
    with pytest.raises(c10_capture.CaptureError, match="provenance|response identity"):
        c10_capture.validate_execution_evidence(
            evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
        )


@pytest.mark.parametrize("response", [
    "Replace the placeholder secret before deployment.",
    "This fixture demonstrates the vulnerable query construction.",
    "Do not use fabricated credentials in production.",
])
def test_validator_accepts_production_like_content_that_mentions_fixture_terms(response) -> None:
    evidence = _evidence()
    evidence["recorded_target_response"] = response
    c10_capture.validate_execution_evidence(
        evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
    )


def test_request_rejects_untrusted_openai_shaped_response(monkeypatch) -> None:
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return json.dumps({
                "id": "untrusted-response-id",
                "model": "qwen2.5:3b",
                "created": 1,
                "choices": [{"message": {"role": "assistant", "content": "arbitrary"}}],
            }).encode()

    monkeypatch.setenv("SCANNER_TARGET_URL", "https://target.invalid")
    monkeypatch.setenv("SCANNER_TARGET_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("SCANNER_TARGET_KEY", "test-only")
    monkeypatch.setattr(c10_capture.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(c10_capture.CaptureError, match="verdict provenance metadata"):
        c10_capture._request_case({"prompt_turn_sequence": ["test"]})


def test_request_preserves_existing_api_contract_and_target_provenance(monkeypatch) -> None:
    captured = {}
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "legitimate output"}}],
                "backbond_metadata": {
                    "target_identity": c10_capture.TARGET_IDENTITY,
                    "target_source_revision": c10_capture.TARGET_SOURCE_REVISION,
                    "target_model": "qwen2.5:3b",
                    "target_model_immutable_id": c10_capture.TARGET_MODEL_IMMUTABLE_ID,
                    "target_runtime": c10_capture.TARGET_RUNTIME,
                    "response_id": "chatcmpl-production-like",
                    "response_model": "qwen2.5:3b",
                },
            }).encode()

    def urlopen(request, **kwargs):
        captured.update(json.loads(request.data))
        return Response()
    monkeypatch.setenv("SCANNER_TARGET_URL", "https://target.invalid")
    monkeypatch.setenv("SCANNER_TARGET_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("SCANNER_TARGET_KEY", "test-only")
    monkeypatch.setattr(c10_capture.urllib.request, "urlopen", urlopen)
    response, metadata = c10_capture._request_case({"prompt_turn_sequence": ["one", "two"]})
    assert captured["messages"] == [{"role": "user", "content": "one\n\ntwo"}]
    assert "prompt" not in captured
    assert captured["model"] == "qwen2.5:3b"
    assert captured["temperature"] == 0
    assert captured["seed"] == 0
    assert response == "legitimate output"
    assert metadata["target_source_revision"] == c10_capture.TARGET_SOURCE_REVISION


@pytest.mark.parametrize("choices", [
    None,
    {},
    [],
    [{"message": {"role": "assistant", "content": "one"}},
     {"message": {"role": "assistant", "content": "two"}}],
    [None],
    [{}],
    [{"message": None}],
    [{"message": {}}],
    [{"message": {"content": None}}],
    [{"message": {"content": []}}],
    [{"message": {"content": ""}}],
    [{"message": {"content": "   "}}],
])
def test_request_fails_closed_on_invalid_openai_content(monkeypatch, choices) -> None:
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return json.dumps({
                "choices": choices,
                "backbond_metadata": {
                    "target_identity": c10_capture.TARGET_IDENTITY,
                    "target_source_revision": c10_capture.TARGET_SOURCE_REVISION,
                },
            }).encode()

    monkeypatch.setenv("SCANNER_TARGET_URL", "https://target.invalid")
    monkeypatch.setenv("SCANNER_TARGET_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("SCANNER_TARGET_KEY", "test-only")
    monkeypatch.setattr(c10_capture.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(c10_capture.CaptureError, match="choices|message|content"):
        c10_capture._request_case({"prompt_turn_sequence": ["test"]})


def test_request_does_not_fall_back_to_legacy_response(monkeypatch) -> None:
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return json.dumps({
                "response": "legacy content must not be accepted",
                "backbond_metadata": {
                    "target_identity": c10_capture.TARGET_IDENTITY,
                    "target_source_revision": c10_capture.TARGET_SOURCE_REVISION,
                },
            }).encode()

    monkeypatch.setenv("SCANNER_TARGET_URL", "https://target.invalid")
    monkeypatch.setenv("SCANNER_TARGET_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("SCANNER_TARGET_KEY", "test-only")
    monkeypatch.setattr(c10_capture.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(c10_capture.CaptureError, match="choices"):
        c10_capture._request_case({"prompt_turn_sequence": ["test"]})


@pytest.mark.parametrize("field,value,reason", [
    ("executed_at", "not-a-time", "timestamp"),
    ("runner_identity", "other-runner", "runner identity"),
    ("target_identity", "fixture", "target identity"),
    ("target_source_revision", "sha256:wrong", "revision"),
])
def test_validator_rejects_malformed_or_mismatched_identity(field, value, reason) -> None:
    evidence = _evidence()
    evidence[field] = value
    with pytest.raises(c10_capture.CaptureError, match=reason):
        c10_capture.validate_execution_evidence(
            evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
        )


def test_validator_names_structured_provenance_mismatch_fields_without_values() -> None:
    evidence = _evidence()
    evidence["target_metadata"]["target_runtime"] = "sensitive-untrusted-value"
    evidence["target_metadata"]["target_model_immutable_id"] = "another-untrusted-value"

    with pytest.raises(c10_capture.CaptureError) as caught:
        c10_capture.validate_execution_evidence(
            evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
        )

    message = str(caught.value)
    assert message == (
        "target structured provenance mismatch: "
        "fields=target_model_immutable_id,target_runtime"
    )
    assert "sensitive-untrusted-value" not in message
    assert "another-untrusted-value" not in message


def test_evidence_write_is_exclusive(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(c10_capture, "EVIDENCE_DIR", tmp_path)
    evidence = _evidence()
    c10_capture.write_execution_evidence_exclusive(
        evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
    )
    with pytest.raises(c10_capture.CaptureError, match="refusing overwrite"):
        c10_capture.write_execution_evidence_exclusive(
            evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
        )


def test_capture_resumes_only_after_validating_existing_evidence(monkeypatch, tmp_path) -> None:
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps([{"stable_case_id": "c10-case-1"}]), encoding="utf-8")
    monkeypatch.setattr(c10_capture, "ROSTER", roster)
    monkeypatch.setattr(c10_capture, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(c10_capture, "preflight", lambda: {"rows": 1})
    (tmp_path / "c10-case-1.json").write_text(json.dumps(_evidence()), encoding="utf-8")
    monkeypatch.setattr(
        c10_capture, "_request_case", lambda row: pytest.fail("target must not be called")
    )
    assert c10_capture.capture()["captured"] == 1


def test_capture_refuses_malformed_existing_evidence(monkeypatch, tmp_path) -> None:
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps([{"stable_case_id": "c10-case-1"}]), encoding="utf-8")
    monkeypatch.setattr(c10_capture, "ROSTER", roster)
    monkeypatch.setattr(c10_capture, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(c10_capture, "preflight", lambda: {"rows": 1})
    (tmp_path / "c10-case-1.json").write_text("{", encoding="utf-8")
    with pytest.raises(c10_capture.CaptureError, match="refusing resume"):
        c10_capture.capture()


@pytest.mark.parametrize("field,value,reason", [
    ("verdict", "PASS", "reported-not-scored"),
    ("artifact_ref", "sha256:abc123", "artifact reference"),
])
def test_validator_preserves_reporting_boundary_and_artifact_integrity(
    field, value, reason
) -> None:
    evidence = _evidence()
    evidence[field] = value
    with pytest.raises(c10_capture.CaptureError, match=reason):
        c10_capture.validate_execution_evidence(
            evidence, stable_case_id="c10-case-1", runner_identity="eval_framework_capture"
        )
