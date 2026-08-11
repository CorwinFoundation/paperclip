---
artifact_type: runbook
owner: scanner-engineer
persona_audience: internal
canonical_terms_only: true
ticket_refs: ["BEAAA-21634"]
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.0"]
external_scope: out
---

# Canonical BackBond Scanner report renderer

BLUF: only the renderer in the canonical Scanner project is authorized for current report generation. Every other local `report_card/render.py` copy is quarantined by classification as internal historical evidence only. This control does not delete or modify a worktree.

## Allowlist

The machine-readable allowlist is `config/canonical-report-renderers.txt`. Its sole authorized repository root is:

```
/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner
```

The allowlist is intentionally path-based. A matching file hash in another worktree does not make that worktree canonical.

## Inventory and quarantine label

Run:

```sh
scripts/audit-local-report-renderers.sh > /tmp/scanner-report-renderers.tsv
```

The audit emits one of two classifications:

- `CANONICAL`: the resolved repository root appears in the allowlist.
- `QUARANTINED_INTERNAL_HISTORICAL_EVIDENCE_ONLY`: the copy may be retained for provenance, but it must not generate, publish, demonstrate, or promote a current BackBond Report.

The `brand_verifier` column is diagnostic. A verifier's presence does not authorize a clone. Its absence is an additional reason the clone cannot produce external-bound work.

## Operating rule

Before report generation, resolve the repository root and require a `CANONICAL` audit row. External-bound report work must also satisfy the current Brand Backbone, design, Designer-validation, QA, and promotion gates. Deletion or destructive cleanup requires separate target-specific approval.
