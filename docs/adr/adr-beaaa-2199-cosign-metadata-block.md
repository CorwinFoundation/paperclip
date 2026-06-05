---
adr_id: ADR-BEAAA-2199
title: "`cosign_metadata` block at the contract output surface (auditor-readable cosign trail)"
status: Accepted
date: 2026-06-05
contract_referenced: "are-scanner-api-v1@v1.2.1 → v1.3.0"
supervision_contract: "scanner-implementation-supervision-v1@v1.0"
classification: rule-change
ticket_refs: ["BEAAA-2199", "BEAAA-1686", "BEAAA-2262", "BEAAA-2175", "BEAAA-794", "BEAAA-1683", "BEAAA-1721"]
cosign_refs:
  cto_design: "/BEAAA/issues/BEAAA-2199#comment-ca7071d9"
  uw_op_seat: "/BEAAA/issues/BEAAA-2262#comment-e59a5d3b"
  cto_merge: "pending — PR-submission comment on BEAAA-2199"
scanner_baseline: "0.3.10"
scanner_target: "0.3.11"
contract_baseline: "are-scanner-api-v1@v1.2.1"
contract_target: "are-scanner-api-v1@v1.3.0"
master_baseline: "52c003e"
---

# ADR-BEAAA-2199 — `cosign_metadata` block at the contract output surface

## §1 — BLUF

Add one additive top-level `cosign_metadata` block to every scanner
response surface (`POST /scanner/v1/scans`, `POST /scanner/v1/rescans`,
and the `bin/are-scan` CLI JSON output) so the Auditor and the
Underwriter intake can verify rule-change co-signs (CTO ticket / HoUW
ticket / Founder ticket / ADR ref) at the envelope surface without
spelunking per-probe `_summary.json` artifacts. The block is sourced
from `harness.cosign_manifest.build_cosign_metadata(...)`, composed in
`harness.contract_envelope.to_contract_output`, and snapshot-hash
sealed by construction. Field set crosses the v1.2.x patch seam — the
new top-level key is a field-set change — so the contract bump is
minor (v1.2.1 → v1.3.0), per Underwriter op-seat call on
[BEAAA-2262](/BEAAA/issues/BEAAA-2262).

## §2 — Classification

