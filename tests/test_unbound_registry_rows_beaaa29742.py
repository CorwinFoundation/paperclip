"""BEAAA-29742: no executable registry row may reach the unbound-adapter raise.

This is the third time ``registered case adapter not bound`` reached a live
scan (BEAAA-21723, BEAAA-29085, and now the BEAAA-29082 controlled full scan on
master ``79c5d1f1``). The first two were fixed family by family. This sweep
walks the whole registry instead, so a fourth family cannot ship the same way.

It deliberately drives the real ``_execute_case`` dispatch ladder rather than
re-stating its branch conditions: a static mirror of the ladder is exactly the
kind of thing that drifts from the ladder it is supposed to guard. Every
exception other than the unbound-adapter raise is tolerated here, because a
missing fixture or a stub response the adapter dislikes still proves the row
reached a bound adapter, which is all this test claims.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

import pytest

from harness.runner import MockRunner
from tools.active_test_registry import (
    UNBOUND_REGISTERED_CASE_FAMILIES,
    build_registry,
    count_canonical_scored_rows,
    is_canonical_scored_row,
)
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)

UNBOUND_RAISE = "registered case adapter not bound"
_PILOT_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "harness"
    / "fixtures"
    / "p4_attestation_pilot_v1.json"
)
_ATTESTATION_NOW = dt.date(2026, 5, 22)


def _pilot_attestation_form() -> dict:
    return json.loads(_PILOT_FIXTURE.read_text())

# The six the BEAAA-29082 live scan named, plus the three more the sweep below
# found. Gating them changes no score -- see
# test_gating_does_not_move_the_canonical_scored_row_count, which measures that
# rather than asserting it. Fifty of the flipped rows do carry
# ``reporting_status: scored`` (BEAAA-29777 QA finding); scored reporting
# status is not the same predicate as the canonical scored-row inventory.
BEAAA_29742_FAMILIES = {
    "demographic_parity",
    "equal_opportunity",
    "eval_representation",
    "pii_attestation",
    "sentiment_skew",
    "voice_adversarial_pack",
}
# Empty, and it must stay empty. ``pii_attestation`` was the one member while
# its disposition was escalated; the CTO ruled on BEAAA-29746 (option (b),
# co-signed) that gating it is rule-execution, because its score is produced by
# harness/p4_attestation_adapter.py and not by this registry row. The xfail that
# stood here is replaced by
# test_gated_pii_attestation_still_scores_in_the_contract_envelope below, which
# is a stronger claim: not "we know this one is broken" but "the dim survives
# the gate". Anything added here needs a fresh ruling, not a code review.
CTO_DECISION_PENDING: set[str] = set()


def _executable_rows() -> list[dict]:
    return [
        row
        for row in build_registry()
        if row.get("status") == "active" and row.get("production_runner_executable")
    ]


def _first_row_per_family() -> list[dict]:
    seen: dict[str, dict] = {}
    for row in _executable_rows():
        seen.setdefault(row["test_id"], row)
    return [seen[test_id] for test_id in sorted(seen)]


FAMILY_ROWS = _first_row_per_family()


@pytest.mark.parametrize(
    "registry_row", FAMILY_ROWS, ids=[row["test_id"] for row in FAMILY_ROWS]
)
def test_every_executable_family_reaches_a_bound_adapter(registry_row, tmp_path):
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: "I cannot help with that."),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    try:
        executor._execute_case(
            registry_row["test_id"],
            registry_row["case_id"],
            registry_row,
        )
    except RuntimeError as error:  # pragma: no cover - message check only
        assert UNBOUND_RAISE not in str(error), (
            f"{registry_row['test_id']} is production_runner_executable but no "
            f"adapter branch claims it: {error}"
        )
    except Exception:
        # Any other failure is an adapter's own business, not a binding defect.
        pass


def test_the_beaaa29742_families_are_gated_out_of_production_execution():
    rows = [
        row
        for row in build_registry()
        if row.get("status") == "active"
        and row["test_id"] in BEAAA_29742_FAMILIES
    ]

    assert Counter(row["test_id"] for row in rows) == {
        "voice_adversarial_pack": 24,
        "sentiment_skew": 3,
        "demographic_parity": 2,
        "equal_opportunity": 1,
        "eval_representation": 1,
        "pii_attestation": 1,
    }
    assert not any(row["production_runner_executable"] for row in rows)
    assert BEAAA_29742_FAMILIES <= set(UNBOUND_REGISTERED_CASE_FAMILIES)
    # Not vacuous by accident: the allowlist is empty and must stay empty.
    assert CTO_DECISION_PENDING == set()


def test_executable_and_runtime_identity_counts_are_reported_separately():
    rows = _executable_rows()
    identities = {
        row.get("runtime_case_identity")
        for row in rows
        if row.get("runtime_case_identity")
    }

    # Two distinct numbers by design, reported separately per the BEAAA-29742
    # definition of done: executable rows 2,314 -> 2,122 (164 named by the live
    # scan, 27 more the sweep found, and pii_attestation after the BEAAA-29746
    # ruling) and distinct runtime case identities 1,999 -> 1,907. BEAAA-29957
    # then binds 40 CBRN rows; 20 content-candidate rows share existing runtime
    # identities, so the two censuses move by 40 and 20 respectively.
    #
    # The identity count does NOT move when pii_attestation is gated, and that
    # is not an oversight. Its runtime_case_identity is None, so it never
    # entered this set to begin with -- which is independent corroboration of
    # the ruling's finding that the row is a synthetic execution surface that
    # never ran. Rows sharing an identity are replicates of one case, so the
    # two counts never coincide and neither substitutes for the other.
    assert len(rows) == 2_322
    assert len(identities) == 2_007


def test_gated_pii_attestation_still_scores_in_the_contract_envelope():
    """BEAAA-29746 condition 2: gating the census row must not drop the dim.

    This is the assertion that replaced the ``CTO_DECISION_PENDING`` xfail. The
    whole basis of the CTO's co-sign is that ``GOVERNANCE.PII.HANDLING_ATTESTATION``
    is scored by ``harness/p4_attestation_adapter.py`` rather than by the
    registry row gated above. If that ever stops being true, gating the row
    silently drops a scored dim -- so prove the dim survives instead of
    assuming it.
    """
    from harness import p4_attestation_adapter as adapter

    registry_rows = [
        row
        for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "pii_attestation"
    ]
    assert registry_rows, "the pii_attestation row should still exist, just not execute"
    assert not any(row["production_runner_executable"] for row in registry_rows)

    result = adapter.attestation_to_measurements(
        _pilot_attestation_form(),
        pii_evidence_pack={
            "policy_text": "s3://attest/policy.pdf",
            "signed_dpa": True,
            "dpia": True,
            "dsar_runbook": "s3://attest/dsar.pdf",
            "dsar_sla_days": 15,
            "breach_sla_72h": 24,
            "third_party_audit": "SOC2_TYPE_II",
        },
        pii_substrate_raw=4.0,
        now=_ATTESTATION_NOW,
    )

    emitted = {m["dim_id"]: m for m in result.measurements}
    assert "GOVERNANCE.PII.HANDLING_ATTESTATION" in emitted, (
        "gating the registry row dropped the scored dim out of the envelope"
    )
    pii = emitted["GOVERNANCE.PII.HANDLING_ATTESTATION"]
    assert pii["benchmark"] == adapter.PII_ATTESTATION_BENCHMARK_LABEL
    # Graded, not merely present: a null/absent raw would be a silent drop too.
    assert pii["raw"] == 4.0
    assert result.scored["dims"]["GOVERNANCE.PII.HANDLING_ATTESTATION"]["raw"] == 4.0

    # The production envelope path imports this adapter, so the dim reaches the
    # emitted contract envelope and not just a script-only caller.
    from harness import contract_envelope

    assert contract_envelope.PII_ATTESTATION_BENCHMARK_LABEL == (
        adapter.PII_ATTESTATION_BENCHMARK_LABEL
    )


# BEAAA-29777. QA found that 50 of the 192 gated rows carried base
# ``reporting_status: scored``, against a comment claiming all of them were
# reported_not_scored. The comment was wrong; the gate is not. Pin the number
# the claim actually rests on so a future widening of the frozenset cannot
# quietly drop a canonical scored row out of the inventory.
CANONICAL_SCORED_ROWS_AT_79C5D1F1 = 431


def test_gating_does_not_move_the_canonical_scored_row_count():
    rows = build_registry()

    assert count_canonical_scored_rows(rows) == CANONICAL_SCORED_ROWS_AT_79C5D1F1

    gated_scored = [
        row
        for row in rows
        if row.get("test_id") in UNBOUND_REGISTERED_CASE_FAMILIES
        and row.get("reporting_status") == "scored"
    ]
    # Not vacuous: these rows exist, they are gated, and they are still counted.
    assert len(gated_scored) == 13
    assert all(row["production_runner_executable"] is False for row in gated_scored)
    assert sum(1 for row in gated_scored if is_canonical_scored_row(row)) == 12

    # The mechanism behind the invariant: the gate writes exactly two fields,
    # and neither is one of the five is_canonical_scored_row reads.
    for row in gated_scored:
        assert row["execution_evidence_status"] == "registered_case_adapter_not_bound"
