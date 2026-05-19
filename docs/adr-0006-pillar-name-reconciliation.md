---
artifact_id: ADR-0006
title: Pillar-name reconciliation — internal P1..P4 → contract GOVERNANCE/ROBUSTNESS/BIAS_FAIRNESS/OPS
issue: BEAAA-470
status: ADOPTED
date: 2026-05-17
adopted_via: BEAAA-472 (CTO refactor PR — rename internal P1..P4 → OPS / BIAS_FAIRNESS / ROBUSTNESS / GOVERNANCE)
scanner_version_at_proposal: 0.3.0
rubric_version_at_proposal: 1.0.0
contract_pin: contracts/are-scanner-api-v1.md@v1.0
co_sign_required:
  - Underwriter (operational) — agent afa65747-4323-419c-9850-bdb77711648f
  - Founder gate: NOT required (mapping is purely lexical, no golden moves >1 point)
post_merge_smoke: tools/are-scan-smoke-post-beaaa470.md
related_adrs:
  - ADR-0005 (BEAAA-464) — flagged this gap as a known gap; this ADR resolves it.
  - ADR-0002 (BEAAA-19) — rubric 1.0.0 pillar-weight lock (25/25/25/25).
---

# ADR-0006: Pillar-name reconciliation — internal P1..P4 → contract names

## Decision (BLUF)

Adopt a **lexical 1:1 rename** of the four internal rubric pillars (`P1`,
`P2`, `P3`, `P4`) to the four canonical contract pillar names
(`GOVERNANCE`, `ROBUSTNESS`, `BIAS_FAIRNESS`, `OPS`) per the mapping
below. **No rubric dim moves between pillars; no pillar weights change;
no scoring math changes.** N=5 replay against the locked G1 fixture is
required to produce `are_total = 84` byte-identically with the existing
ADR-0005 smoke sha256 (`eb127166…`); only the subscore *labels* change,
not the values.

The recommended mapping:

| Internal id | Internal name           | →   | Contract name      |
|-------------|-------------------------|-----|---------------------|
| `P1`        | Failure Surface         | →   | `OPS`               |
| `P2`        | Bias & Fairness         | →   | `BIAS_FAIRNESS`     |
| `P3`        | Adversarial Robustness  | →   | `ROBUSTNESS`        |
| `P4`        | Governance & Operations | →   | `GOVERNANCE`        |

This is the cleanest 1:1 lexical mapping that (a) preserves the rubric
math byte-identically, (b) honours the contract's locked 25/25/25/25
weights, and (c) leaves three of the four pillar labels strongly
matching their contract descriptions (`P2→BIAS_FAIRNESS` is clean,
`P3→ROBUSTNESS` is clean, `P4→GOVERNANCE` is defensible because audit /
kill-switch / IR / owner-accountability are governance-grade).

The fourth pillar (`P1→OPS`) is the load-bearing concession of this
ADR: P1's dimensions (hallucination, citation integrity, contradiction,
refusal accuracy, confidence calibration, eval coverage breadth) are
broader than the contract's OPS description ("Eval reproducibility,
data handling, retention, PII scrubbing, version pinning") would
suggest in pure form. The concession is documented and load-bearing on
the next ADR if a customer or auditor reads the contract literally —
see *§ Known imperfection*.

## Why this issue exists

ADR-0005 (BEAAA-464) shipped `bin/are-scan` and surfaced — but did not
fix — the pre-existing mismatch between:

- **Contract canonical labels** (`contracts/are-scanner-api-v1.md@v1.0`
  §Score structure): `GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS`,
  each weighted 25 of 100.
- **Internal rubric labels** (`rubric/rubric.yaml@1.0.0`): `P1 Failure
  Surface / P2 Bias & Fairness / P3 Adversarial Robustness / P4
  Governance & Operations`, each weighted 25 of 100.

The mismatch was latent for weeks. ADR-0005's CLI ships a workaround
(`subscores_contract_named` emits a re-grouped view with `P1+P3 →
ROBUSTNESS` and `P4 → GOVERNANCE_OPS`) which is only three pillars, not
four — so customer-facing reports could not match the contract's 4-pillar
shape without a per-report manual mapping.

BEAAA-470 is the gating reconciliation: every v0.1 external customer
scan emits subscore *labels* matching the contract.

## Options considered

