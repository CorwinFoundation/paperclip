import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from harness.agent_egress_control import (
    ApplicabilityInputs,
    EVIDENCE_SCHEMA_VERSION,
    ReceiptBoundaryError,
    applicability,
    load_cases,
    run,
    verify_receipt,
)
from tools.active_test_registry import build_registry, load_published_count, summarize

ROOT = Path(__file__).parents[1] / "third_party/oss-pilots/agent-egress-bench/cases"
ENV = {"BACKBOND_AGENT_EGRESS_CONTROL": "1"}


def _all_applicable(cases):
    return ApplicabilityInputs(
        supported_transports=frozenset(case.transport for case in cases),
        capabilities=frozenset(req for case in cases for req in case.requires),
    )


def _write_evidence(tmp_path, rows):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": EVIDENCE_SCHEMA_VERSION, "observations": rows}))
    return path, f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _invoke(tmp_path, cases, rows, **overrides):
    key = overrides.pop("key", Ed25519PrivateKey.generate())
    path, digest = _write_evidence(tmp_path, rows)
    kwargs = dict(
        case_refs=[case.upstream_case_id for case in cases],
        inputs=_all_applicable(cases),
        corpus_root=ROOT,
        evidence_uri=path.as_uri(),
        evidence_sha256=digest,
        signing_key=key,
        signing_key_id="fixture-key-1",
        trusted_public_keys={"fixture-key-1": key.public_key()},
        env=ENV,
    )
    kwargs.update(overrides)
    return run(**kwargs), key


def test_pinned_tree_resolves_197_cases_once_across_18_categories():
    cases = load_cases(ROOT)
    assert len(cases) == len({case.local_control_case_id for case in cases}) == 197
    assert len({case.category for case in cases}) == 18


def test_semantic_observations_drive_results_and_receipt_signature(tmp_path):
    cases = load_cases(ROOT)[:3]
    rows = [
        {"case_id": cases[0].upstream_case_id, "verdict": cases[0].expected_verdict},
        {"case_id": cases[1].upstream_case_id, "verdict": "allow" if cases[1].expected_verdict != "allow" else "block"},
        {"case_id": cases[2].upstream_case_id, "verdict": "error", "error_code": "evaluation_error"},
    ]
    receipt, key = _invoke(tmp_path, cases, rows)
    assert receipt["counts"] == {"passed": 1, "failed": 1, "errored": 1, "not_applicable": 0, "applicable": 3}
    assert receipt["results"][2]["error_code"] == "evaluation_error"
    assert receipt["evidence_schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert receipt["execution_boundary"] == "authenticated-semantic-evidence-v1"
    assert set(receipt["telemetry"].values()) == {0}
    assert verify_receipt(receipt, key.public_key())
    tampered = copy.deepcopy(receipt)
    tampered["counts"]["passed"] = 2
    assert not verify_receipt(tampered, key.public_key())


def test_n5_all_197_cases_is_byte_identical_with_zero_model_calls(tmp_path):
    cases = load_cases(ROOT)
    rows = [{"case_id": case.upstream_case_id, "verdict": case.expected_verdict} for case in cases]
    key = Ed25519PrivateKey.generate()
    receipts = [_invoke(tmp_path, cases, rows, key=key)[0] for _ in range(5)]
    canonical = [json.dumps(receipt, sort_keys=True, separators=(",", ":")) for receipt in receipts]
    assert len(set(canonical)) == 1
    assert receipts[0]["counts"] == {"passed": 197, "failed": 0, "errored": 0, "not_applicable": 0, "applicable": 197}
    assert set(receipts[0]["telemetry"].values()) == {0}


def test_arbitrary_hashed_non_observation_evidence_cannot_yield_pass(tmp_path):
    case = load_cases(ROOT)[0]
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"expected_verdict": case.expected_verdict}))
    digest = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    key = Ed25519PrivateKey.generate()
    with pytest.raises(ReceiptBoundaryError, match="evidence_schema_invalid"):
        run([case.upstream_case_id], _all_applicable([case]), corpus_root=ROOT,
            evidence_uri=path.as_uri(), evidence_sha256=digest, signing_key=key,
            signing_key_id="fixture-key-1", trusted_public_keys={"fixture-key-1": key.public_key()}, env=ENV)


@pytest.mark.parametrize("mutation,code", [
    ("missing", "evidence_case_missing"),
    ("duplicate", "evidence_case_duplicate"),
    ("unknown", "evidence_case_unknown"),
    ("extra", "evidence_case_unknown"),
])
def test_evidence_case_set_is_exactly_once(tmp_path, mutation, code):
    cases = load_cases(ROOT)[:2]
    rows = [{"case_id": case.upstream_case_id, "verdict": case.expected_verdict} for case in cases]
    if mutation == "missing": rows.pop()
    elif mutation == "duplicate": rows.append(dict(rows[0]))
    elif mutation == "unknown": rows[0]["case_id"] = "forged"
    else: rows.append({"case_id": "extra", "verdict": "block"})
    with pytest.raises(ReceiptBoundaryError, match=code):
        _invoke(tmp_path, cases, rows)


