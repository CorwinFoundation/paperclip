"""BEAAA-16534 AWS Bedrock/CloudWatch next-provider cross-provider increment.

Extends the QA-approved BEAAA-16490 ADR-0039 normalization harness with AWS
Bedrock/CloudWatch as a THIRD, distinct provider stack. These tests reuse the
UNCHANGED provider normalizer, continuity resolver, and continuity-policy@1.0.0;
nothing here changes BackBond Score math, rubric, band cuts, or the
are-scanner-api response envelope.

Acceptance criteria locked here (issue BEAAA-16534):

* AWS evidence is ingested with EACH existing stack (OTel, Datadog);
* AWS + OTel corroboration resolves to exactly one band + one decision;
* AWS + Datadog conflict resolves to DIVERGENCE (never averaged);
* AWS stale/missing resolves to DEGRADED (fail-closed);
* a contradictory AWS provider-native verdict CANNOT override BackBond;
* shared signing-key lineage between AWS and OTel fails the independence check
  and drops to DEGRADED;
* every scenario preserves provenance and yields a bundle validated by
  ``verify_bundle`` and ``recompute_decision``;
* the resolver never reads the AWS provider verdict (recompute-under-tamper);
* the new modules import no scoring / rubric / band-cut surface (isolation).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from determination.continuity import (
    BAND_CONTINUE,
    BAND_DEGRADED,
    BAND_DIVERGENCE,
    BAND_ELEVATED,
    ContinuityDecisionEngine,
    ContinuityPolicy,
    recompute_decision,
    verify_bundle,
)
from determination.provider_normalization import (
    ProviderEvidenceEnvelope,
    check_independence,
    normalize_provider_evidence,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = (
    REPO
    / "determination"
    / "fixtures"
    / "cross_provider"
    / "synthetic_agent_aws_third_provider.json"
)

AWS_STACK = "aws-bedrock-cloudwatch"
AWS_PROVIDER_ID = "provider-c-aws"


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def engine() -> ContinuityDecisionEngine:
    return ContinuityDecisionEngine()


def _decide(engine, fixture_data, scenario_key):
    scenario = fixture_data["scenarios"][scenario_key]
    return scenario, engine.decide(
        agent_id=fixture_data["agent_id"],
        agent_version=fixture_data["agent_version"],
        envelopes=scenario["envelopes"],
        as_of=scenario["as_of"],
    )


SCENARIOS = [
    ("aws_otel_corroboration_clean", BAND_CONTINUE, ["otel-genai-collector"]),
    ("aws_datadog_conflict_divergence", BAND_DIVERGENCE, ["datadog-llm-obs"]),
    ("aws_stale_degraded", BAND_DEGRADED, ["otel-genai-collector"]),
    ("aws_verdict_cannot_override", BAND_ELEVATED, ["otel-genai-collector"]),
    ("aws_shared_signing_key_not_independent", BAND_DEGRADED, ["otel-genai-collector"]),
]


@pytest.mark.parametrize("scenario_key,expected_band,partner_stacks", SCENARIOS)
def test_scenario_resolves_to_single_band_and_decision(
    engine, fixture_data, scenario_key, expected_band, partner_stacks
):
    scenario, decision = _decide(engine, fixture_data, scenario_key)
    assert decision.band == expected_band == scenario["expected_band"]
    assert decision.decision == scenario["expected_decision"]
    # Exactly ONE band + ONE decision (both scalar fields, not collections).
    assert isinstance(decision.band, str)
    assert isinstance(decision.decision, str)
    # AWS is present in every scenario, paired with the named existing stack.
    stacks = {s.provenance["provider_stack"] for s in decision.sources}
    assert AWS_STACK in stacks
    for partner in partner_stacks:
        assert partner in stacks


def test_aws_ingested_with_each_existing_stack(engine, fixture_data):
    """AWS is exercised against BOTH existing stacks (OTel and Datadog)."""
    _, otel = _decide(engine, fixture_data, "aws_otel_corroboration_clean")
    _, datadog = _decide(engine, fixture_data, "aws_datadog_conflict_divergence")
    otel_stacks = {s.provenance["provider_stack"] for s in otel.sources}
    dd_stacks = {s.provenance["provider_stack"] for s in datadog.sources}
    assert otel_stacks == {AWS_STACK, "otel-genai-collector"}
    assert dd_stacks == {AWS_STACK, "datadog-llm-obs"}


def test_aws_otel_corroboration_is_continue(engine, fixture_data):
    _, decision = _decide(engine, fixture_data, "aws_otel_corroboration_clean")
    assert decision.band == BAND_CONTINUE
    assert decision.decision == "continue_monitoring"
    assert decision.eligible_source_count == 2
    assert decision.independence["independent"] is True
    assert all(s.signal == "no_finding" for s in decision.sources)


def test_aws_datadog_conflict_diverges_without_averaging(engine, fixture_data):
    _, decision = _decide(engine, fixture_data, "aws_datadog_conflict_divergence")
    assert decision.band == BAND_DIVERGENCE
    assert decision.decision == "divergence_hold"
    signals = sorted(s.signal for s in decision.sources)
    assert signals == ["covered_peril_candidate", "no_finding"]


def test_aws_stale_drops_to_degraded(engine, fixture_data):
    """AWS evidence older than the freshness window is ineligible; the single
    remaining fresh source cannot corroborate -> DEGRADED."""
    _, decision = _decide(engine, fixture_data, "aws_stale_degraded")
    assert decision.band == BAND_DEGRADED
    assert decision.decision == "degraded_insufficient_independent_corroboration"
    # AWS source is present but ineligible (stale); only one eligible source left.
    assert decision.eligible_source_count == 1
    aws_src = next(
        s for s in decision.sources
        if s.provenance["provider_stack"] == AWS_STACK
    )
    assert aws_src.fresh is False
    assert aws_src.eligible is False


def test_aws_provider_verdict_cannot_override_backbond(engine, fixture_data):
    """Negative test: AWS Bedrock guardrail asserts no_incident/override, but
    BackBond covered-peril-candidate evidence still resolves to ELEVATED."""
    _, decision = _decide(engine, fixture_data, "aws_verdict_cannot_override")
    assert decision.band == BAND_ELEVATED
    assert decision.decision == "review_covered_peril_candidate"
    aws_src = next(
        s for s in decision.sources
        if s.provenance["provider_stack"] == AWS_STACK
    )
    # AWS verdict retained as INPUT provenance...
    assert aws_src.provenance["provider_native_determination"]["verdict"] == "no_incident"
    assert aws_src.provenance["provider_native_determination"]["override"] is True
    # ...but BackBond signal is a candidate independent of the provider verdict.
    assert all(s.signal == "covered_peril_candidate" for s in decision.sources)


def test_aws_shared_signing_key_fails_independence(engine, fixture_data):
    """AWS + OTel signed by the same key are not independent -> DEGRADED."""
    scenario, decision = _decide(
        engine, fixture_data, "aws_shared_signing_key_not_independent"
    )
    assert decision.band == BAND_DEGRADED
    assert decision.independence["independent"] is False
    assert decision.independence["reason"] == "shared_attestation_key"
    # Confirm directly through the independence check too.
    norms = [
        normalize_provider_evidence(ProviderEvidenceEnvelope.from_dict(e))
        for e in scenario["envelopes"]
    ]
    result = check_independence(norms)
    assert result.independent is False
    assert result.reason == "shared_attestation_key"


def test_aws_provenance_preserved_through_normalization(engine, fixture_data):
    """AWS-distinct provenance (provider id, stack, SigV4 key, auth method,
    independence group, immutable event ids, integrity status) survives."""
    _, decision = _decide(engine, fixture_data, "aws_datadog_conflict_divergence")
    aws_src = next(
        s for s in decision.sources
        if s.provenance["provider_stack"] == AWS_STACK
    )
    prov = aws_src.provenance
    assert prov["source_provider_id"] == AWS_PROVIDER_ID
    assert prov["provider_stack"] == AWS_STACK
    assert prov["attestation_key_id"] == "kid-C-sigv4"
    assert prov["authentication_method"] == "aws_sigv4_cloudwatch_logs"
    assert prov["attestation_type"] == "cloudwatch_signed_log_delivery"
    assert prov["independence_group"] == "aws-acct-9910-us-east-1"
    assert prov["integrity_status"] == "authenticated"
    assert prov["source_event_ids"] == ["c-conflict-1"]
    assert aws_src.determination_ids


@pytest.mark.parametrize("scenario_key", [s[0] for s in SCENARIOS])
def test_bundle_is_machine_verifiable(engine, fixture_data, scenario_key, tmp_path):
    """Each AWS scenario emits a bundle whose bound hash and decision re-derive
    from the bundle alone, round-tripping through JSON."""
    _, decision = _decide(engine, fixture_data, scenario_key)
    bundle = decision.to_bundle()
    out = tmp_path / f"{scenario_key}.json"
    out.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert verify_bundle(reloaded) is True
    recomputed = recompute_decision(reloaded)
    assert recomputed["band_matches"]
    assert recomputed["decision_matches"]
    assert recomputed["hash_matches"]


def test_recompute_ignores_tampered_aws_verdict(engine, fixture_data):
    """Rewriting the AWS provider verdict inside a preserved bundle does not
    change the re-derived band (verdict is not in the deciding path); the bound
    hash separately catches the tamper."""
    _, decision = _decide(engine, fixture_data, "aws_verdict_cannot_override")
    bundle = decision.to_bundle()
    for src in bundle["sources"]:
        src["provenance"]["provider_native_determination"] = {
            "verdict": "no_incident",
            "guardrail_action": "NONE",
            "override": True,
        }
    recomputed = recompute_decision(bundle)
    assert recomputed["band"] == BAND_ELEVATED
    assert recomputed["decision"] == "review_covered_peril_candidate"
    assert recomputed["hash_matches"] is False


def test_determinism_repeated_runs_identical_bundle(engine, fixture_data):
    for scenario_key, _band, _p in SCENARIOS:
        _, d1 = _decide(engine, fixture_data, scenario_key)
        _, d2 = _decide(engine, fixture_data, scenario_key)
        assert d1.bundle_sha256 == d2.bundle_sha256
        assert d1.band == d2.band and d1.decision == d2.decision


def test_policy_thresholds_unchanged(engine, fixture_data):
    """The AWS increment must not have moved any continuity policy threshold."""
    policy = ContinuityPolicy.load()
    assert policy.version == "continuity-policy@1.0.0"
    assert policy.min_independent_corroborating_sources == 2
    assert policy.freshness_window_seconds == 3600


def test_no_scoring_surface_imported():
    """Isolation guard: reused modules touch no scoring / rubric / band-cut."""
    import determination.continuity as c
    import determination.provider_normalization as p

    for mod in (c, p):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "rubric.scoring" not in src
        assert "rubric.bands" not in src
        assert "import rubric" not in src