**Rule-change** per `contracts/scanner-implementation-supervision-v1@v1.0`
table row "`bin/are-scan` CLI behavior change that affects output JSON
shape — Rule change, YES + ADR (contract version bump)". The same row
the CTO cited in the
[BEAAA-2175 PR-A v1 rejection 954754f5](/BEAAA/issues/BEAAA-2175#comment-954754f5-4ef7-4cba-ad00-3465d4beb7e1)
when diagnosing this exact symbol as out-of-scope for that PR.

CTO design co-sign pre-code AND pre-merge required. UW op-seat co-sign
required on the contract doc bump direction per
`are-scanner-api-v1@v1.2.1 §Approval gates` (contract output surface
change). HoUW gate not triggered — no substantive scoring / rubric /
applicability-policy axis is touched.

## §3 — Scope (4 files)

| File | Change | Surface |
|---|---|---|
| `harness/cosign_manifest.py` | NEW, tracked (156 LOC) | Static `SCANNER_COSIGN_MANIFEST` list of rule-change entries (today: BEAAA-794 `ROBUSTNESS.TOOL_MISUSE.*` rubric activation + BEAAA-1683 WS-B sanitization-layer tamper-resistance strengthening); `build_cosign_metadata(*, scanner_version, contract_version, rubric_version, applicability_policy_version) -> dict[str, Any]` factory. |
| `harness/contract_envelope.py` | additive import + 1 new top-level key in `contract_output.update({...})` (4 lines) | Composes the block via the factory; `score_result.get("rubric_version")` and `score_result.get("applicability_policy_version")` flow from `rubric/scoring.py` (both already populated on `score_result`). |
| `contracts/are-scanner-api-v1.md` | v1.2.1 → v1.3.0; new §cosign_metadata section; §Versioning history entry; frontmatter refresh | Documents the block shape, key set, snapshot-hash inclusion, pre-v1.3.0 consumer compatibility, customer-facing adapter rule, and the BEAAA-1721 stale-`CONTRACT_VERSION`-constant known issue. |
| `tests/test_cosign_metadata_beaaa2199.py` | NEW | Asserts `cosign_metadata` present at top level on `bin/are-scan` JSON output of a B1 fixture run; asserts key set + per-entry key set + non-empty `rule_changes`. |

Bump: `scanners.__init__.SCANNER_VERSION` 0.3.10 → 0.3.11 per CTO
judgment ([comment ca7071d9](/BEAAA/issues/BEAAA-2199#comment-ca7071d9)
Ask 4): envelope-shape change requires a `scanner_version` bump so
consumers that pin `scanner_version` for envelope reproducibility can
distinguish 0.3.10 (no block) from 0.3.11+ (with block).

## §4 — Why this is additive, not breaking

1. **Field set: +1 top-level key (`cosign_metadata`).** No existing
   field renamed, removed, or semantically shifted. v1.2.1 consumers
   that ignore the new key receive byte-identical envelope content
   relative to their v1.2.1 pin (verified by N=5 full-dict calibration
   in §6).
2. **Scoring math: untouched.** No edit to `rubric/scoring.py`. The
   `are_total`, pillar subscores, band, confidence, and evidence_refs
   shapes and values are produced by exactly the same callee chain
   (`rubric.scoring.score` → `harness.contract_envelope.to_contract_output`).
3. **Judge model / judge prompt: untouched.** No probe-pack edit, no
   judge-model substitution, no judge-prompt edit.
4. **Tamper-resistance: preserved by construction.** `snapshot_hash`
   continues to sign the full envelope (the RFC 8785 JCS canonical
   preimage is whole envelope minus the three rescan-only volatile
   keys `{snapshot_hash, forensic_bundle_uri, purpose_aware_sla}` per
   `are-scanner-api-v1 §Canonical-JSON serialization`). The
   `cosign_metadata` block is sealed inside the envelope; auditors
   cannot have the block tampered post-write without invalidating the
   hash.
5. **JCS canonicalization domain: unchanged.** No edit to
   `api/jcs.py:canonicalize()`; the cosign_metadata block is a
   plain-JSON nested object with strings, bools, and arrays — fully
   in-domain.

## §5 — `scanner_version` bump (0.3.10 → 0.3.11) — rationale

Per CTO judgment ([BEAAA-2199 comment ca7071d9](/BEAAA/issues/BEAAA-2199#comment-ca7071d9)
Ask 4):

> The envelope shape changes. Consumers (UW intake, Auditor intake)
> that pin `scanner_version` for envelope-shape reproducibility MUST
> be able to distinguish `0.3.10` envelopes (no `cosign_metadata`)
> from envelopes that carry the new block. Pinning only
> `contract_version` is insufficient given the stale-constant issue
> in Open Q2 (= BEAAA-1721 below).

The reproducibility-band guarantee (±1 on `are_total` and pillars,
per `are-scanner-api-v1 §Hard guarantees (CTO) 1a`) is **unaffected**
because scoring math is unchanged. The N=5 calibration in §6 is the
evidence backing the bump (spread = 0 on `are_total` + every pillar
across B1/G1/G2/M1/M2/M3 — strictly inside the ±1 band).

## §6 — N=5 calibration plan + halt conditions

**Method.** N=5 full-dict calibration on the standing golden roster
(B1/G1/G2/M1/M2/M3) using the BEAAA-2175 PR-B calibration harness
shape (`INTENDED_DELTAS` set + halt-on-unintended-delta scan).

**`INTENDED_DELTAS = ["cosign_metadata"]`.** The single intended
field-set delta between the master `52c003e` baseline envelope and the
BEAAA-2199 candidate envelope, per-agent, is exactly the new
`cosign_metadata` top-level key. Any other delta — any other key
added, renamed, removed, or value-shifted — is an unintended-delta
halt condition.

**Halt conditions (PR merge blocked on any of these).**
- `unintended_diffs != []` on full-dict comparison per agent.
- `missing_intended_diffs != []` (`cosign_metadata` absent from any
  candidate envelope).
- N=5 spread > 0 on `are_total` or any pillar subscore for any of the
  six goldens.
- `cosign_metadata.cto_cosign_ref` resolves to an empty or
  non-BEAAA-comment string for any `rule_changes[]` entry.
- `cosign_metadata.adr_ref` resolves to a non-existent file for any
  `rule_changes[]` entry.

## §7 — Risks and mitigations

**§7.1 — Stale `scanners.CONTRACT_VERSION` constant (BEAAA-1721
dependency).** The constant in `scanners/__init__.py:CONTRACT_VERSION`
is currently `"are-scanner-api-v1@v1.0.3"`. `build_cosign_metadata`
reads this constant, so the emitted
`cosign_metadata.contract_version` will say `v1.0.3` while the doc is
at `v1.3.0`. This is a real defect, but it is
[BEAAA-1721](/BEAAA/issues/BEAAA-1721)'s defect (the
contract-version-sync ticket), not BEAAA-2199's. CTO judgment
([comment ca7071d9](/BEAAA/issues/BEAAA-2199#comment-ca7071d9)
Ask 3): **PROCEED with BEAAA-2199**; BEAAA-1721 self-heals the value
at its own merge with no further BEAAA-2199 work.

Mitigations on BEAAA-2199:
- This ADR §7.1 documents the staleness so auditors reading the ADR
  see the discrepancy.
- The new §cosign_metadata section of
  `contracts/are-scanner-api-v1.md` includes a "Known issue" callout
  with the BEAAA-1721 cross-reference so the contract reader sees the
  discrepancy without spelunking ADRs.
- The cosign block's primary auditor value is the cosign refs
  themselves (CTO ticket, HoUW ticket, Founder ticket, ADR ref) —
  those are correct regardless of the constant. Auditors reading
  `rule_changes[*]` get the truth.

Escalation hook: if during implementation BEAAA-1721's scope cannot
self-heal without also touching `build_cosign_metadata`, the Scanner
Engineer MUST escalate back to the CTO before merge.

**§7.2 — Other agents' WIP on the shared worktree.** The activation
must be lifted clean off master `52c003e` (the BEAAA-2175 PR-B merge
commit) in an isolated git worktree per the BEAAA-2175 PR-A v1
workspace-hygiene rejection lesson
([comment 954754f5](/BEAAA/issues/BEAAA-2175#comment-954754f5-4ef7-4cba-ad00-3465d4beb7e1)).
The shared worktree's uncommitted modifications MUST NOT contaminate
the BEAAA-2199 diff. Implementation route: `git worktree add
/tmp/are-scanner-beaaa2199 -b beaaa-2199-pr master`; sources lifted
via `git show 52c003e:<path>` rather than the on-disk shared-worktree
copies.

**§7.3 — `cosign_metadata` block staleness drift over time.** The
`SCANNER_COSIGN_MANIFEST` is a static Python module versioned in-tree.
A future rule-change activation (new BEAAA rubric activation, new
sanitization layer, etc.) MUST update this manifest in the same PR as
the rule change, by construction. Audit hook in the file's docstring
+ the `Block-level invariants` paragraph in the contract doc enforce
that `scanner_version` / `rubric_version` /
`applicability_policy_version` inside the block equal the same-named
live fields in the response.

## §8 — Rollback

Tier 1. Two paths:

1. **Revert the four-file diff** (≤30 min): delete
   `harness/cosign_manifest.py`, revert the 4-line
   `harness/contract_envelope.py` addition, revert
   `scanners/__init__.py` 0.3.11 → 0.3.10, revert this contract doc.
   Pre-v1.3.0 consumers continue to work unchanged.
2. **Empty the manifest** (≤5 min): set
   `SCANNER_COSIGN_MANIFEST = []` in `harness/cosign_manifest.py`.
   The block still emits but `rule_changes` is empty. Useful if
   manifest content is found defective but the block surface is
   correct.

## §9 — Cosign refs (canonical for citation)

- `cto_design_cosign_ref`: [BEAAA-2199 comment ca7071d9](/BEAAA/issues/BEAAA-2199#comment-ca7071d9)
- `uw_op_seat_cosign_ref`: [BEAAA-2262 comment e59a5d3b](/BEAAA/issues/BEAAA-2262#comment-e59a5d3b)
- `cto_merge_cosign_ref`: pending — PR-submission comment on BEAAA-2199
- `supervision_contract`: `scanner-implementation-supervision-v1@v1.0`
- `contract_doc_baseline`: `are-scanner-api-v1@v1.2.1`
- `contract_doc_target`: `are-scanner-api-v1@v1.3.0`
- `scanner_master_baseline`: `52c003e`
- `scanner_version_baseline → target`: `0.3.10 → 0.3.11`
