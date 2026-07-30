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
    assert all(adapter.call_counts[role] == 0 for role in ("target", "simulator", "attacker", "judge"))
    assert adapter.call_counts["local_evaluations"] == 197
    assert receipt["execution_boundary"] == "sealed-declarative-local-v1"
    assert receipt["telemetry_verified"] is True
    assert receipt["version"] == "agent-egress-control-receipt-v2"
    assert receipt["adapter_version"] == "agent-egress-control-adapter-v2"
    assert verify_receipt(receipt, key.public_key())
    tampered = copy.deepcopy(receipt)
    tampered["counts"]["failed"] = 0
    assert not verify_receipt(tampered, key.public_key())


def test_explicit_not_applicable_is_first_class_and_adapter_is_not_called():
    case = load_cases(ROOT)[0]
    inputs = ApplicabilityInputs(
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


def test_adversarial_executable_adapter_is_rejected_before_invocation():
    case = load_cases(ROOT)[0]

    class ModelCallingAdapter:
        def __init__(self):
            self.observed_target_calls = 0

        def evaluate(self, case):
            self.observed_target_calls += 1
            return case.expected_verdict

    adapter = ModelCallingAdapter()
    key = Ed25519PrivateKey.generate()
    try:
        run(
            [case],
            _all_applicable([case]),
            adapter,
            evidence_uri="file:///adversarial.json",
            evidence_sha256="sha256:" + "4" * 64,
            signing_key=key,
            signing_key_id="fixture-key-1",
            env=ENV,
        )
    except TypeError as exc:
        assert "unverifiable adapter rejected" in str(exc)
    else:
        raise AssertionError("arbitrary executable adapter must fail closed")
    assert adapter.observed_target_calls == 0


def test_fixture_adapter_subclass_is_rejected_before_override_invocation():
    case = load_cases(ROOT)[0]

    class SubvertedFixtureAdapter(FixtureAdapter):
        def evaluate(self, case):
            raise AssertionError("subclass callback must never execute")

    key = Ed25519PrivateKey.generate()
    try:
        run(
            [case],
            _all_applicable([case]),
            SubvertedFixtureAdapter(),
            evidence_uri="file:///subclass.json",
            evidence_sha256="sha256:" + "5" * 64,
            signing_key=key,
            signing_key_id="fixture-key-1",
            env=ENV,
        )
    except TypeError as exc:
        assert "unverifiable adapter rejected" in str(exc)
    else:
        raise AssertionError("adapter subclasses must fail closed")


def test_receipt_path_does_not_dispatch_through_mutable_adapter_method(monkeypatch):
    case = load_cases(ROOT)[0]
    adapter = FixtureAdapter()

    def subverted_evaluate(self, case):
        raise AssertionError("mutable class callback must never execute")

    monkeypatch.setattr(FixtureAdapter, "evaluate", subverted_evaluate)
    key = Ed25519PrivateKey.generate()
    receipt = run(
        [case],
        _all_applicable([case]),
        adapter,
        evidence_uri="file:///sealed.json",
        evidence_sha256="sha256:" + "6" * 64,
        signing_key=key,
        signing_key_id="fixture-key-1",
        env=ENV,
    )
    assert receipt["results"][0]["outcome"] == "passed"
    assert set(receipt["telemetry"].values()) == {0}


def test_receipt_path_ignores_caller_replaced_telemetry_descriptor(monkeypatch):
    case = load_cases(ROOT)[0]
    adapter = FixtureAdapter()
    observed = {"descriptor_calls": 0}

    def caller_controlled_descriptor(self):
        observed["descriptor_calls"] += 1
        return {
            "target": 0,
            "simulator": 0,
            "attacker": 0,
            "judge": 0,
            "local_evaluations": 0,
        }

    monkeypatch.setattr(
        FixtureAdapter,
        "call_counts",
        property(caller_controlled_descriptor),
    )
    key = Ed25519PrivateKey.generate()
    receipt = run(
        [case],
        _all_applicable([case]),
        adapter,
        evidence_uri="file:///descriptor-substitution.json",
        evidence_sha256="sha256:" + "8" * 64,
        signing_key=key,
        signing_key_id="fixture-key-1",
        env=ENV,
    )

    assert observed["descriptor_calls"] == 0
    assert receipt["results"][0]["outcome"] == "passed"
    assert receipt["counts"]["passed"] == receipt["counts"]["applicable"] == 1
    assert receipt["telemetry_verified"] is True
    assert receipt["telemetry"] == {
        "target_calls": 0,
        "simulator_calls": 0,
        "attacker_calls": 0,
        "judge_calls": 0,
    }
    assert verify_receipt(receipt, key.public_key())


def test_post_construction_executable_verdict_state_is_rejected_without_invocation():
    case = load_cases(ROOT)[0]
    adapter = FixtureAdapter()
    observed = {"contains": 0}

    class ExecutableDict(dict):
        def __contains__(self, key):
            observed["contains"] += 1
            return super().__contains__(key)

    try:
        adapter._verdicts = ExecutableDict({case.upstream_case_id: case.expected_verdict})
    except AttributeError as exc:
        assert "immutable after construction" in str(exc)
    else:
        raise AssertionError("post-construction fixture payload replacement must fail closed")

    # Even object.__setattr__ cannot smuggle executable state into the receipt path.
    object.__setattr__(
        adapter,
        "_verdicts",
        ExecutableDict({case.upstream_case_id: case.expected_verdict}),
    )
    key = Ed25519PrivateKey.generate()
    try:
        run(
            [case],
            _all_applicable([case]),
            adapter,
            evidence_uri="file:///post-construction-state.json",
            evidence_sha256="sha256:" + "7" * 64,
            signing_key=key,
            signing_key_id="fixture-key-1",
            env=ENV,
        )
    except TypeError as exc:
        assert "not sealed declarative data" in str(exc)
    else:
        raise AssertionError("replaced executable fixture state must fail closed")
    assert observed["contains"] == 0


def test_control_type_is_rejected_as_misleading_applicability_input():
    try:
        ApplicabilityInputs.from_mapping(
            {
                "control_type": "proxy",
                "supported_transports": ["http"],
                "capabilities": [],
            }
        )
    except ValueError as exc:
        assert "not an applicability input" in str(exc)
    else:
        raise AssertionError("control_type must not imply an unsupported eligibility matrix")


def test_applicability_uses_only_transport_and_capability_and_semantics():
    case = load_cases(ROOT)[0]
    assert applicability(
        case,
        ApplicabilityInputs(
            supported_transports=frozenset({case.transport}),
            capabilities=frozenset(case.requires),
        ),
    )
    assert not applicability(
        case,
        ApplicabilityInputs(
            supported_transports=frozenset(),
            capabilities=frozenset(case.requires),
        ),
    )
    if case.requires:
        assert not applicability(
            case,
            ApplicabilityInputs(
                supported_transports=frozenset({case.transport}),
                capabilities=frozenset(),
            ),
        )


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