### Option A — Lexical 1:1 rename (RECOMMENDED — this ADR)

Rename the 4 internal pillar ids/names to the 4 contract names. Dim
allocation, dim weights, pillar weights, and scoring math all unchanged.

- **Math impact:** zero. Same dims, same weights, same arithmetic.
  G1 N=5 still produces `are_total = 84` and sha256 `eb127166…` after
  the rename (with the smoke's pinned `--scored-at`).
- **Calibration impact:** none. No golden re-baseline. Approval gate
  "Any change that moves a golden agent's score by >1 point" not
  triggered.
- **Contract impact:** trivial (no contract version bump). The
  contract already lists `GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS`
  as the four pillar keys; this ADR aligns the code to the keys the
  contract already declares.
- **Cost:** medium-large refactor across `rubric/rubric.yaml`,
  `rubric/scoring.py`, `rubric/bands.py`, `harness/cli_runner.py`,
  `bin/are-scan`, the test suite (729 + 62 subtests), the golden
  artifacts (`tests/fixtures/golden_agents/G*/expected_score.json`),
  `tools/are-scan-smoke-2026-05-17.md`, every ADR currently referencing
  P1..P4, and `canon/glossary.md` (deprecation note).
- **Known imperfection:** the `P1→OPS` choice asks the OPS label to
  carry more than the contract description bullets. Defensible but
  load-bearing. See *§ Known imperfection*.

### Option B — Structural rebalance to match contract descriptions exactly

Move rubric dims between pillars so each contract pillar's dims actually
match the contract description (e.g., move P1.5 + P1.6 + P4.2 + P4.5
into a new OPS pillar; keep P4.1/3/4/6 in GOVERNANCE; move P1.1-P1.4
into ROBUSTNESS alongside P3.1-P3.3).

- **Math impact:** subscores change. Each pillar's dim mean changes
  with new dim allocation, so the four pillar scores will all move.
  Total may also move depending on dim raws.
- **Calibration impact:** REQUIRED full re-baseline of the golden
  roster. The "Any change that moves a golden agent's score by >1 point"
  approval gate fires. N=5 calibration delta required per the AGENTS.md
  *Standing procedure — Scanner reproducibility check*.
- **Contract impact:** contract version stays at v1.0 (pillar names and
  weights unchanged), but the *meaning* of each pillar shifts internally.
  Underwriter pricing engine must be reviewed for sensitivity.
- **Cost:** structural refactor + golden re-baseline + UW + Founder
  cosign + likely a v0.2 rubric stamp.
- **When to prefer:** if the gap between internal P-content and
  contract-description-content is judged material by Underwriter or by
  a future auditor reading the contract literally.

### Option C — Amend the contract to publish P1..P4 vocabulary

Bump `are-scanner-api-v1.md` to v1.1 with the internal P1..P4 keys.

- Rejected. The contract names are the customer-facing vocabulary
  (filed-rate language, reinsurance docs, prospect decks all use them).
  Forcing customers, reinsurers, and counsel onto a P1..P4 vocabulary
  is bad UX with no upside.

## Mapping — defended pillar by pillar

### `P2 → BIAS_FAIRNESS` (CLEAN)

Both labels mean the same thing. The dim set is unchanged:
P2.1 demographic parity, P2.2 equal opportunity, P2.3 counterfactual
stability, P2.4 representation in eval set, P2.5 output sentiment skew,
P2.6 mitigation evidence. Contract description: "Bias in autonomous
decisions, fairness metrics on standard suites, discriminatory output
detection." Exact match.

### `P3 → ROBUSTNESS` (CLEAN)

P3 already self-describes as "Adversarial Robustness". The dim set is
unchanged: P3.1 direct prompt-injection resistance, P3.2 indirect
injection resistance, P3.3 secret/credential exfiltration resistance.
Contract description: "Prompt-injection resistance, tool misuse,
hallucinated actions, runaway loops, adversarial input handling." Three
of these five contract sub-items map directly to P3 dims; tool misuse
and runaway loops are deferred to v0.2 per `rubric.yaml@deferred_to_v02`
(P3.4 / P3.5 / P3.6). The label fits; the gap is one of *coverage
depth*, not of *label semantics*.

### `P4 → GOVERNANCE` (DEFENSIBLE)

P4 is self-described as "Governance & Operations". The dim set is
unchanged: P4.1 audit log completeness, P4.2 model/prompt version
control, P4.3 kill-switch + rollback, P4.4 incident-response playbook,
P4.5 eval re-run cadence, P4.6 owner accountability matrix. Contract
GOVERNANCE description: "Audit trail, change management, kill switch,
human-in-the-loop policy, monitoring, incident response." P4.1, P4.3,
P4.4, P4.6 are clean GOVERNANCE matches. P4.2 (version control) and
P4.5 (eval cadence) are arguably OPS per the contract description, but
they are governance disciplines in practice — change-management *is*
version control, IR cadence *is* eval cadence. Bundle is defensible.

### `P1 → OPS` (LOAD-BEARING CONCESSION)

This is the asymmetric one. P1 dims: P1.1 hallucination rate, P1.2
citation integrity, P1.3 self-contradiction, P1.4 out-of-scope refusal,
P1.5 confidence calibration, P1.6 eval coverage breadth. Contract OPS
description: "Eval reproducibility, data handling, retention, PII
scrubbing, version pinning."

- P1.6 (eval coverage breadth) ↔ contract OPS "eval reproducibility" ✓
- P1.5 (confidence calibration) ↔ contract OPS measurement-discipline
  bullet ✓ (loose)
- P1.1-P1.4 (hallucination / citation / contradiction / refusal) are
  *output-quality* dims that the contract's OPS bullets don't explicitly
  cover — these are the load-bearing dims of this concession.

The concession is justified because:

1. **No other 1:1 lexical mapping exists.** Contract `ROBUSTNESS` is
   already taken by P3 (adversarial). Contract `BIAS_FAIRNESS` is
   already taken by P2. Contract `GOVERNANCE` is already taken by P4.
   The only contract label left for P1 is OPS — and the contract
   requires four named pillars, no more, no fewer.
2. **The contract description is a guidance bullet list, not an
   exhaustive enumeration.** The contract's `are-scanner-api-v1.md@v1.0`
   §Score structure lists "what each pillar owns" as a sentence, not as
   a rubric. The Underwriter pricing engine consumes `subscores.OPS` as
   a number; it does not parse the description bullets to validate
   contents.
3. **Output discipline IS operational.** Hallucination, citation
   integrity, refusal accuracy: these are operational properties of
   the agent's output pipeline. Re-reading the contract's OPS bullets
   ("data handling, retention, PII scrubbing") with this framing,
   hallucination is "data handling — does the model emit data it
   didn't have." Citation integrity is "retention — does the agent
   retain provenance."

Option B (structural rebalance) is on the table for any reader who
finds this concession too thin. The trigger to escalate Option B is a
material Underwriter or counsel objection — see *§ Escalation triggers*.

## Reproducibility test (REQUIRED for merge)

Per `agents/cto-os.md` Supreme Law and the AGENTS.md *Standing procedure
— Scanner reproducibility check*, after the rename refactor the
following must hold:

1. N=5 `bin/are-scan` against the G1 fixture with the same `--scored-at`
   pin as ADR-0005's smoke (`2026-05-17T12:00:00+00:00`) emits 5
   byte-identical stdout blobs.
2. Each blob has `are_total = 84`.
3. Each blob has subscores `{OPS: 20.3125, BIAS_FAIRNESS: 19.2708,
   ROBUSTNESS: 22.9167, GOVERNANCE: 21.875}` — i.e., the four values
   from ADR-0005's smoke with new labels (P1→OPS, P2→BIAS_FAIRNESS,
   P3→ROBUSTNESS, P4→GOVERNANCE).
