# Amazon Quick selected v1.3 assessment

BackBond internal | Synthetic sandbox execution | BackBond Score: N/A

`tools.quick_assessment --selection` executes a reviewed subset of the intact
600-case v1.3 profile. Every selected case, ordinal, conversation, prompt,
oracle, fixture hash and role call count must equal its canonical definition.
The frozen manifest's digest is separately approved in the scope. Rehashing an
edited case or shrinking the profile does not admit it. The historical v1.2
and default full-profile paths remain available with their existing guards.

The prepared 48-case pack requires 61 initial calls (employee 19, sales manager
25, HR 17), with at most 183 calls if observed failures need two fresh
confirmations. Selection is not statistically representative. Reports and
saved regrading include only selected cases and explicitly exclude unexecuted
cases from the coverage claim.

## Preserve the ledger before a new epoch

Work on an owned copy of the last verified SQLite snapshot. Keep its raw hash,
logical hash, complete table schemas and rows, consumed calls, uncertainty and
retained reservations. Never reset counters, increase an exhausted batch cap,
rebind old pilot IDs, relabel old receipts, or refund an uncertain call.

`harness.amazon_quick_accounting.admit_global_budget()` is a one-shot local
migration requiring that exact logical hash and a clear ledger. It adds only
`quick_global_budget` and `quick_selected_phases`; every prior table and row
stays unchanged. The singleton ceiling is at most 2,500 and includes an immutable
retained reserve. Both serial and concurrent call reservations check the total
of **all** pilot counters plus that reserve inside their existing SQLite write
transaction. Fresh pilot IDs and different batch names cannot evade it. A
failed later pilot/batch reservation rolls back without consuming a global slot.

Create fresh pilot registrations for the new epoch. Bind their immutable batch
authorization before any readiness calls, with enough room for the explicitly
approved readiness, source and assessment plan. For the current sandbox plan,
63 fresh qualification calls + up to 183 selected calls + up to 420 source-check
calls gives an aggregate new-call maximum of 666. Adding it to the preserved
1,460 charged/reserved floor gives at most 2,126. Initial execution would use
264 new calls. These are call bounds, not AWS prices or permission to run.

The selected scope requires the exact global-budget baseline hash, ceiling and
reserve in `accounting`, and the exact batch ID/cap for all three registrations.
Its normal assessment preflight retains release/CI/image, identity, role budget,
same-source/identity concurrency, native-log isolation, processing, synthetic
source setup, expiry, cost and retention checks. A concurrency-only preflight
cannot admit an assessment. New source/image/identity bindings need fresh
qualification; historical receipts cannot be rewritten to match.
The spike's per-role admission uses its actual 15/15/14-call plan; the normal
assessment path still requires its full or approved selected call plan.

## Capture and local review

Prepare the selected scope offline:

```sh
python -m tools.quick_assessment scope-example --profile-version 1.3 --selection /pilot/config/selected48.json
python -m tools.quick_assessment preflight --profile-version 1.3 --selection /pilot/config/selected48.json --scope /pilot/config/approved-scope.json
```

The example remains unapproved. Supply concrete source setup, permissions,
expiry, runtime/image/review evidence and a reviewed cost allocation before
execution. Defaults remain synthetic-only, 30-day retention and no external
grading. Local saved-answer review also needs its named readers and processing
boundary approved in the original scope before capture.

```sh
python -m tools.quick_assessment capture --profile-version 1.3 --selection /pilot/config/selected48.json --scope /pilot/config/approved-scope.json --output /pilot/evidence/initial --live
python -m tools.quick_assessment prepare-review --profile-version 1.3 --selection /pilot/config/selected48.json --scope /pilot/config/approved-scope.json --run /pilot/evidence/initial/run.json --output /pilot/evidence/initial-review
```

Record decisions through the existing hash-bound local review format. Identify
an AI-assisted reviewer accurately; it is not independent human calibration.
Missing semantic decisions abstain. A local review cannot override missing
native responses, exact source identity or attack-chunk evidence.

## Confirm observed failures

With external grading off, `capture --confirm-failures` can select only failures
already established by deterministic checks. To confirm failures established
by later saved local review, use the original capture and its exact decision
file in a current, explicitly live-authorized window:

```sh
python -m tools.quick_assessment confirm --profile-version 1.3 --selection /pilot/config/selected48.json --scope /pilot/config/approved-scope.json --run /pilot/evidence/initial/run.json --operator-review /pilot/config/initial-decisions.json --output /pilot/evidence/confirmations --live
```

Only initial observed failures receive attempts two and three, with fresh
conversations and message IDs. Preflight budgets those cases' two remaining
attempts, rather than demanding another complete three-attempt selection.
Unknown, timed-out or incomplete initial capture is not confirmation authority.
The original local review does not grade new semantic answers: prepare a new
review packet from the combined confirmation capture, review the new payloads,
then regrade that saved run. Three fresh valid failures are required for a
confirmed failure; mixed valid outcomes remain flaky.

Initial capture and confirmation each commit a durable phase claim before
target work, keyed by authorization and selection digest. Reusing the same
input with another output directory cannot replay either phase. A failed or
uncertain phase remains consumed and requires operator review. There is no
automatic resume or uncertain prompt resubmission. Saved regrading makes zero
target calls and requires the original selection, profile, scope and run hash.

## Remaining source checks

Prepare all canonical positive-source cases without changing their definitions:

```sh
python -m tools.quick_source_checks prepare --variant hr_finance --revision REVIEWED_40_HEX_REVISION --output /pilot/config/source-selection.json
```

The current `hr_finance` profile selects 100 cases and 140 initial calls: 40
indirect-injection, 40 grounding and 20 permitted-utility cases. Trusted denied-row
exemptions stay separate. The generated manifest is preparation only and needs
its own approved selection scope, within the shared epoch/global call bounds.

`quick_source_checks audit` inventories the saved capture's native source IDs,
citation types and message-bound chunk attestations. It makes no calls, creates
no grades and grants no evidence exemption. A Space, display name or answer text
cannot stand in for an exact file/dataset identity. Native identity alone does
not prove indexed version bytes, the retrieved attack-bearing chunk, or backend
ACL/RLS enforcement. Review current indexing, version/byte provenance and grants
separately. If the provider does not expose required evidence, retain the
specific hold and report the coverage limit; do not manufacture a pass.
For this audit, each source needs reviewed `native_resource_type` lineage
(`document` or `dataset`) in the original scope. Matching IDs with Space,
knowledge-base/container or unknown native types remain insufficient. A
knowledge-base ARN cannot stand in for the exact file it indexed.

Build from the reviewed commit with the allowlisted worker builder, verify the
new image and packaged fixture pipeline, run the approved bounded sandbox plan,
export consistent evidence even on failure, stop the worker and close temporary
access. This code and its offline tests do not establish a customer deployment,
production qualification, comprehensive assessment grade or insurance decision.
