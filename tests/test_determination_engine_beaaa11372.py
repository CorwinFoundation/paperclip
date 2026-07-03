"""BEAAA-11372 Determination Engine v1 tests.

Locks the acceptance criteria: versioned rules with golden fixtures per scoped
peril (positive/negative/boundary); every determination reproducible from rule
version + immutable evidence alone; no LLM in the deciding path; determinations
write to the append-only audit spine with full evidence packet references; the
human-lane review queue is operational.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from determination.audit_spine import InMemoryAuditSpine, JsonlAuditSpine
from determination.engine import DeterminationEngine, DeterminationRuleSet
from determination.evidence_source import (
    EvidenceStoreReadAdapter,
    FixtureEvidenceSource,
)
from determination.records import (
    DECISION_COVERED_CANDIDATE,
    DECISION_INSUFFICIENT_EVIDENCE,
    DECISION_NO_FINDING,
    EvidencePacket,
    determination_id,
    determination_preimage,
)
from determination.review_queue import STATUS_RESOLVED, InMemoryReviewQueue

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "determination" / "fixtures" / "golden"
MIGRATION = (
    REPO / "determination" / "migrations" / "0001_determination_records_v1.sql"
)

PERILS = [
    "wrong_recipient_transaction",
    "unauthorized_action",
    "data_leak",
    "hallucination_financial_loss",
]

RULE_ID_BY_PERIL = {
    "wrong_recipient_transaction": ("DET-WRT-001", "DET-WRT-001B"),
    "unauthorized_action": ("DET-UNA-001", "DET-UNA-001B"),
    "data_leak": ("DET-DLK-001", "DET-DLK-001B"),
    "hallucination_financial_loss": ("DET-HFL-001", "DET-HFL-001B"),
}


def _packet(peril: str, case: str) -> EvidencePacket:
    data = json.loads((GOLDEN / peril / f"{case}.json").read_text())
    return EvidencePacket.from_dict(data)


def _engine() -> DeterminationEngine:
    return DeterminationEngine(emit=False)


# --- Rule set ---------------------------------------------------------------


def test_rule_set_is_versioned_and_covers_every_scoped_peril():
    rs = DeterminationRuleSet.load()
    assert rs.version == "determination-rules-wrapper1@1.0.0"
    covered = {r["peril"] for r in rs.rules}
    assert covered == set(PERILS)


# --- Per-peril golden coverage ---------------------------------------------


@pytest.mark.parametrize("peril", PERILS)
def test_positive_fixture_flags_covered_candidate(peril):
    records = _engine().evaluate(_packet(peril, "positive"))
    assert len(records) == 1
    record = records[0]
    assert record.final_decision == DECISION_COVERED_CANDIDATE
    assert record.peril == peril
    assert record.triggered_rule_id == RULE_ID_BY_PERIL[peril][0]
    assert record.review_required is True
    # Full evidence packet reference + content hashes are present.
    assert record.evidence_packet_ref["evidence_batch_id"]
    assert record.evidence_packet_ref["tenant_id_hash"] == "tenant-hash-alpha"
    assert record.content_hashes
    assert record.source_spans[0]["content_sha256"].startswith("sha256:")


@pytest.mark.parametrize("peril", PERILS)
def test_negative_fixture_yields_no_finding(peril):
    records = _engine().evaluate(_packet(peril, "negative"))
    assert len(records) == 1
    assert records[0].final_decision == DECISION_NO_FINDING
    assert records[0].peril is None
    assert records[0].review_required is False


@pytest.mark.parametrize("peril", PERILS)
def test_boundary_fixture_refers_insufficient_evidence(peril):
    records = _engine().evaluate(_packet(peril, "boundary"))
    assert len(records) == 1
    record = records[0]
    assert record.final_decision == DECISION_INSUFFICIENT_EVIDENCE
    assert record.peril == peril
    assert record.triggered_rule_id == RULE_ID_BY_PERIL[peril][1]
    assert record.review_required is True


# --- Reproducibility: rule version + immutable evidence alone ---------------


@pytest.mark.parametrize("peril", PERILS)
@pytest.mark.parametrize("case", ["positive", "negative", "boundary"])
def test_determination_is_reproducible_from_evidence_and_rule_version(peril, case):
    packet = _packet(peril, case)
    first = _engine().evaluate(packet)
    # A fresh engine + a re-parsed packet reproduce the identical determination.
    reparsed = EvidencePacket.from_dict(
        json.loads((GOLDEN / peril / f"{case}.json").read_text())
    )
    second = DeterminationEngine(emit=False).evaluate(reparsed)
    assert [r.determination_id for r in first] == [r.determination_id for r in second]


@pytest.mark.parametrize("peril", PERILS)
def test_determination_id_recomputes_from_persisted_record_fields(peril):
    record = _engine().evaluate(_packet(peril, "positive"))[0]
    # Rebuild the preimage purely from the persisted record and re-hash it.
    preimage = determination_preimage(
        rule_set_version=record.rule_set_version,
        engine_version=record.engine_version,
        peril=record.peril,
        triggered_rule_id=record.triggered_rule_id,
        final_decision=record.final_decision,
        trigger=record.trigger,
        source_spans=[dict(s) for s in record.source_spans],
        content_hashes=list(record.content_hashes),
        evidence_packet_ref=record.evidence_packet_ref,
        policy_engine_check=record.policy_engine_check,
    )
    assert determination_id(preimage) == record.determination_id


# --- No LLM in the deciding path -------------------------------------------


@pytest.mark.parametrize("peril", PERILS)
def test_advisory_triage_never_changes_determination(peril):
    packet = _packet(peril, "positive")
    without = DeterminationEngine(emit=False).evaluate(packet)[0]

    calls = {"n": 0}

    def rogue_triage(_packet, _event):
        calls["n"] += 1
        return {"llm_verdict": "definitely not covered", "confidence": 0.99}

    with_triage = DeterminationEngine(
        emit=False, advisory_triage=rogue_triage
    ).evaluate(packet)[0]

    # Advisory triage ran, is recorded, and is flagged non-deciding...
    assert calls["n"] == 1
    assert with_triage.advisory_triage is not None
    assert with_triage.advisory_triage["excluded_from_deciding_path"] is True
    # ...but the id and decision are byte-identical to the no-triage run.
    assert with_triage.determination_id == without.determination_id
    assert with_triage.final_decision == without.final_decision


# --- Audit spine (append-only) + feature flag ------------------------------


def test_emission_is_gated_by_feature_flag():
    packet = _packet("wrong_recipient_transaction", "positive")
    spine = InMemoryAuditSpine()
    # Flag off (default): evaluate but never write.
    off = DeterminationEngine(emit=False, audit_spine=spine)
    off.evaluate_and_emit(packet)
    assert spine.records == ()
    # Flag on: determinations land in the spine.
    on = DeterminationEngine(emit=True, audit_spine=spine)
    on.evaluate_and_emit(packet)
    assert len(spine.records) == 1
    assert spine.records[0].final_decision == DECISION_COVERED_CANDIDATE


def test_jsonl_audit_spine_round_trips_with_packet_reference(tmp_path):
    spine = JsonlAuditSpine(tmp_path / "spine.jsonl")
    engine = DeterminationEngine(emit=True, audit_spine=spine)
    engine.evaluate_and_emit(_packet("data_leak", "positive"))
    rows = spine.read_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["peril"] == "data_leak"
    assert row["evidence_packet_ref"]["manifest_content_sha256"]
    assert row["content_hashes"]


# --- Human-lane review queue -----------------------------------------------


def test_review_queue_routes_candidates_and_referrals_not_no_findings():
    queue = InMemoryReviewQueue()
    engine = _engine()
    queue.enqueue_all(engine.evaluate(_packet("unauthorized_action", "positive")))
    queue.enqueue_all(engine.evaluate(_packet("data_leak", "boundary")))
    # No-finding records are book-of-record only; they are not routed.
    queue.enqueue_all(engine.evaluate(_packet("data_leak", "negative")))
    pending = queue.pending()
    assert len(pending) == 2
    assert {e.final_decision for e in pending} == {
        DECISION_COVERED_CANDIDATE,
        DECISION_INSUFFICIENT_EVIDENCE,
    }


def test_review_queue_is_idempotent_and_supports_resolution():
    queue = InMemoryReviewQueue()
    records = _engine().evaluate(_packet("wrong_recipient_transaction", "positive"))
    queue.enqueue_all(records)
    queue.enqueue_all(records)  # re-enqueue must not duplicate
    assert len(queue.all_entries()) == 1
    det_id = records[0].determination_id
    queue.claim(det_id, reviewer="uw-reviewer-1")
    resolved = queue.resolve(
        det_id, reviewer="uw-reviewer-1", ruling={"decision": "covered", "note": "confirmed"}
    )
    assert resolved.status == STATUS_RESOLVED
    assert resolved.resolution["decision"] == "covered"
    assert queue.pending() == ()


# --- Evidence source port ---------------------------------------------------


def test_fixture_source_is_tenant_and_batch_isolated():
    data = json.loads(
        (GOLDEN / "data_leak" / "positive.json").read_text()
    )
    source = FixtureEvidenceSource([data])
    packet = source.read_packet(
        tenant_id_hash="tenant-hash-alpha",
        agent_id="agent-smith-ai-001",
        agent_version="v1",
        evidence_batch_id="batch-dlk-pos",
    )
    assert packet.evidence_batch_id == "batch-dlk-pos"
    with pytest.raises(KeyError):
        source.read_packet(
            tenant_id_hash="tenant-hash-OTHER",
            agent_id="agent-smith-ai-001",
            agent_version="v1",
            evidence_batch_id="batch-dlk-pos",
        )


def test_live_evidence_store_read_adapter_is_a_named_blocker_seam():
    # v1 does not have the live Postgres read client; the seam raises clearly.
    with pytest.raises(NotImplementedError) as exc:
        EvidenceStoreReadAdapter().read_packet()
    assert "evidence_store.latest" in str(exc.value)


# --- Append-only determination audit-spine migration invariants -------------


def test_determination_migration_is_append_only_and_tenant_isolated():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS determination" in sql
    assert "CREATE TABLE IF NOT EXISTS determination.determination_records" in sql
    for col in ("tenant_id_hash", "agent_id", "agent_version", "evidence_batch_id",
                "manifest_content_sha256", "rule_set_version", "final_decision"):
        assert col in sql
    assert "BEFORE UPDATE OR DELETE ON determination.determination_records" in sql
    assert "determination_append_only_update_forbidden" in sql
    assert "determination_append_only_delete_forbidden" in sql
    assert "latest_determination_records" in sql
    assert "ux_determination_records_one_direct_correction" in sql