4. The full pytest suite (729 + 62 subtests pre-rename) passes after
   the rename. Tests with hard-coded `P1..P4` literals are updated as
   part of the refactor.
5. `tools/are-scan-smoke-2026-05-17.md` is replaced by
   `tools/are-scan-smoke-post-beaaa470.md` (or amended in place) with
   the same table layout, new column headers, and the new sha256
   (the sha will change because the JSON key strings change).

The byte-identical assertion in (1) is strictly stronger than the ±1
band the contract requires. The new sha256 in (5) is expected and is
NOT a calibration drift — it is a re-keying of the output JSON.

## Tamper resistance review

The rename does not introduce new injection surfaces. Pillar keys are
internal constants, not user-controllable. The CLI's input contract
(`eval_results.json` keyed by `P<n>.<m>`) is the only place a scanned
agent's owner can influence pillar identification, and that is the
**dim id**, not the pillar id. The refactor preserves dim ids verbatim
(P1.1, P1.2, …, P4.6) — only the pillar-level key strings change. The
attacker surface is unchanged.

## Glossary impact

`canon/glossary.md` already lists the contract pillar names as the
canonical externally-facing vocabulary
(`GOVERNANCE 25 / ROBUSTNESS 25 / BIAS-FAIRNESS 25 / OPS 25`). This ADR
brings the code into line with the glossary — no glossary edit is
required unless any external-facing artifact ever published the
P1..P4 vocabulary verbatim. A `grep` sweep across `docs/`, `tools/`,
and the customer-facing artifact set is part of the refactor PR.

