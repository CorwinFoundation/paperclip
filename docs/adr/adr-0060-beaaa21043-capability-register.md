# ADR-0060: Scanner Capability Register authority and publication eligibility

Decision: adopt `config/scanner_capability_register.v1.json` as the sole source for typed Scanner inventory counts and generate five deterministic views bound to its SHA-256 receipt. A claim is production-active only when one signed full-scan manifest records equal non-negative integers for `expected`, `attempted`, `terminal`, and `evidenced`.

This is a rule change under `scanner-implementation-supervision-v1@v1.1`; CTO classification is recorded on BEAAA-21052. The register changes publication eligibility and evidence authority. It does not change `POST /scanner/v1/scans`, `POST /scanner/v1/rescans`, scoring, bands, rubric points, judge models, or the contracted API envelope, so no API contract version bump or Underwriter co-sign is required unless the implementation later crosses that boundary.

The canonical source owns semantics. `dictionary_records.json`, `marketing_claims.json`, `executive_summary.md`, `inactive_backlog.csv`, and `register_receipt.json` are projections and may not add eligibility, scoring, or evidence rules. Every projection carries the same `register_receipt_id`; the receipt records exact hashes for the other four views.

Failure behavior is fail-closed. Missing fields, invalid enums, duplicate IDs, stale receipt IDs, unsupported manual claims, unequal manifest counts, or absent signature references reject generation or production-active status. The 278 observed catalog rows and 280 expected boundary remain separate records with an explicit two-row unresolved delta. The historical 1,180 count is rejected.

Rollback: stop publishing generated views and retain the last accepted immutable receipt. Existing Scanner API behavior and scoring remain unchanged.

Metadata: `scanner_version`, `corpus_version`, and `probe-set-version` unchanged; `rubric_point_id: none`; `cto_cosign_ref: BEAAA-21052 plus pending exact-candidate ADR co-sign`; N=5 golden-set delta required before merge.