def test_caller_constructed_control_case_cannot_enter_signing_path(tmp_path):
    case = load_cases(ROOT)[0]
    rows = [{"case_id": case.upstream_case_id, "verdict": case.expected_verdict}]
    with pytest.raises(ReceiptBoundaryError, match="case_ref_invalid"):
        _invoke(tmp_path, [case], rows, case_refs=[case])
    with pytest.raises(ReceiptBoundaryError, match="case_ref_unknown"):
        _invoke(tmp_path, [case], rows, case_refs=["forged-case"])


def test_pinned_path_reference_reloads_case_inside_boundary(tmp_path):
    case = load_cases(ROOT)[0]
    rows = [{"case_id": case.upstream_case_id, "verdict": case.expected_verdict}]
    receipt, _ = _invoke(tmp_path, [case], rows, case_refs=[case.source_path])
    assert receipt["results"][0]["source_path"] == case.source_path


def test_corpus_mutation_aborts_before_signing(tmp_path):
    copied = tmp_path / "cases"
    shutil.copytree(ROOT, copied)
    target = next(copied.glob("*/*.json"))
    target.write_bytes(target.read_bytes() + b" ")
    case = load_cases(ROOT)[0]
    path, digest = _write_evidence(tmp_path, [{"case_id": case.upstream_case_id, "verdict": case.expected_verdict}])
    key = Ed25519PrivateKey.generate()
    with pytest.raises(ReceiptBoundaryError, match="corpus_invalid"):
        run([case.upstream_case_id], _all_applicable([case]), corpus_root=copied,
            evidence_uri=path.as_uri(), evidence_sha256=digest, signing_key=key,
            signing_key_id="fixture-key-1", trusted_public_keys={"fixture-key-1": key.public_key()}, env=ENV)


@pytest.mark.parametrize("key_id,trust,code", [
    ("", "matching", "signing_key_id_invalid"),
    (" unknown ", "matching", "signing_key_id_invalid"),
    ("unknown", "empty", "signing_key_untrusted"),
    ("fixture-key-1", "other", "signing_key_mismatch"),
])
def test_signing_identity_is_canonical_and_trusted(tmp_path, key_id, trust, code):
    case = load_cases(ROOT)[0]
    key = Ed25519PrivateKey.generate()
    mapping = ({"fixture-key-1": key.public_key()} if trust == "matching" else
               {} if trust == "empty" else {"fixture-key-1": Ed25519PrivateKey.generate().public_key()})
    with pytest.raises(ReceiptBoundaryError, match=code):
        _invoke(tmp_path, [case], [{"case_id": case.upstream_case_id, "verdict": case.expected_verdict}],
                key=key, signing_key_id=key_id, trusted_public_keys=mapping)


@pytest.mark.parametrize("error_code", ["evaluation_error", "timeout", "transport_error"])
def test_execution_errors_have_closed_stable_codes(tmp_path, error_code):
    case = load_cases(ROOT)[0]
    receipt, _ = _invoke(tmp_path, [case], [{"case_id": case.upstream_case_id, "verdict": "error", "error_code": error_code}])
    assert receipt["results"][0]["outcome"] == "errored"
    assert receipt["results"][0]["error_code"] == error_code


def test_unknown_execution_error_code_aborts(tmp_path):
    case = load_cases(ROOT)[0]
    with pytest.raises(ReceiptBoundaryError, match="evidence_error_code_invalid"):
        _invoke(tmp_path, [case], [{"case_id": case.upstream_case_id, "verdict": "error", "error_code": "exception text"}])


def test_not_applicable_requires_no_observation(tmp_path):
    case = load_cases(ROOT)[0]
    inputs = ApplicabilityInputs(frozenset(), frozenset())
    receipt, _ = _invoke(tmp_path, [case], [], inputs=inputs)
    assert receipt["results"][0]["outcome"] == "not_applicable"


def test_feature_is_disabled_by_default(tmp_path):
    case = load_cases(ROOT)[0]
    with pytest.raises(RuntimeError, match="disabled"):
        _invoke(tmp_path, [case], [{"case_id": case.upstream_case_id, "verdict": case.expected_verdict}], env={})


def test_control_cases_are_absent_from_agent_behavior_registry():
    rows = build_registry()
    assert not any(str(row.get("case_id", "")).startswith("oss.agent-egress.") or
                   "agent-egress-bench" in str(row.get("source_pack", "")) for row in rows)
    assert summarize(rows, load_published_count())["registry_rows"] == len(rows)


def test_control_type_is_rejected_as_misleading_applicability_input():
    with pytest.raises(ValueError, match="not an applicability input"):
        ApplicabilityInputs.from_mapping({"control_type": "proxy", "supported_transports": ["http"], "capabilities": []})


def test_applicability_uses_transport_and_capability_and_semantics():
    case = load_cases(ROOT)[0]
    assert applicability(case, ApplicabilityInputs(frozenset({case.transport}), frozenset(case.requires)))
    assert not applicability(case, ApplicabilityInputs(frozenset(), frozenset(case.requires)))