## Known imperfection (read this before reading the rest)

The `P1 → OPS` mapping is the weakest seam in this ADR. If a customer,
reinsurer, or counsel reads the contract description for OPS literally
("Eval reproducibility, data handling, retention, PII scrubbing,
version pinning") and then reads the customer's ARE Report card and
sees "OPS: 20.3125 / 25 — six dims covering hallucination, citation,
refusal, calibration, eval coverage", the gap will be visible.

Mitigations:

1. **Description amendment in the contract.** Optional follow-on:
   amend `contracts/are-scanner-api-v1.md@v1.0` §Score structure OPS
   bullet to read "Eval reproducibility, data handling, retention, PII
   scrubbing, version pinning, **output-quality discipline (hallucination
   rate, citation integrity, refusal accuracy)**." This is a contract
   *description* change, not a *shape* change — under
   `are-scanner-api-v1.md` §Versioning ("Breaking changes bump major")
   this is not a v2.0 bump; it is a v1.0.1 or v1.1.0 patch. Requires
   UW co-sign per §Approval gates. If we don't amend, the gap is
   acknowledged in this ADR and in the customer-facing ARE Report
   methodology section.
2. **Per-dim transparency in the ARE Report.** The customer-facing
   report already itemises every dim under its pillar. A reader who
   looks at the OPS pillar will see all six P1 dim names and can judge
   for themselves. This is the v0.1 path of least resistance.
3. **Escalate to Option B** if a material objection arises (see below).

## Escalation triggers (any of these → reopen as Option B)

- Underwriter declines to co-sign Option A and prefers structural
  rebalance.
- Founder ack on the customer-facing ARE Report flags the P1→OPS
  mismatch as a reputation risk.
- Counsel review of filed-rate language objects to OPS bullet vs OPS
  pillar content gap.
- A reinsurer reviewing the contract requires the OPS bullet and OPS
  pillar content to match exactly.

Any of these triggers reopens this ADR as Option B, with the full
restructure cost + golden re-baseline + Founder gate.

## Rollback plan

The refactor PR lands all four pillar renames in a single commit. If
the rename breaks any downstream consumer (Underwriter pricing engine,
report renderer, customer artifact pipelines):

1. **Tier 1 — revert the refactor commit.** `git revert <commit>`
   restores the P1..P4 keys verbatim. ETA: ≤5 minutes. The CLI's
   `subscores_contract_named` workaround from ADR-0005 remains as the
   bridge until a re-attempt.
2. **Tier 2 — partial revert.** If only one of the four renames is
   contested (most likely `P1→OPS`), keep `P2→BIAS_FAIRNESS`,
   `P3→ROBUSTNESS`, `P4→GOVERNANCE` and revert only `P1→OPS` back to
   the literal `P1` key. The output JSON would have three contract
   names and one legacy P-name until Option B lands. **NOT
   RECOMMENDED — defeats the purpose of the ADR** but available as a
   forensic option.
3. **Tier 3 — escalate to Option B.** Open a new BEAAA ticket for the
   structural rebalance; treat this ADR as superseded.

In-agent scans are unaffected by any rollback tier (the in-agent path
is the same `score()` call as the CLI).

## Approval gate per `contracts/are-scanner-api-v1.md@v1.0`

The contract's §"Approval gates" lists:

- **Pillar weight changes (Founder).** Not triggered — weights unchanged.
- **Rubric point added or removed (CTO + Underwriter).** Not triggered
  — no dim added or removed.
- **Scoring logic change (CTO + Underwriter).** Triggered narrowly —
  the *scoring math* doesn't change, but the *output keys* the
  Underwriter pricing engine reads change. UW co-sign required on the
  re-keyed output JSON shape.
- **New eval that uses an LLM judge (CTO).** Not triggered.
- **Any change that moves a golden agent's score by >1 point (CTO +
  Underwriter; Founder if >3 points).** Not triggered — golden scores
  are byte-identical pre/post rename.
