---
artifact_type: adr
owner: ceo
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "A single authoritative Brand Backbone pin keeps every external BackBond asset provably consistent with the scanner's true 27-dimension figure."
contracts_referenced: []
ticket_refs: ["BEAAA-15425", "BEAAA-15424", "BEAAA-15377"]
proof_points_cited: []
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-15425 — Brand Backbone pin conflict resolution

**Decision (Founder ruling, 2026-07-18):** Brand Backbone **v1.6**, SHA-256
`8aa5421c3b6629afa9ea27c5f6026441e3ce2a26ae3c0dea42a2ceba7a084e7b` is authoritative.
v1.5 / `b1ae308e0f150ff5d0a6bdf05071ee98540e296bb3a86a4626b03041a266838f` is superseded —
it predates the BEAAA-15377 25→27 risk-dimension correction and must not be re-pinned.

## Root cause of the conflict
BEAAA-15424 could not package its external candidate because it read a "v1.5 mandate"
while `scripts/verify-brand-backbone.sh` returned v1.6. Investigation:
- The v1.6 verifier + pin bundle was sitting as **uncommitted workspace changes** (from BEAAA-15377), so it looked "floating."
- The cited "v1.5 mandate" lived in the agent-instruction files (`AGENTS.md`, `artifact-metadata-schema.md`), not in `BACKBOND_COMPANY_CANON.md`. Those instruction files were cascaded to v1.6 earlier under BEAAA-15425.
- `BACKBOND_COMPANY_CANON.md` itself named **no** Brand Backbone version, but did still carry the stale "25 risk dimensions" figure.

## Resolution actions
1. Committed the v1.6 gate bundle atomically as `896ff8c` (`.pdf`, `.sha256`, `brand/CHANGELOG.md`, `brand/README.md`, `scripts/verify-brand-backbone.sh`). Rollback = `git revert 896ff8c`.
2. Made `BACKBOND_COMPANY_CANON.md` explicit: added an authoritative Brand Backbone pin block (v1.6 / `8aa5421c…`) and corrected "25 risk dimensions" → "27 risk dimensions".
3. Instruction files (`AGENTS.md`, `artifact-metadata-schema.md`) already read v1.6 / `8aa5421c…`; no v1.5 references remain anywhere.

## Acceptance — verified
- `scripts/verify-brand-backbone.sh` → `OK: Brand Backbone v1.6 SHA matches pin (8aa5421c...)`, exit 0.
- Pinned PDF actual SHA = `8aa5421c…` = pin file = canon = instruction files. All four agree.

## Unblocks
BEAAA-15424: CTO packages the immutable candidate (record commit SHA + new quota ceiling)
→ QA cd1e82a0 full verification + regression → QA exact-word `APPROVED` → Founder sign-off
→ only then promote and confirm the ceiling on the live tunnel. No self-promotion.
