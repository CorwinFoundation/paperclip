"""BEAAA-16533 AWS Bedrock/CloudWatch next-provider cross-provider tests.

Extends the QA-approved BEAAA-16490 / ADR-0039 normalization + continuity harness
with AWS Bedrock/CloudWatch as a THIRD, distinct provider stack, ingested
alongside each existing independent stack for one synthetic deployed agent. Reuses
the unchanged ``determination.provider_normalization`` /
``determination.continuity`` modules and continuity-policy@1.0.0 — no new monitor,
no policy threshold/semantic change.

Locks the acceptance criteria for BEAAA-16533:

* (1) AWS + OTel independently corroborate a covered-peril candidate -> ELEVATED;
* (2) AWS + Datadog conflict -> DIVERGENCE (never averaged, neither provider wins);
* (3) AWS stale / unauthenticated -> DEGRADED (fail-closed);
* (4) AWS Bedrock Guardrails verdict contradicts telemetry yet CANNOT override the
  BackBond result -> still ELEVATED, verdict retained as provenance only;
* (5) AWS sharing a signing key with OTel fails independence -> DEGRADED;
* every scenario emits exactly ONE band + ONE decision, preserves the AWS
  provenance (distinct provider id, stack, signing key, auth method, independence
  group, immutable event ids, timestamps, integrity status, provider-native
  verdict), and produces a hash-bound bundle that ``verify_bundle`` and
  ``recompute_decision`` both validate;
* nothing here changes BackBond Score math, rubric, band cuts, or the
  are-scanner-api response envelope (import-surface guard).
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
    / "synthetic_agent_aws_bedrock.json"
)

AWS_PROVIDER_ID = "provider-c-aws-bedrock"
AWS_STACK = "aws-bedrock-cloudwatch"
AWS_KEY = "kid-C-kms-asym"


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
    ("aws_otel_corroboration", BAND_ELEVATED),
    ("aws_datadog_conflict_divergence", BAND_DIVERGENCE),
    ("aws_stale_degraded", BAND_DEGRADED),
    ("aws_unauthenticated_degraded", BAND_DEGRADED),
    ("aws_provider_verdict_cannot_override", BAND_ELEVATED),
    ("aws_shared_lineage_not_independent", BAND_DEGRADED),
    ("aws_otel_datadog_triple_corroboration", BAND_CONTINUE),
]


@pytest.mark.parametrize("scenario_key,expected_band", SCENARIOS)
def test_scenario_resolves_to_single_band_and_decision(
    engine, fixture_data, scenario_key, expected_band
):
    scenario, decision = _decide(engine, fixture_data, scenario_key)
    assert decision.band == expected_band == scenario["expected_band"]
    assert decision.decision == scenario["expected_decision"]
    # Exactly ONE band + ONE decision (scalar fields, not collections).
    assert isinstance(decision.band, str)
    assert isinstance(decision.decision, str)


def test_all_five_required_cases_present(engine, fixture_data):
    """The five mandated AWS cases each resolve to their expected band."""
    got = {
        key: _decide(engine, fixture_data, key)[1].band
        for key in (
            "aws_otel_corroboration",
            "aws_datadog_conflict_divergence",
            "aws_stale_degraded",
            "aws_unauthenticated_degraded",
            "aws_provider_verdict_cannot_override",
            "aws_shared_lineage_not_independent",
        )
    }
    assert got["aws_otel_corroboration"] == BAND_ELEVATED
    assert got["aws_datadog_conflict_divergence"] == BAND_DIVERGENCE
    assert got["aws_stale_degraded"] == BAND_DEGRADED
    assert got["aws_unauthenticated_degraded"] == BAND_DEGRADED
    assert got["aws_provider_verdict_cannot_override"] == BAND_ELEVATED
    assert got["aws_shared_lineage_not_independent"] == BAND_DEGRADED


def test_aws_provenance_preserved_through_normalization(engine, fixture_data):
    """Every mandated AWS provenance field survives normalization into the
    machine-verifiable bundle (distinct provider id, stack, signing key, auth
    method, independence group, immutable event ids, timestamps, integrity)."""
    _, decision = _decide(engine, fixture_data, "aws_otel_corroboration")
    aws = next(
        s for s in decision.sources
        if s.provenance["source_provider_id"] == AWS_PROVIDER_ID
    )
    prov = aws.provenance
    assert prov["provider_stack"] == AWS_STACK
    assert prov["attestation_key_id"] == AWS_KEY
    assert prov["authentication_method"] == "aws_sigv4_kms_asymmetric_signature"
    assert prov["independence_group"] == "grp-aws-account-4417"
    assert prov["integrity_status"] == "authenticated"
    assert prov["received_timestamp"], "received timestamp not preserved"
    assert prov["source_event_ids"], "immutable AWS event ids not preserved"
    assert aws.determination_ids, "AWS determination ids not attached"


def test_aws_datadog_divergence_does_not_average(engine, fixture_data):
    """AWS candidate vs Datadog no_finding emits a divergence action, not a blend."""
    _, decision = _decide(engine, fixture_data, "aws_datadog_conflict_divergence")
    assert decision.band == BAND_DIVERGENCE
    assert decision.decision == "divergence_hold"
    signals = sorted(s.signal for s in decision.sources)
    assert signals == ["covered_peril_candidate", "no_finding"]


def test_aws_provider_verdict_cannot_override_backbond(engine, fixture_data):
    """AWS Bedrock Guardrails asserts no_incident/override; BackBond still finds a
    candidate. The verdict is present as provenance but cannot flip to CONTINUE."""
    _, decision = _decide(
        engine, fixture_data, "aws_provider_verdict_cannot_override"
    )
    assert decision.band == BAND_ELEVATED
    assert decision.decision == "review_covered_peril_candidate"
    aws = next(
        s for s in decision.sources
        if s.provenance["source_provider_id"] == AWS_PROVIDER_ID
    )
    # Verdict retained as INPUT provenance...
    assert aws.provenance["provider_native_determination"]["verdict"] == "no_incident"
    assert aws.provenance["provider_native_determination"]["override"] is True
    # ...but the AWS BackBond signal is a candidate, independent of it.
    assert aws.signal == "covered_peril_candidate"


def test_aws_shared_signing_key_fails_independence(engine, fixture_data):
    """AWS + OTel share attestation key kid-shared-lineage -> not independent."""
    scenario = fixture_data["scenarios"]["aws_shared_lineage_not_independent"]
    norms = [
        normalize_provider_evidence(ProviderEvidenceEnvelope.from_dict(e))
        for e in scenario["envelopes"]
    ]
    result = check_independence(norms)
    assert result.independent is False
    assert result.reason == "shared_attestation_key"
    # And the resolved band is DEGRADED (shared lineage cannot corroborate).
    _, decision = _decide(engine, fixture_data, "aws_shared_lineage_not_independent")
    assert decision.band == BAND_DEGRADED


def test_aws_stale_and_unauthenticated_fail_closed(engine, fixture_data):
    """Stale and failed-integrity AWS evidence are ineligible -> DEGRADED, with
    exactly one eligible source in each case."""
    _, stale = _decide(engine, fixture_data, "aws_stale_degraded")
    assert stale.band == BAND_DEGRADED
    assert stale.eligible_source_count == 1
    _, unauth = _decide(engine, fixture_data, "aws_unauthenticated_degraded")
    assert unauth.band == BAND_DEGRADED
    assert unauth.eligible_source_count == 1
    aws = next(
        s for s in unauth.sources
        if s.provenance["source_provider_id"] == AWS_PROVIDER_ID
    )
    assert aws.authenticated is False
    assert aws.eligible is False


def test_three_provider_join_emits_one_band(engine, fixture_data):
    """AWS + OTel + Datadog (three distinct stacks) resolve to ONE band/decision."""
    _, decision = _decide(engine, fixture_data, "aws_otel_datadog_triple_corroboration")
    assert decision.band == BAND_CONTINUE
    assert decision.decision == "continue_monitoring"
    assert len(decision.sources) == 3
    provider_ids = {s.provenance["source_provider_id"] for s in decision.sources}
    assert provider_ids == {
        "provider-a-otel",
        "provider-b-datadog",
        AWS_PROVIDER_ID,
    }
    assert decision.eligible_source_count == 3
    assert decision.independence["independent"] is True


def test_bundle_is_machine_verifiable_and_reachable(engine, fixture_data, tmp_path):
    """Every AWS scenario bundle serializes, round-trips through JSON, and both its
    bound hash and its decision re-derive from the bundle alone."""
    for scenario_key, _ in SCENARIOS:
        _, decision = _decide(engine, fixture_data, scenario_key)
        bundle = decision.to_bundle()
        out = tmp_path / f"{scenario_key}.json"
        out.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        reloaded = json.loads(out.read_text(encoding="utf-8"))
        assert verify_bundle(reloaded) is True, scenario_key
        recomputed = recompute_decision(reloaded)
        assert recomputed["band_matches"], scenario_key
        assert recomputed["decision_matches"], scenario_key
        assert recomputed["hash_matches"], scenario_key
        assert reloaded["bundle_sha256"] == recomputed["recomputed_bundle_sha256"]


def test_recompute_ignores_tampered_aws_verdict(engine, fixture_data):
    """Re-derives the SAME band after an attacker rewrites the AWS Bedrock verdict
    inside a preserved bundle — proving the verdict is not in the deciding path.
    The hash check separately catches the tamper."""
    _, decision = _decide(engine, fixture_data, "aws_provider_verdict_cannot_override")
    bundle = decision.to_bundle()
    for src in bundle["sources"]:
        if src["provenance"]["source_provider_id"] == AWS_PROVIDER_ID:
            src["provenance"]["provider_native_determination"] = {
                "verdict": "guardrail_pass",
                "override": True,
            }
    recomputed = recompute_decision(bundle)
    assert recomputed["band"] == BAND_ELEVATED
    assert recomputed["decision"] == "review_covered_peril_candidate"
    assert recomputed["hash_matches"] is False


def test_determinism_repeated_runs_identical_bundle(engine, fixture_data):
    for scenario_key, _ in SCENARIOS:
        _, d1 = _decide(engine, fixture_data, scenario_key)
        _, d2 = _decide(engine, fixture_data, scenario_key)
        assert d1.bundle_sha256 == d2.bundle_sha256, scenario_key
        assert d1.band == d2.band and d1.decision == d2.decision


def test_aws_provider_verdict_never_read_by_resolver(engine, fixture_data):
    """Flipping the AWS provider verdict in the raw envelope does NOT change the
    band/decision (verdict is provenance-only input)."""
    scenario = fixture_data["scenarios"]["aws_otel_corroboration"]
    base = engine.decide(
        agent_id=fixture_data["agent_id"],
        agent_version=fixture_data["agent_version"],
        envelopes=scenario["envelopes"],
        as_of=scenario["as_of"],
    )
    mutated = [dict(e) for e in scenario["envelopes"]]
    mutated[0] = dict(mutated[0])
    mutated[0]["provider_native_determination"] = {"verdict": "no_incident", "override": True}
    after = engine.decide(
        agent_id=fixture_data["agent_id"],
        agent_version=fixture_data["agent_version"],
        envelopes=mutated,
        as_of=scenario["as_of"],
    )
    assert after.band == base.band == BAND_ELEVATED
    assert after.decision == base.decision


def test_pins_and_policy_unchanged():
    """The increment reuses continuity-policy@1.0.0 with unchanged thresholds."""
    policy = ContinuityPolicy.load()
    assert policy.version == "continuity-policy@1.0.0"
    assert policy.min_independent_corroborating_sources == 2
    assert policy.freshness_window_seconds == 3600


def test_no_scoring_surface_imported():
    """Isolation guard: the reused cross-provider modules do not import or touch
    BackBond Score / rubric / band-cut modules."""
    import determination.continuity as c
    import determination.provider_normalization as p

    for mod in (c, p):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "rubric.scoring" not in src
        assert "rubric.bands" not in src
        assert "import rubric" not in src