- **Test corpus version bump affecting >5% of rubric points (CTO +
  Underwriter).** Not triggered.
- **Anything reducing tamper resistance (Founder).** Not triggered.

→ **Required:** Underwriter co-sign on the re-keyed output JSON shape
and on the `P1→OPS` concession. **No Founder gate** unless the
co-sign surfaces Option B as the preferred path.

## Audit trail (per CTO Supreme Law)

- ADR: this file.
- Refactor PR (pending — created as child issue after UW co-sign).
- Regression artifact (pending — `tools/are-scan-smoke-post-beaaa470.md`).
- Glossary sweep (pending — part of the refactor PR).
- Issue: BEAAA-470.
- Contract pin: `contracts/are-scanner-api-v1.md@v1.0` (unchanged).
- Scanner version at proposal: 0.3.0.
- Rubric version at proposal: 1.0.0 (unchanged after merge).
- Co-sign request: Underwriter (operational) — agent
  `afa65747-4323-419c-9850-bdb77711648f` via
  `request_confirmation` on BEAAA-470.

## Decision sequencing

1. **This heartbeat (CTO):** ADR-0006 drafted (PROPOSED). UW
   `request_confirmation` opened with this ADR attached. BEAAA-470 →
   `in_review` pointing at the interaction.
2. **Next (UW):** co-sign Option A as proposed, or push back to Option
   B / amended Option A. Wake assignee back to CTO on response.
3. **After UW co-sign (CTO):** spawn child issue for the rename
   refactor PR (rubric.yaml + scoring.py + bands.py + tests + cli +
   smoke + ADR-0005 amendment). Include the N=5 regression artifact.
   Glossary sweep included.
4. **After PR merge (CTO):** BEAAA-470 → `done`.

## Merged in BEAAA-472

Adopted by the refactor PR on BEAAA-472. Post-merge smoke:
`tools/are-scan-smoke-post-beaaa470.md` records the N=5 G1 byte-identical
run (`are_total = 84`; `OPS = 20.3125 / BIAS_FAIRNESS = 19.2708 /
ROBUSTNESS = 22.9167 / GOVERNANCE = 21.875` — same numbers as ADR-0005,
new keys, new stdout sha256). The ADR-0005 `subscores_contract_named`
bridge view is removed as part of the same refactor (the in-emitter
`_PILLAR_TO_CONTRACT` mapping table no longer has a role once
`subscores` is contract-named at the source).

## Footer — Mitigation #1 status (added 2026-05-19)

§Known imperfection Mitigation #1 ("Description amendment in the
contract") is now **shipped**, not "optional follow-on". The contract
OPS bullet was expanded per `are-scanner-api-v1@v1.0.2` to read:
"Eval reproducibility, data handling, retention, PII scrubbing,
version pinning, output-quality discipline (hallucination rate,
citation integrity, refusal accuracy, confidence calibration)."

The customer-facing gap flagged in §Known imperfection ("a customer
reading the v1.0 OPS bullet and the ARE Report Card will see the
mismatch") is closed by this description expansion. Mitigations #2
(per-dim transparency in the ARE Report) and #3 (escalate to Option B
on material objection) remain available as further fallbacks but are
not load-bearing now that #1 is in.

- Patch reference: `contracts/are-scanner-api-v1.0.2-patch.md`.
- Issues: BEAAA-473 (UW Rider 1) + BEAAA-551 (CTO file commit).
- Cosign: interaction `2519a4be-9eee-43cc-bc29-619635a1bdb4` accepted
  2026-05-19T09:10:11.831Z; UW first-touch on BEAAA-473 comment
  `3a1c16fe`.
- Classification: description clarification — no output-shape /
  field-meaning / scoring-math / judge-model / tamper-resistance
  change. Δ_total = Δ_subscore = 0 expected on N=5 G1.
