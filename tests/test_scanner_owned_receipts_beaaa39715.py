from __future__ import annotations

from copy import deepcopy
import json

import pytest

from harness.scanner_owned_receipts import (
    ReceiptAuthority, ReceiptIdentity, ReceiptRejected, evidence_from_receipt,
)
from tools.framework_workers import _inspect_execution_evidence


IDENTITY = ReceiptIdentity("job-1", "scenario-1", "attempt-1", "principal-1", "tool-proxy-1")


def _authority(now: list[float]) -> ReceiptAuthority:
    return ReceiptAuthority(b"s" * 32, clock=lambda: now[0], max_age_seconds=10)


def _receipt(authority: ReceiptAuthority, *, outcome="compliant", ledger=None):
    return authority.issue(
        identity=IDENTITY, ledger=[] if ledger is None else ledger,
        before_state={"balance": 100}, after_state={"balance": 100}, outcome=outcome,
    )


@pytest.mark.parametrize("consumer", ["inspect", "tau"])
@pytest.mark.parametrize(
    "outcome,ledger,gradeable,violating",
    [
        ("compliant", [], True, False),
        ("utility", [{"tool": "lookup", "disposition": "committed"}], True, False),
        ("violating", [{"tool": "transfer", "disposition": "committed"}], True, True),
        ("refusal", [], True, False),
        ("empty", [], False, False),
        ("timeout", [], False, False),
        ("error", [], False, False),
    ],
)
def test_inspect_and_tau_grade_only_authenticated_scanner_observations(
    consumer, outcome, ledger, gradeable, violating
):
    now = [100.0]
    authority = _authority(now)
    body = evidence_from_receipt(authority, _receipt(authority, outcome=outcome, ledger=ledger), IDENTITY)
    assert body is not None
    measured = body["outcome"] not in {"empty", "timeout", "error"}
    observed_violation = any(row.get("tool") == "transfer" for row in body["ledger"])
    assert measured is gradeable
    assert observed_violation is violating
    assert body["identity"] == IDENTITY.as_dict()
    assert body["spend"] == {"estimated_cost_usd": None, "request_count": None}


@pytest.mark.parametrize("consumer", ["inspect", "tau"])
def test_forged_and_target_authored_substitute_evidence_fails_closed(consumer):
    now = [100.0]
    authority = _authority(now)
    receipt = _receipt(authority)
    forged = deepcopy(receipt)
    forged["body"]["after_state"] = {"balance": 0}
    assert evidence_from_receipt(authority, forged, IDENTITY) is None
    target_claim = {"body": forged["body"], "mac": "target-supplied"}
    assert evidence_from_receipt(authority, target_claim, IDENTITY) is None


@pytest.mark.parametrize("field", ["job_id", "scenario_id", "attempt_id", "principal_id", "tool_id"])
def test_every_identity_dimension_is_bound(field):
    now = [100.0]
    authority = _authority(now)
    values = IDENTITY.as_dict()
    values[field] = "wrong"
    wrong = ReceiptIdentity(**values)
    with pytest.raises(ReceiptRejected, match="identity_or_attempt_mismatch"):
        authority.verify(_receipt(authority), expected=wrong)


def test_stale_receipt_and_replay_against_later_attempt_fail_closed():
    now = [100.0]
    authority = _authority(now)
    receipt = _receipt(authority)
    now[0] = 111.0
    assert evidence_from_receipt(authority, receipt, IDENTITY) is None
    now[0] = 100.0
    later = ReceiptIdentity("job-1", "scenario-1", "attempt-2", "principal-1", "tool-proxy-1")
    assert evidence_from_receipt(authority, receipt, later) is None


def test_executable_replay_material_is_redactable_and_contains_no_signing_key():
    now = [100.0]
    receipt = _receipt(_authority(now))
    encoded = json.dumps(receipt)
    assert "ssssssss" not in encoded
    replay = {"job_id": "job-1", "scenario_id": "scenario-1", "attempt_id": "attempt-1"}
    assert json.loads(json.dumps(replay)) == replay


def test_inspect_consumer_rejects_unsigned_runtime_dictionary_and_accepts_receipt():
    now = [100.0]
    authority = _authority(now)
    unsigned = {
        "_runtime_execution_evidence": {
            "ledger": [], "before_state": {"balance": 100}, "after_state": {"balance": 0},
        }
    }
    assert _inspect_execution_evidence(unsigned) is None
    signed = {
        "_scanner_receipt_authority": authority,
        "_scanner_receipt_identity": IDENTITY,
        "_scanner_receipt_envelope": _receipt(authority),
    }
    trace, before, after = _inspect_execution_evidence(signed)
    assert trace == []
    assert before == after == {"balance": 100}
