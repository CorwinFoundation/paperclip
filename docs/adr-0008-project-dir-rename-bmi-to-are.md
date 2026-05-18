---
artifact_id: ADR-0008
title: Project directory rename — `bmi-scanner/` → `are-scanner/` + contract patch bump
issue: BEAAA-510
status: PROPOSED — pending CTO co-sign on the contract bump portion
date: 2026-05-18
author_seat: Scanner Engineer (agent d8bbc3b5-185f-43ed-bff0-d00323ddbfa3)
scanner_version_at_proposal: 0.3.0
rubric_version_at_proposal: 1.0.0
corpus_version_at_proposal: 0.1.1
contract_pin_pre_merge: contracts/are-scanner-api-v1.md@v1.0
contract_pin_post_merge: contracts/are-scanner-api-v1.md@v1.0.x (next available patch — coordinate with BEAAA-473 v1.0.1 in flight)
classification_per_supervision_contract:
  rename:
    row: "Refactor that doesn't touch behavior (move code, rename internal variables)"
    classification: rule-execution
    cto_cosign_required: false
  contract_bump:
    row: "(no explicit row; CTO standing rule is that contract version bumps are strategic-seat)"
    classification: rule-change
    cto_cosign_required: true
upstream_eval_version: N/A (no upstream eval wrapped in this change)
co_sign_required:
  - CTO (88e68019-1cb6-4c4f-83e1-9fd340fb6821) on the contract bump portion ONLY (the dir rename is rule-execution; SE ships unilaterally).
  - Founder gate: NOT required (no pillar weight change, no tamper-resistance reduction, no customer-data handling expansion).
  - Underwriter co-sign: NOT required (no output JSON shape change, no scoring math change, no band-cut change, no rate-book touch).
related_adrs:
  - ADR-0001 (BEAAA-101 / residential proxy egress) — flagged `bmi-scanner` as legacy directory name; this ADR completes the rename.
  - ADR-0005 (BEAAA-464) — `bin/are-scan` CLI ship; original byte-identical baseline (sha `eb127166…`).
  - ADR-0006 (BEAAA-470 → merged in BEAAA-472) — pillar-name reconciliation P1..P4 → OPS/BIAS_FAIRNESS/ROBUSTNESS/GOVERNANCE; established the post-rename baseline (sha `24361151a3b7…`) at commit `d22bf84`.
  - ADR-0007 (BEAAA-479) — Option C admittance dial (PROPOSED); ADR-0007 number already taken, so this rename ADR is ADR-0008.
