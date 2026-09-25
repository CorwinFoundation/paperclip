# BEAAA-34396 — Stage A acceptance basis (QA, independent)

Recorded 2026-09-24 by the QA seat. This file exists so the acceptance criteria for the
Stage A QA harness are pinned to the build specification instead of to whatever the
implementation eventually does. No candidate build exists yet (BEAAA-34389 is `todo`).

## Source, verified

The specification is not attached as a file. It is carried in eight `SPEC_SOURCE_PART n/8`
comments on BEAAA-34387 as base64 between `DATA_BEGIN` / `DATA_END`.

Reconstructed with `tests/qa/reconstruct_spec_34387.py`:

    bytes 55455
    sha256 0a096e6ae962176e88379a6d9e4a8afee838a66434f0b673137885dd6add3c46

That matches the SHA256 declared in the BEAAA-34396 description, so the eight parts are
complete and unaltered and the text below is quotable.

## Stage A scope and exit, verbatim

Section 12, "Stage A: first visible product and integration proof":

> Build BB-C01/C02, a reference adapter spike, mandate and evidence skeleton, demo receipts
> and invitation attribution. Demonstrate the five cases above.

> Exit: a shareable demo with truthful status, plus one reproducible mandate-to-outcome
> trace. No live protection is implied.

## The two P0 components the harness must assert against (spec lines 166-167)

| id | acceptance text, verbatim |
|---|---|
| BB-C01 | "A phone user can enter from an invitation, see support status, register, connect and complete the demo without technical setup instructions in the product flow. Unsupported agents never show Active." |
| BB-C02 | "Cross-account access is rejected; scopes are revocable; the agent cannot approve its own action or change payout details. Terms and consent versions are retained." |

Three of the abuse cases named in the BEAAA-34396 description are therefore spec-anchored,
not QA-invented: agent-cannot-self-approve, revocation, and tenant/cross-account isolation.
A harness asserting them is testing the specification, not mirroring the implementation.

Two further spec anchors that bound the truthfulness checks:

- Line 86, `Concept/demo` maturity row: "Demonstration tasks, illustrative offer, interest
  registration and explicitly simulated receipts. Demo / not activated. No live protected
  badge, premium collection or promised reimbursement."
- Line 218: "Late evidence cannot create retroactive enrollment. It may support a claim
  under a previously registered mandate and valid effective period. Store occurrence,
  receipt and processing timestamps separately."

## "The five cases above", resolved

Section 12 says only "Demonstrate the five cases above". The enumeration it points at is
the closing line of section 11, verbatim:

> A minimal technical demonstration should include one correct task, one incorrect task,
> one duplicate retry, one missing-evidence case and one claim reconstruction.
> Demonstrating only a successful checkout is insufficient.

That is the Stage A demonstration set, and it is what the harness has to cover. Mapped
against `tools/stage_a_abuse_harness.py` at master `0313498b`, with the spec row from the
section 11 validation table that defines each required result:

| # | case | spec row | probe | at `0313498b` |
|---|---|---|---|---|
| 1 | one correct task | "Correct purchase" | A15 | HOLDS |
| 2 | one incorrect task | "Wrong amount/SKU/date" | A16 | **FAILS** |
| 3 | one duplicate retry | "Duplicate retry" | A6, A7 | HOLDS (Path 1 gap filed as BEAAA-35579) |
| 4 | one missing-evidence case | "Omitted callback / missing telemetry" | A8, P1-2, P1-4 | HOLDS |
| 5 | one claim reconstruction | s10 "side-by-side view ... with time-ordered changes"; s12 exit "one reproducible mandate-to-outcome trace" | A17 | **FAILS** |

Cases 1, 2 and 5 had no probe before this run. Case 1 matters beyond its own result: every
other probe in the harness asserts that something is *refused*, so a build that refused
every request would have scored 20/21. A15 is the positive control that makes the rest of
the greens mean something.

## What the two new failures are

**A16 - the independent merchant record is stored and never read.** `EvidenceStore.record_case`
builds its mismatch list by comparing `asserted_amount_cents` against the mandate, which is
the agent's own assertion against the thing the agent was approved for. The merchant record
it then writes to `merchant_json` is never compared to either. Measured with a merchant that
returns something other than what it was called with: approved `SKU-1` at 100c, asserted
`SKU-1` at 100c, merchant recorded `SKU-OTHER` at 4900c, and the case reads `status='MATCH'`
with `reviewable_mismatches=[]`. The spec row requires the opposite result - "Mismatch is
visible with source evidence and creates a reviewable incident" - and section 10 requires a
retained "distinction between measured fact, agent assertion, customer assertion and derived
interpretation". A 49x divergence between the measured fact and the agent assertion currently
produces no distinction at all.

The demo cannot show this on its own: `SyntheticMerchantAdapter.purchase` echoes its
arguments back, so in the shipped demo the independent evidence can never differ from the
assertion and the join is structurally untestable. A16 substitutes a divergent adapter,
which is the only way to ask the question.

**A17 - the exit trace has no time.** The side-by-side half works: `packet()` returns
mandate, approval, agent assertion and independent merchant evidence as four separately
labelled sources. The time half does not exist. `PRAGMA table_info` on `cases` and
`executions` returns no timestamp column of any kind. The one instant reachable anywhere in
a packet is `execution.receipt_json.completed_at`, a processing stamp inside an unparsed
JSON string. There is no occurrence time, no separate receipt time, and nothing that orders
an approval against the execution it authorised - against section 10's "time-ordered
changes" and line 218's "Store occurrence, receipt and processing timestamps separately".
The Stage A exit asks for "one reproducible mandate-to-outcome trace"; what exists is
reproducible in content and not in time.

Both are implementation defects rather than harness findings, so both are filed against the
engineer seat rather than fixed here.

## Standing rule

Checks stay red-or-skipped with an explicit reason until a candidate exists; a passing
harness against no candidate would be a false green. The harness is report-only and always
exits 0, so it never gates a branch on its own.
