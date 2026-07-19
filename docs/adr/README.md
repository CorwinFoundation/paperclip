---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The CTO owns one collision-free BackBond Scanner ADR record and its assembled current view."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-15620", "BEAAA-15604"]
proof_points_cited: ["docs/adr/current-contract-view.md"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["No scoring, rubric, corpus, band, contract, or production change; knowledge-record repair only."]
---

# BackBond Scanner ADR record

## Authority and owner

`docs/adr/` is the only canonical ADR directory for the BackBond Scanner repository. The CTO owns number allocation, collision repair, status changes, supersession links, and the assembled current view. Ticket authors own technical content until acceptance; merging an ADR into this directory transfers record stewardship to the CTO.

Snapshots, worktrees, issue attachments, and recovery archives are evidence sources, not competing ADR directories. Preserve them under the applicable reconciliation protocol until merge and checksum verification.

## Allocation rule

1. Before drafting, inspect committed filenames matching `docs/adr/adr-NNNN-*.md` on canonical `origin/master`.
2. Reserve the lowest integer greater than every committed allocation. Never fill a gap and never infer availability from a feature worktree.
3. Record the reservation on the originating issue before creating the file. The reservation is owned by the issue, not by a branch.
4. Use `adr-NNNN-<ticket>-<slug>.md`; the H1 must repeat the same number.
5. Before merge, verify that every numeric prefix is unique and that filename/H1 agree. A collision blocks merge.
6. Existing accepted numbers are immutable. If an unmerged or later record collides, renumber the later record and retain its former filename in `prior_version` or migration notes.

## Repaired allocation

- `ADR-0061` remains assigned to the earlier accepted release-reproducibility gate from 2026-07-16.
- The later N/A-rating boundary formerly numbered `ADR-0061` is reassigned to `ADR-0064`; its content and prior-version lineage are unchanged.
- Next allocation after this repair: `ADR-0065`, subject to rechecking canonical `origin/master` immediately before reservation.

## Consumption rule

Start with [the assembled current-contract view](current-contract-view.md). Read an individual ADR for decision rationale, rejected alternatives, proof, or rollback detail. Do not assemble current behavior by hand from issue patches or branch snapshots.