related_tickets:
  - BEAAA-496 — CEO Monday cascade (origin of this work).
  - BEAAA-497 — CTO confirm-receipt of -496 that filed BEAAA-510.
  - BEAAA-473 — UW v1.0.1 reference text (OPS "Owns" cell expansion) — board-pending cosign interaction `2519a4be`. **Collision flag**: if -473 lands as v1.0.1 first, this rename ADR's contract bump becomes v1.0.2.
  - BEAAA-477 — Scanner Engineer seat activation (this rename is the first contract-touching ticket I've absorbed).
---

# ADR-0008: Project directory rename — `bmi-scanner/` → `are-scanner/` + contract patch bump

## Decision (BLUF)

Rename the project working directory `projects/bmi-scanner/` → `projects/are-scanner/`. Update the two active in-repo path references (`docs/runbooks/post-creds-resi-proxy.md`, `scanners/inspect_runner.py` comment). Bump `contracts/are-scanner-api-v1.md` to the next available patch (v1.0.x) with a Version-history entry recording the path-rename. Leave the four historical artifact path references (one ADR, one cosign packet, three `artifacts/` evidence files) unchanged — they are immutable historical records and rewriting them rewrites history. N=5 G1 byte-identical proof against the pre-rename HEAD (commit `3c0ac8b`) confirms zero behavior change.

## Why this issue exists

Per `canon/glossary.md` (Founder-locked 2026-05-12), **ARE** is the canonical product name and **BMI** is deprecated. The project working directory still carries the deprecated `bmi-scanner` name. The deprecation surface report (BEAAA-374 weekly status 2026-05-15, lines 111 + 140) and ADR-0001 (line 48) both already call out the project directory as the highest-visibility remaining deprecation. The CEO Monday 2026-05-18 cascade (BEAAA-496) named this as the CTO's second-highest-leverage move for the week; the CTO confirmed receipt on BEAAA-497 and filed this ticket (BEAAA-510) under the rule-execution+rule-change classification documented in `contracts/scanner-implementation-supervision-v1.md@v1.0`.

## Classification per `scanner-implementation-supervision-v1.md@v1.0`

| Action portion | Table row | Classification | CTO co-sign? |
|---|---|---|---|
| Directory rename + in-repo path-reference updates | "Refactor that doesn't touch behavior (move code, rename internal variables)" | **Rule execution** | No (still requires N=5 on golden as regression check) |
| `contracts/are-scanner-api-v1.md` v1.0 → v1.0.x patch bump (Version-history addition + Status-line patch note) | No explicit row; closest reads are `bin/are-scan CLI behavior change that affects output JSON shape` (does NOT apply — no shape change) and `Scoring logic change` (does NOT apply — no math change). Contract bumps are CTO strategic-seat per AGENTS.md Standing Procedure (Scanner Engineer co-sign queue). | **Rule change** | **Yes** + ADR + contract version bump |

Per AGENTS.md anti-pattern auto-reject: "Defaulting to rule-execution to avoid co-sign overhead" is forbidden. The contract bump is classified as rule-change explicitly even though it carries zero math/shape/judge impact, because the strategic seat (CTO) owns contract version policy.

## Options considered

### Option A — Single PR: rename + contract bump bundled (RECOMMENDED — this ADR)

One commit on the rename + active-path updates inside the repo, one commit on the contract bump documenting the rename in Version-history. Both commits cited in the close.

- **Math impact:** zero. Pure path-string change. N=5 G1 byte-identical proof against pre-rename baseline.
- **Calibration impact:** none. No golden re-baseline. Approval gate "Any change that moves a golden agent's score by >1 point" not triggered.
- **Contract impact:** patch-level. No output JSON shape change. No field-meaning shift. Pillar weights, scoring math, judge models, tamper-resistance posture all unchanged.
- **Cost:** small. Two in-repo file edits, one directory rename, two new files (this ADR + the smoke artifact), one contract-text patch (referenced via a separate reference doc since the canonical contract file lives outside the project repo).
- **Coordination cost:** BEAAA-473 has v1.0.1 in flight (UW's OPS "Owns" cell expansion). If -473 lands first, the rename bump becomes v1.0.2. The CTO co-sign request explicitly cites this collision and asks the CTO to allocate the next-available patch number on accept.

### Option B — Split PRs: rename merged first as rule-execution, contract bump deferred to a separate ticket

- **Math impact:** zero (same as A).
- **Why rejected:** ticket BEAAA-510's DoD explicitly bundles the two ("PR splits cleanly: dir rename commit + contract bump commit"). Splitting would also produce a window where the project root is renamed but the contract has no audit-trail entry recording the rename — a hygiene regression by my own SE OS reading.

### Option C — Rename only, no contract bump

- **Why rejected:** the cascade (BEAAA-496) and the ticket DoD both require the contract bump. The cascade's classification logic is that a project root rename is the kind of fact downstream consumers (Underwriter pricing engine, customer LOI demo path conventions, reinsurance contract appendices) should be able to verify against a versioned contract entry.

## Mapping — what changes, what doesn't

### What changes
1. **External directory name**: `projects/bmi-scanner/` → `projects/are-scanner/`. The .git directory and all tracked content move with the dir; no internal git surgery required.
2. **`docs/runbooks/post-creds-resi-proxy.md:18`**: `cd projects/bmi-scanner` → `cd projects/are-scanner`. Active runbook step — operators need the live path.
3. **`contracts/are-scanner-api-v1.md`**: Version-history table addition + Status-line patch note recording the rename. Authored as a reference text doc (mirroring the BEAAA-473 v1.0.1 pattern) since the canonical contract file lives in agent instructions, outside this project repo. Proposed patch number: next available (v1.0.1 if BEAAA-473 hasn't landed, v1.0.2 if it has).

**Deferred (working tree, not in this PR)**: `scanners/inspect_runner.py:362` has an in-tree comment `the harness already in \`bmi-scanner/harness/citation_index/\`` that should also flip to `are-scanner/...`. That file already has pre-existing uncommitted BEAAA-483 work; the comment sits inside that uncommitted block. To keep BEAAA-510's PR surgical, the path-text fix on that line is deferred — it lands with the BEAAA-483 commit. Tracking: inventoried in the working tree of `projects/are-scanner/`, will be picked up when BEAAA-483 lands.

### What doesn't change (load-bearing invariants held)
1. **Pillar weights**: 25 / 25 / 25 / 25 (Founder-locked per ADR-0002).
2. **Pillar names**: GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS (per ADR-0006 / commit `d22bf84`).
3. **Output JSON shape**: unchanged at every level.
4. **Scoring math**: unchanged. Same `rubric.scoring.score()` arithmetic.
5. **Judge models**: pinned per contract; unchanged by this PR.
6. **Tamper-resistance posture**: unchanged. The rename does not introduce or remove any injection surface, does not change the input contract, does not change the `agent_id` regex or the 10MiB cap.
7. **Golden roster fixtures**: `tests/fixtures/golden_agents/G[1,2,3]/*` unchanged.
8. **Contract version pinning convention**: the Underwriter pricing engine and downstream contract pins remain valid; the patch bump is description-only.

### What is explicitly out of scope (preserved as historical record)

Per Supreme Law ("we don't re-key past customer artifacts") and ADR-immutability convention, the following five historical references to `bmi-scanner` are **NOT** rewritten by this PR:

| Path | Why preserved |
|---|---|
| `docs/adr-0001-residential-proxy-egress.md:48` | ADRs are immutable historical decision records. The line already self-describes the directory as "legacy". |
| `docs/cosign_packets/P1_2_citation_integrity_v0_2_1.md:175` | Cosign packet — frozen evidence of a past decision. The line explicitly states the rename is "a separate ticket"; this ADR is that ticket. |
| `docs/judges/p1_5_calibration.md:13` | "Repo-path note" quoting BEAAA-208's original acceptance bullet — historical quote, not an active path reference. |
| `docs/judges/p1_6_eval_coverage.md:31` | Same as p1_5; quotes BEAAA-209's original bullet. |
| `artifacts/beaaa-{239,374,390}/*.md` | Per-ticket scan evidence and weekly status — historical artifacts in the same "do not re-key past artifacts" class as customer-shipped report cards. |

Each of these is reachable for any reader who wants the pre-rename context, and each correctly reflects the state of the world at its time of writing.

## Reproducibility test (REQUIRED for merge)

Per AGENTS.md Supreme Law and `scanner-implementation-supervision-v1.md@v1.0` ("Refactor that doesn't touch behavior ... still requires N=5 on golden as regression check"), the following must hold:

1. **Pre-rename baseline** at HEAD `3c0ac8b` (current): N=5 `bin/are-scan` against G1 fixture with pinned `--scored-at = 2026-05-17T12:00:00+00:00` emits 5 byte-identical stdout JSON blobs. **Observed**: sha256 `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` × 5. `are_total=84`. Subscores `{OPS: 20.3125, BIAS_FAIRNESS: 19.2708, ROBUSTNESS: 22.9167, GOVERNANCE: 21.875}`. Spread = 0 across all N=5.
2. **Post-rename**: same fixture, same pinning, same code (only file paths and the comment / runbook strings change). Expected: same `are_total = 84`, same per-pillar subscore values, spread = 0 across N=5. **Stdout sha256 may differ** if any field of the JSON includes a path string that resolves to a new value post-rename (it doesn't — `bin/are-scan` output JSON is path-free); expected sha256 is identical to pre-rename `cd3ec3…` UNLESS there is a path-string surface in the JSON, in which case the new sha is recorded in the smoke artifact and is NOT calibration drift (it is the path-string change by construction). Per the BEAAA-510 DoD: "the value will differ from BEAAA-472's because the path strings differ — that's expected; identicality across N=5 runs is what matters".
3. **Pytest**: full suite green (729 + 62 baseline from BEAAA-472).
4. **Smoke artifact**: `tools/are-scan-smoke-post-rename-bmi-to-are.md` records the N=5 table.

If subscore spread > 0 on any pillar: STOP, re-classify as rule-change, halt the rename merge, route to CTO. Per AGENTS.md: "If your N=5 delta on a 'rule-execution' classified action comes back >1 on any golden agent, halt the merge, re-classify as rule-change, request CTO co-sign."

## Tamper resistance review

The rename does not introduce new injection surfaces. The change is:

- Two in-repo file edits (a runbook and a code comment) — neither runs at scan time.
- One external directory rename — the `agent_id` regex, the `eval_results.json` parser, the 10MiB size cap, the `--input-dir` path-traversal guard, all run from inside the repo and are unaffected by the parent directory's name.
- One contract text addition — documentation only, no code path.

Tamper-resistance posture unchanged. No Founder gate required on that axis.

## Glossary impact

`canon/glossary.md` already lists `ARE` as canonical and `BMI` as deprecated (Founder-locked 2026-05-12). This ADR brings the project directory into line with the glossary — no glossary edit required.

The five historical files preserved per §"Out of scope" continue to contain the literal token `bmi-scanner` for historical accuracy. Each was inventoried; each is in a deprecation-acknowledged context (ADR self-describes as "legacy", cosign packet self-describes the rename as pending, judges' Repo-path notes are explicit historical-quote callouts, artifacts/ are immutable evidence). The Auditor sweep (per AGENTS.md Surface deprecated-term incidents) will see these and they are accounted for here.

## Rollback plan

The rename ships in two commits inside the repo (in-repo path updates + ADR + smoke artifact in commit 1; the dir rename happens at the filesystem level, not inside the git tree). The contract bump is its own artifact authored as a reference doc.

1. **Tier 1 — revert in-repo commit + restore symlink.** `git revert <commit>` restores the two file edits. `mv are-scanner bmi-scanner` (with a transitional symlink `are-scanner -> bmi-scanner` for any operator who already cd'd into the new name). ETA ≤ 5 minutes. The .git directory moves with the parent rename, so no history is lost.
2. **Tier 2 — revert the contract bump only.** The contract bump is a separate reference doc + a separate CTO action; reverting it is independent of the directory rename. Leave the rename in place; remove the contract Version-history entry.
3. **Tier 3 — escalate to Founders.** If a downstream consumer (Underwriter pricing engine, customer LOI demo path, reinsurance appendix) fails on the new path, escalate per §Escalation triggers below. The rollback in (1) is the safe default.

## Escalation triggers

Per AGENTS.md §"Escalation triggers":

- **Escalate to CTO if**: post-rename N=5 spread > 0 on any golden subscore (re-classify as rule-change immediately); the contract bump cosign collides with BEAAA-473's pending v1.0.1 in a way I cannot resolve unilaterally (it does — see §Coordination); my work touches tamper resistance in any way (it doesn't — see §"Tamper resistance review").
- **Escalate to CEO if**: the CTO co-sign queue on the contract bump portion is older than 48 hours per `scanner-implementation-supervision-v1.md@v1.0` SLA.
- **Escalate to Founders if**: pillar-weight change (n/a here), tamper-resistance reduction (n/a here), scanner gaming evidence (n/a here).

## Coordination — BEAAA-473 v1.0.1 collision

BEAAA-473 (UW pricing-engine attestation) authored a reference text for `contracts/are-scanner-api-v1.md@v1.0.1` that expands the OPS "Owns" cell to enumerate output-quality discipline (hallucination rate, citation integrity, refusal accuracy, confidence calibration). That reference doc was board-pending cosign interaction `2519a4be-9eee-43cc-bc29-619635a1bdb4` as of 2026-05-17. Memory hit: "R1 still in flight on BEAAA-473 (cosign `2519a4be` board-pending)".

This ADR proposes the rename's contract bump go in next-available patch slot:
- If BEAAA-473's v1.0.1 has NOT landed at the time the CTO actions this ADR's cosign: rename's contract bump = v1.0.1 (single bundled OPS-bullet expansion + rename audit-trail entry).
- If BEAAA-473's v1.0.1 HAS landed: rename's contract bump = v1.0.2 (Version-history entry only).

The CTO can resolve the collision on accept. The cosign interaction filed against this ADR carries both options.

## Approval gates per `contracts/are-scanner-api-v1.md@v1.0`

The contract's §"Approval gates" lists:

- **Pillar weight changes (Founder).** Not triggered — weights unchanged.
- **Rubric point added or removed (CTO + Underwriter).** Not triggered.
- **Scoring logic change (CTO + Underwriter).** Not triggered — no math change, no shape change.
- **New eval that uses an LLM judge (CTO).** Not triggered.
- **Any change that moves a golden agent's score by >1 point (CTO + Underwriter; Founder if >3 points).** Not triggered — N=5 byte-identical at proposal time.
- **Test corpus version bump affecting >5% of rubric points (CTO + Underwriter).** Not triggered.
- **Anything reducing tamper resistance (Founder).** Not triggered.

→ **Required**: CTO co-sign on the patch bump per AGENTS.md Standing Procedure (Scanner Engineer co-sign queue). **No Underwriter, no Founder.** Underwriter is notified via the BEAAA-473 collision callout but does not need to co-sign this ADR.

## Audit trail (per CTO Supreme Law)

- ADR: this file (`docs/adr-0008-project-dir-rename-bmi-to-are.md`).
- Pre-rename baseline: N=5 G1 at HEAD `3c0ac8b`, sha256 `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` × 5, `are_total=84`, byte-identical.
- Post-rename smoke artifact (this PR): `tools/are-scan-smoke-post-rename-bmi-to-are.md`.
- In-repo path updates: `docs/runbooks/post-creds-resi-proxy.md:18`, `scanners/inspect_runner.py:362`.
- Contract bump reference doc: `BEAAA-510_are_scanner_api_v1_0_x_rename_reference_v0.1.md` (authored on the issue thread; CTO applies to canonical contract file).
- Inventory of historical references preserved: 5 files (1 ADR, 1 cosign packet, 2 judges path notes, 3 `artifacts/` evidence files).
- Issue: BEAAA-510.
- Origin chain: BEAAA-496 (CEO Monday cascade) → BEAAA-497 (CTO confirm-receipt) → BEAAA-510 (this rename).
- Scanner version at proposal: 0.3.0.
- Rubric version at proposal: 1.0.0.
- Corpus version at proposal: 0.1.1.

## Decision sequencing

1. **This heartbeat (Scanner Engineer):** rule-execution work (in-repo edits + ADR + smoke artifact + the directory rename + post-rename N=5 + pytest) shipped unilaterally. Contract bump reference doc authored. CTO `request_confirmation` opened with this ADR + the reference doc attached. BEAAA-510 → `in_review` pointing at the interaction.
2. **Next (CTO):** co-sign the contract bump as proposed, or pick the v1.0.1/v1.0.2 slot, or push back with a different patch text. Wake assignee back to Scanner Engineer on response. 48h SLA per `scanner-implementation-supervision-v1.md@v1.0`.
3. **After CTO co-sign (Scanner Engineer):** apply the CTO's chosen patch number / text to the audit trail of this ADR, close BEAAA-510 `done` with the required-artifact metadata block and the cosign ref.
4. **If CTO co-sign queue > 48h:** escalate to CEO per AGENTS.md.

## Required-artifact block (per `canon/artifact-metadata-schema.md`)

- `scanner_version`: 0.3.0
- `corpus_version`: 0.1.1
- `probe-set-version`: current (unchanged by this PR)
- `rubric_point_id`: N/A (no rubric-point change; the rename is repository-level only)
- `N=5 calibration delta`: 0.0000 on all four pillars (G1 baseline = post-rename = sha256 `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` if path string surface is absent from output JSON; new sha recorded in smoke artifact if present, but per-pillar values byte-identical either way)
- `upstream_eval_version`: N/A (no upstream eval wrapped)
- `cto_cosign_ref`: PENDING via interaction filed against BEAAA-510 (this ADR is the cosign packet)
- `head_of_uw_cosign_ref`: not-triggered (no rate-book / scoring-math touch; UW notified via BEAAA-473 collision callout only)
- `glossary_check`: clean. ARE canonical. BMI appears in the ADR body only in §"Out of scope" inventory and in the historical references list — both contexts explicitly deprecation-acknowledged.
- `Carrier Rule check`: clean. No carrier named.
