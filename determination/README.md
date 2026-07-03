# Determination Engine v1 (BEAAA-11372)

BLUF: turns immutable runtime evidence into reproducible covered-peril
determination records for the scoped wrapper-1 perils, with **no LLM in the
deciding path**. Governing spec: `backbond-runtime-evidence-pipeline-build-spec-2026-07-02` §3.4.B
(BEAAA-11341); parent lane BEAAA-11371. Runs under
`contracts/are-scanner-api-v1.md@v1.3.0` and `contracts/evidence-profile-v1.md`.

This is runtime evidence adjudication infrastructure. It changes no BackBond
Score math, rubric weights, judge models, pricing, or the `/scanner/v1/scans`
response envelope. `scanner_version` / `corpus_version` / `probe-set-version` are
unchanged.

## Pipeline

```
candidate event → optional advisory LLM triage → deterministic rule evaluation
    → determination record → audit spine → human-lane review queue
```

- **Deterministic rule evaluation is the only deciding step.** Advisory triage is
  recorded for reviewer context but is excluded from the determination preimage,
  so `determination_id` and `final_decision` are identical whether or not triage
  runs (`tests/test_determination_engine_beaaa11372.py::test_advisory_triage_never_changes_determination`).
- **Reproducible:** `determination_id = sha256(canonical_preimage)`; the preimage
  binds rule-set version, decision, triggering evidence (event/span ids + content
  hashes), and the immutable evidence packet reference. Same evidence + same
  `rule_set_version` ⇒ same id.
- **Human lane first (graded autonomy):** a matched rule yields a covered-peril
  *candidate* routed to a reviewer, never an auto-bound coverage ruling.

## Modules

| File | Role |
|---|---|
| `rule_dsl.py` | Shared deterministic predicate/canonical-hash library. Also used by `underwriting/automatic_decision.py` (refactored to call into it — no duplicated matcher). |
| `records.py` | `EvidencePacket`, `StructuredEvent`, `DeterminationRecord`, preimage + id. |
| `rules/determination_rules_v1.yaml` | Versioned detection rules for the four scoped perils. |
| `engine.py` | `DeterminationEngine` — evaluate + (flag-gated) emit. |
| `evidence_source.py` | `EvidencePacketSource` port; `FixtureEvidenceSource` (live); `EvidenceStoreReadAdapter` (blocker seam, see below). |
| `audit_spine.py` | Append-only determination book of record (`InMemory` / `Jsonl`). |
| `review_queue.py` | Human-lane review queue keyed by `determination_id`. |
| `migrations/0001_determination_records_v1.sql` | Production append-only audit-spine tables + views. |
| `fixtures/golden/<peril>/{positive,negative,boundary}.json` | Golden evidence packets per peril. |

## Scoped perils (wrapper-1 only)

`wrong_recipient_transaction`, `unauthorized_action`, `data_leak`,
`hallucination_financial_loss` (narrow scope). Expanding this set is a
**rule-change** question for Underwriter + CTO — not an edit to the rules file.

## Feature flag / rollback

Determination emission to the audit spine is gated by `BACKBOND_DETERMINATION_EMIT`
(default off) or an explicit `emit=` on the engine. With emission disabled the
engine still evaluates and returns records for inspection but writes nothing. If
reproducibility or evidence integrity is not defensible, leave emission off and
the runtime evidence plane is unchanged.

## Known dependency seam (first-class blocker)

The engine reads immutable evidence through the `EvidencePacketSource` port. The
**live** binding to the append-only evidence store (BEAAA-11366) —
`EvidenceStoreReadAdapter` — is **not implemented** because the evidence store
ships as Postgres DDL only (`tools/evidence-store/`) with no Python read client
over `evidence_store.latest_signed_batch_manifests` /
`latest_structured_event_summaries` / `evidence_content_hashes`. Until that
tenant-isolated read client exists, v1 runs on `FixtureEvidenceSource` (golden
fixtures + exported-packet replay) and emission stays behind the flag. Tracked as
a blocker on BEAAA-11372.
