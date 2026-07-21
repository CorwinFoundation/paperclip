"""BEAAA-16490 cross-provider Passive Monitor normalization tests.

Locks the acceptance criteria:

* two independent provider stacks for ONE synthetic deployed agent normalize
  into exactly ONE BackBond continuity band + ONE continuity decision;
* the three deterministic cases — corroboration, conflict/divergence,
  missing/stale — each resolve to their expected band;
* source provenance (provider identity, immutable source event ids, attestation
  type, timestamps, integrity/authentication status, independence result)
  survives normalization into the machine-verifiable bundle;
* a provider-supplied verdict CANNOT override the BackBond determination
  (negative test);
* the produced bundle is reachable and machine-verifiable (hash + re-derivable
  decision), and the resolver never reads the provider verdict;
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
    ProviderNormalizationError,
    check_independence,
    normalize_provider_evidence,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = (
    REPO
    / "determination"
    / "fixtures"
    / "cross_provider"
    / "synthetic_agent_two_providers.json"
)


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
    ("corroboration_clean", BAND_CONTINUE),
    ("corroboration_candidate", BAND_ELEVATED),
    ("conflict_divergence", BAND_DIVERGENCE),
    ("missing_stale", BAND_DEGRADED),
    ("provider_verdict_cannot_override", BAND_ELEVATED),
    ("shared_lineage_not_independent", BAND_DEGRADED),
]


@pytest.mark.parametrize("scenario_key,expected_band", SCENARIOS)
def test_scenario_resolves_to_single_band_and_decision(
    engine, fixture_data, scenario_key, expected_band
):
    scenario, decision = _decide(engine, fixture_data, scenario_key)
    assert decision.band == expected_band == scenario["expected_band"]
    assert decision.decision == scenario["expected_decision"]
    # Exactly ONE band + ONE decision (both are scalar fields, not collections).
    assert isinstance(decision.band, str)
    assert isinstance(decision.decision, str)


def test_three_deterministic_cases_present(engine, fixture_data):
    """Corroboration, conflict/divergence, and missing/stale all exercised."""
    bands = {
        key: _decide(engine, fixture_data, key)[1].band
        for key in ("corroboration_candidate", "conflict_divergence", "missing_stale")
    }
    assert bands["corroboration_candidate"] == BAND_ELEVATED
    assert bands["conflict_divergence"] == BAND_DIVERGENCE
    assert bands["missing_stale"] == BAND_DEGRADED


def test_provenance_preserved_through_normalization(engine, fixture_data):
    _, decision = _decide(engine, fixture_data, "corroboration_candidate")
    assert len(decision.sources) == 2
    provider_ids = {s.provenance["source_provider_id"] for s in decision.sources}
    assert provider_ids == {"provider-a-otel", "provider-b-datadog"}
    for src in decision.sources:
        prov = src.provenance
        # Every mandated provenance field survives.
        for field_name in (
            "source_provider_id",
            "provider_stack",
            "attestation_type",
            "attestation_key_id",
            "authentication_method",
            "integrity_status",
            "received_timestamp",
            "source_event_ids",
        ):
            assert prov.get(field_name), f"missing provenance field {field_name}"
        # Immutable source event ids are carried through.
        assert prov["source_event_ids"], "source event ids not preserved"
        assert src.determination_ids, "determination ids not attached"


def test_divergence_does_not_average(engine, fixture_data):
    """Conflict emits a divergence action, never a blended/averaged outcome."""
    _, decision = _decide(engine, fixture_data, "conflict_divergence")
    assert decision.band == BAND_DIVERGENCE
    assert decision.decision == "divergence_hold"
    # Both provider signals are preserved and they genuinely disagree.
    signals = sorted(s.signal for s in decision.sources)
    assert signals == ["covered_peril_candidate", "no_finding"]


def test_provider_verdict_cannot_override_backbond(engine, fixture_data):
    """Negative test: provider asserts no_incident, BackBond still finds a
    candidate. The provider verdict is present in provenance but cannot flip the
    outcome to CONTINUE.
    """
    scenario, decision = _decide(
        engine, fixture_data, "provider_verdict_cannot_override"
    )
    assert decision.band == BAND_ELEVATED
    assert decision.decision == "review_covered_peril_candidate"
    # The provider verdict is retained as INPUT provenance...
    for src in decision.sources:
        assert (
            src.provenance["provider_native_determination"]["verdict"]
            == "no_incident"
        )
        # ...but every source's BackBond signal is a candidate, independent of it.
        assert src.signal == "covered_peril_candidate"


def test_bundle_is_machine_verifiable_and_reachable(engine, fixture_data, tmp_path):
    """The evidence bundle serializes, round-trips through JSON, and both its
    bound hash and its decision re-derive from the bundle alone.
    """
    _, decision = _decide(engine, fixture_data, "corroboration_candidate")
    bundle = decision.to_bundle()

    out = tmp_path / "bundle.json"
    out.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    reloaded = json.loads(out.read_text(encoding="utf-8"))

    assert verify_bundle(reloaded) is True
    recomputed = recompute_decision(reloaded)
    assert recomputed["band_matches"]
    assert recomputed["decision_matches"]
    assert recomputed["hash_matches"]
    assert reloaded["bundle_sha256"] == recomputed["recomputed_bundle_sha256"]


def test_recompute_ignores_tampered_provider_verdict(engine, fixture_data):
    """Machine-verification re-derives the SAME band even after an attacker
    rewrites the provider verdict inside a preserved bundle — proving the verdict
    is not in the deciding path. (The hash check separately catches the tamper.)
    """
    _, decision = _decide(engine, fixture_data, "corroboration_candidate")
    bundle = decision.to_bundle()
    # Tamper: flip both provider verdicts to a benign override claim.
    for src in bundle["sources"]:
        src["provenance"]["provider_native_determination"] = {
            "verdict": "no_incident",
            "override": True,
        }
    recomputed = recompute_decision(bundle)
    # Band/decision re-derive unchanged (verdict never consulted)...
    assert recomputed["band"] == BAND_ELEVATED
    assert recomputed["decision"] == "review_covered_peril_candidate"
    # ...and the bound hash no longer matches, so the tamper is detectable.
    assert recomputed["hash_matches"] is False


def test_determinism_repeated_runs_identical_bundle(engine, fixture_data):
    _, d1 = _decide(engine, fixture_data, "corroboration_candidate")
    _, d2 = _decide(engine, fixture_data, "corroboration_candidate")
    assert d1.bundle_sha256 == d2.bundle_sha256
    assert d1.band == d2.band and d1.decision == d2.decision


def test_independence_check_flags_shared_key(engine, fixture_data):
    scenario = fixture_data["scenarios"]["shared_lineage_not_independent"]
    norms = [
        normalize_provider_evidence(ProviderEvidenceEnvelope.from_dict(e))
        for e in scenario["envelopes"]
    ]
    result = check_independence(norms)
    assert result.independent is False
    assert result.reason == "shared_attestation_key"


def test_unauthenticated_source_is_ineligible():
    """An unverified/failed-integrity source cannot corroborate (fail-closed)."""
    engine = ContinuityDecisionEngine()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    scenario = data["scenarios"]["corroboration_candidate"]
    envelopes = [dict(e) for e in scenario["envelopes"]]
    envelopes[1] = dict(envelopes[1])
    envelopes[1]["integrity_status"] = "failed"
    decision = engine.decide(
        agent_id=data["agent_id"],
        agent_version=data["agent_version"],
        envelopes=envelopes,
        as_of=scenario["as_of"],
    )
    # Only one eligible source -> cannot corroborate -> degraded.
    assert decision.band == BAND_DEGRADED
    assert decision.eligible_source_count == 1


def test_pii_and_disallowed_keys_redacted_before_retention():
    """PII values are masked and customer-weight / prompt keys are dropped from
    retained provider metadata."""
    envelope = ProviderEvidenceEnvelope.from_dict(
        {
            "source_provider_id": "provider-a-otel",
            "provider_stack": "otel-genai-collector",
            "attestation_type": "signed_batch_manifest",
            "attestation_key_id": "kid-A-ed25519",
            "authentication_method": "ed25519_manifest_signature",
            "integrity_status": "authenticated",
            "received_timestamp": "2026-07-21T12:05:00Z",
            "raw_provider_metadata": {
                "system_prompt": "You are a secret underwriting bot",
                "customer_weights": [0.1, 0.2],
                "note": "contact jane.doe@example.com about SSN 123-45-6789",
            },
            "packet": {
                "evidence_batch_id": "b1",
                "tenant_id_hash": "t",
                "agent_id": "a",
                "agent_version": "v3",
                "manifest_content_sha256": "sha256:x",
                "events": [
                    {
                        "evidence_event_id": "e1",
                        "event_kind": "data_access",
                        "event_timestamp": "2026-07-21T12:00:00Z",
                        "content_sha256": "sha256:y",
                        "attributes": {"action_class": "data_access"},
                    }
                ],
            },
        }
    )
    norm = normalize_provider_evidence(envelope)
    redactions = set(norm.provenance.redactions)
    assert "raw_provider_metadata.system_prompt" in redactions
    assert "raw_provider_metadata.customer_weights" in redactions
    assert any(r.startswith("raw_provider_metadata.note") for r in redactions)


def test_unknown_integrity_status_fails_closed():
    envelope = ProviderEvidenceEnvelope.from_dict(
        {
            "source_provider_id": "p",
            "provider_stack": "s",
            "attestation_type": "t",
            "attestation_key_id": "k",
            "authentication_method": "m",
            "integrity_status": "totally_trusted_promise",
            "received_timestamp": "2026-07-21T12:05:00Z",
            "packet": {
                "evidence_batch_id": "b",
                "tenant_id_hash": "t",
                "agent_id": "a",
                "agent_version": "v",
                "manifest_content_sha256": "sha256:x",
                "events": [
                    {
                        "evidence_event_id": "e",
                        "event_kind": "k",
                        "event_timestamp": "2026-07-21T12:00:00Z",
                        "content_sha256": "sha256:y",
                        "attributes": {},
                    }
                ],
            },
        }
    )
    with pytest.raises(ProviderNormalizationError):
        normalize_provider_evidence(envelope)


def test_cross_agent_evidence_rejected(engine, fixture_data):
    """Two providers must describe the SAME deployed agent."""
    scenario = fixture_data["scenarios"]["corroboration_candidate"]
    with pytest.raises(Exception):
        engine.decide(
            agent_id="some-other-agent",
            agent_version="v3",
            envelopes=scenario["envelopes"],
            as_of=scenario["as_of"],
        )


def test_no_scoring_surface_imported():
    """Rollback / isolation guard: the cross-provider modules do not import or
    touch BackBond Score / rubric / band-cut modules."""
    import determination.continuity as c
    import determination.provider_normalization as p

    for mod in (c, p):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "rubric.scoring" not in src
        assert "rubric.bands" not in src
        assert "import rubric" not in src


def test_policy_pins_are_stable():
    policy = ContinuityPolicy.load()
    assert policy.version == "continuity-policy@1.0.0"
    assert policy.min_independent_corroborating_sources == 2
    assert policy.freshness_window_seconds == 3600
