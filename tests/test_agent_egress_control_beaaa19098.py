import copy
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from harness.agent_egress_control import (
    ApplicabilityInputs,
    FixtureAdapter,
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
        control_type="proxy",
        supported_transports=frozenset(case.transport for case in cases),
        capabilities=frozenset(req for case in cases for req in case.requires),
    )


def test_pinned_tree_resolves_197_cases_once_across_18_categories():
    cases = load_cases(ROOT)
    assert len(cases) == len({case.local_control_case_id for case in cases}) == 197
    assert len({case.category for case in cases}) == 18


def test_summary_partition_zero_model_calls_and_receipt_tamper_detection():
    cases = load_cases(ROOT)
    wrong = "allow" if cases[0].expected_verdict != "allow" else "block"
    adapter = FixtureAdapter(
        {cases[0].upstream_case_id: wrong, cases[1].upstream_case_id: "invalid"}
    )
    key = Ed25519PrivateKey.generate()
    receipt = run(
        cases,
        _all_applicable(cases),
        adapter,
        evidence_uri="file:///control-evidence.jsonl",
        evidence_sha256="sha256:" + "1" * 64,
        signing_key=key,
        signing_key_id="fixture-key-1",
        env=ENV,
    )
    counts = receipt["counts"]
    assert sum(counts[name] for name in ("passed", "failed", "errored", "not_applicable")) == 197
    assert counts["applicable"] == counts["passed"] + counts["failed"] + counts["errored"]
    assert counts["failed"] == counts["errored"] == 1
    assert receipt["namespace"] == "customer_controls.agent_egress"
    assert receipt["case_class"] == "customer_control"
    assert "agent_behavior" not in receipt["namespace"]
    assert {item["case_class"] for item in receipt["results"]} == {"customer_control"}
    assert set(receipt["telemetry"].values()) == {0}
    assert set(adapter.call_counts.values()) == {0}
    assert verify_receipt(receipt, key.public_key())
    tampered = copy.deepcopy(receipt)
    tampered["counts"]["failed"] = 0
    assert not verify_receipt(tampered, key.public_key())


def test_explicit_not_applicable_is_first_class_and_adapter_is_not_called():
    case = load_cases(ROOT)[0]
    inputs = ApplicabilityInputs(
        control_type="firewall",
        supported_transports=frozenset(),
        capabilities=frozenset(),
    )
    assert not applicability(case, inputs)
    adapter = FixtureAdapter({case.upstream_case_id: "invalid"})
    key = Ed25519PrivateKey.generate()
    receipt = run(
        [case],
        inputs,
        adapter,
        evidence_uri="file:///na.json",
        evidence_sha256="sha256:" + "2" * 64,
        signing_key=key,
        signing_key_id="fixture-key-1",
        env=ENV,
    )
    assert receipt["results"][0]["outcome"] == "not_applicable"
    assert receipt["counts"]["not_applicable"] == 1
    assert receipt["counts"]["passed"] == 0


def test_feature_is_disabled_by_default():
    cases = load_cases(ROOT)
    key = Ed25519PrivateKey.generate()
    try:
        run(
            cases[:1],
            _all_applicable(cases),
            FixtureAdapter(),
            evidence_uri="file:///disabled.json",
            evidence_sha256="sha256:" + "3" * 64,
            signing_key=key,
            signing_key_id="fixture-key-1",
        )
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("control harness must be disabled by default")


def test_control_cases_are_absent_from_agent_behavior_registry():
    rows = build_registry()
    assert not any(
        str(row.get("case_id", "")).startswith("oss.agent-egress.")
        or str(row.get("source_pack", "")).find("agent-egress-bench") >= 0
        for row in rows
    )
    summary = summarize(rows, load_published_count())
    assert summary["registry_rows"] == len(rows)
