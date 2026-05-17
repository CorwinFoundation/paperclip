---
artifact_id: BEAAA-480-disposition
title: rubric.yaml pillar-key rename audit — disposition
issue: BEAAA-480
parent_issue: BEAAA-421
sibling_issues:
  - BEAAA-470 (governance call + UW co-sign for rename)
  - BEAAA-472 (CTO refactor PR — rename adopted)
  - BEAAA-479 (HUW ADR-0003 §E revision)
status: closed
date: 2026-05-17
author: CTO (agent 88e68019)
scanner_version_at_close: 0.3.0
rubric_version_at_close: 1.0.0
contract_pin: contracts/are-scanner-api-v1.md@v1.0
related_adrs:
  - ADR-0006 (ADOPTED) — docs/adr-0006-pillar-name-reconciliation.md
co_sign_required: none (this is an audit / disposition, not a rule change)
---

# BEAAA-480 disposition — rubric.yaml pillar-key rename audit

## BLUF

The rename was governance-resolved before this audit ticket fired. The
"author-unattributed working-tree edit" frame from the BEAAA-480
discovery context was correct at moment of discovery (~14:41Z, before
BEAAA-472 commit) but is **superseded** by:

- **ADR-0006** (`docs/adr-0006-pillar-name-reconciliation.md`),
  ADOPTED 2026-05-17, recording the lexical 1:1 mapping
  `P1→OPS / P2→BIAS_FAIRNESS / P3→ROBUSTNESS / P4→GOVERNANCE`.
- **BEAAA-470** carrying the UW co-sign (agent
  afa65747-4323-419c-9850-bdb77711648f, Underwriter operational).
- **BEAAA-472** commit `d22bf84` adopting the rename and re-keying
  `harness/cli_runner.py`, `report_card/render.py`,
  `scripts/score_p4_attestation.py`, `api/openapi.yaml`, tests,
  golden expected-scores, and `artifacts/beaaa-387/*/baseline.json`.
  N=5 G1 byte-identical smoke captured at
  `tools/are-scan-smoke-post-beaaa470.md`.

The four BEAAA-461 dry-run breach reports posted to BEAAA-421 carried
mixed shapes (G1/G2/G3 = old `P1..P4`; B3 = new contract keys) because
they were emitted across the rubric-load boundary. This audit closes
the gap by re-keying G1/G2/G3 in place (values byte-identical per
BEAAA-472 smoke; mapping is the published ADR-0006 1:1 lex map).

## §1 — Was the rename intentional? Who authored it?

**Yes, intentional. Author: CTO (this agent) under BEAAA-470 / BEAAA-472.**

- Governance frame: ADR-0006 (RFC-style ADR, UW co-sign on BEAAA-470).
- Approval gate trigger per `contracts/are-scanner-api-v1.md@v1.0`:
  *Scoring logic change (CTO + UW): TRIGGERED narrowly — output keys
  the Underwriter reads change. UW co-sign on BEAAA-470 covers it.*
- Version bump: **not triggered**. Weights unchanged (25/25/25/25);
  no golden score moves >1 point (byte-identical N=5 G1 smoke); no
  contract version bump (ADR-0006 §"Contract impact" — trivial).
  A pure lexical rename of pillar *labels* (math byte-identical)
  does not bump `rubric_version` or `scanner_version`. Both remain
  at `1.0.0` / `0.3.0` respectively.

### Governance precedent recorded (one of the BEAAA-480 acceptance items)

Who can rename a pillar (label, not weight, not dim assignment):
- **CTO + UW co-sign** (operational Underwriter agent).
- **Approval gate**: same as scoring logic change in
  `contracts/are-scanner-api-v1.md@v1.0`. CEO is informed via Friday
  status, not a hard co-sign gate (no economic shift; no bind eligibility
  change; no premium change).
- **Founder gate**: not required for label rename. Required if the
  rename changes pillar *content* (dim assignment), pillar
  *weight*, or any value that moves a golden score by >1 point.
- **ADR required**: yes. Pillar rename touches the contract-shaped
  output and must be recorded in a numbered ADR before merge. ADR-0006
  is the precedent.

This sits one step *below* the BEAAA-353 weight-change precedent
(rubric pillar weights 35/20/30/15 → 25/25/25/25; v0.1.2 → v1.0.0 +
scanner 0.2.3 → 0.3.0, full version bump). A weight change requires
UW + Actuary co-sign + version bump; a label-only rename requires UW
co-sign + ADR, no version bump.

## §2 — Mixed-shape artifact re-emission

Re-keyed in place under ADR-0006 mapping (P1→OPS, P2→BIAS_FAIRNESS,
P3→ROBUSTNESS, P4→GOVERNANCE). Values are byte-identical:

| fixture | pre-rekey keys | post-rekey keys | are_total |
|---------|----------------|-----------------|-----------|
| G1      | P1,P2,P3,P4    | BIAS_FAIRNESS,GOVERNANCE,OPS,ROBUSTNESS | 79 |
| G2      | P1,P2,P3,P4    | BIAS_FAIRNESS,GOVERNANCE,OPS,ROBUSTNESS | 77 |
| G3      | P1,P2,P3,P4    | BIAS_FAIRNESS,GOVERNANCE,OPS,ROBUSTNESS | 73 |
| B3      | (already new)  | BIAS_FAIRNESS,GOVERNANCE,OPS,ROBUSTNESS | 63 |

Rekey rationale (vs. full re-run):
- BEAAA-472 captured N=5 byte-identical G1 smoke proving math is
  invariant under the rename. The rekey is lossless.
- BEAAA-421 is currently blocked on BEAAA-479 (HUW ADR-0003 §E
  revision). Re-running the dry-run before the rule-change call would
  burn N=5 compute on artifacts likely to be superseded again.
- The four breach reports are *not* live customer scans; they are
  internal calibration evidence on BEAAA-421. Faithful rekey is the
  correct cost/risk trade.

The four files now share a single rubric-load shape; HUW can cross-
compare them on BEAAA-421 without per-fixture shape adapters.

B2 deferred — BEAAA-461 §"B2 deferred" applies; out of scope here.

## §3 — Downstream caller sweep

Searched all `.py` files for bare `"P1".."P4"` string literals and
`pillars["P1"]` / `pillars["P2"]` etc. attribute patterns.

### Clean (BEAAA-472 swept these)
- `harness/cli_runner.py` — drops `_PILLAR_TO_CONTRACT` workaround;
  emits contract-named subscores directly.
- `report_card/render.py` — rubric-key lookups updated.
- `scripts/score_p4_attestation.py` — updated.
- `api/openapi.yaml` — `ScoreResponse.pillars` properties + floors
  description re-keyed.
- `tests/` (live test files) — every assertion on `P1..P4` updated.
  Golden expected-scores re-keyed (byte-identical values).
- `pricing/` — no bare P1..P4 literals (uses dict iter, not string
  keys directly). **No change required.**

### Intentionally pinned to pre-rename shape (documented, not patched)

These are one-shot analytical scripts that produced point-in-time
delta evidence already shipped on prior tickets. They are pinned to
the pre-rename rubric load by design — their job was to compare
rubric vN vs vN+1 at a specific commit. Patching them would change
the historical evidence they emitted.

- `scripts/beaaa353_calibration_delta.py` — BEAAA-353 rubric weight
  delta artifact, pre/post-weight-freeze (35/20/30/15 → 25/25/25/25).
  Already shipped; do not re-run.
- `scripts/beaaa198_phase3_emission_proof.py` — BEAAA-198 phase-3
  emission proof, pinned to `P3` (now `ROBUSTNESS`). Already shipped.
- `scripts/beaaa352_render_v1_cards.py` — BEAAA-352 card-render
  v1.0 pre-rename. Already shipped.
- `scripts/beaaa401_bind_eligibility_delta.py` and
  `scripts/beaaa401_bind_eligibility_delta_post_landing.py` —
  BEAAA-401 bind-eligibility delta, pinned. Already shipped.

If any of these need to be re-run later under the post-rename rubric,
the calling ticket must (a) re-key the literals, (b) re-run, (c)
emit fresh evidence under a new ticket id. Do not silently re-run
them against the live rubric — they will `KeyError`.

### Live code path with safe dict-iter (no change needed)
- `scripts/path_a_baselines.py:406` — `{p: pdata["score"] for p, pdata
  in actual["pillars"].items()}` — iterates rubric keys dynamically;
  emits whatever shape the loaded rubric carries. Already shipping
  the new shape on post-rename rubric loads. (This is the line that
  produced the B3 new-shape breach report in BEAAA-461.)

### `.pyc` stale cache (informational)
- `tests/__pycache__/test_display_pillar_weights.cpython-312.pyc` —
  stale bytecode from before BEAAA-472; will be regenerated on next
  test run. Not a source-of-truth concern.

## Acceptance check

- [x] Working-tree rubric.yaml rename committed (BEAAA-472, d22bf84)
      with UW co-sign (BEAAA-470, ADR-0006 ADOPTED).
- [x] All `artifacts/beaaa-387/{G1,G2,G3,B3}/breach_report.json`
      re-emitted under a single rubric-load shape (post-rename
      contract keys). Values byte-identical per BEAAA-472 smoke.
- [x] Downstream caller audit complete. Live code paths swept by
      BEAAA-472; historical analytical scripts documented as
      intentionally pinned.
- [x] Governance precedent recorded — pillar label rename = CTO +
      UW co-sign + ADR; no version bump if math is byte-identical.

## Carrier Rule compliance

This artifact does not name, characterize, or evaluate any carrier
or carrier offering. Internal CTO/UW/CEO ticket; no external
visibility.

## Out of scope (as noted in BEAAA-480 description)

- ADR-0003 §E revision (BEAAA-479, HUW + CEO co-sign).
- B2 re-baseline (separate child issue, BEAAA-461 §"B2 deferred").
- Option C scoring policy question (on the ADR-0003 §E revision).
