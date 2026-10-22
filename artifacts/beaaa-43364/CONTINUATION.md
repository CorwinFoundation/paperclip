# BEAAA-43364 continuation checkpoint

Partial implementation only. No activation, live dispatch, install, QA_READY,
schedule, quota, scoring, corpus, target binding or admission hold changes.
Same retained branch: feat/beaaa-43364-one-use; draft PR1215 remains ineligible
for merge/deploy. Original checkpoint: 0e3d7857370b6edc650544d4500a48bffda3024e.

## Source reconciliation (2026-10-22 engineer continuation)

Base dd0e1707f53eb3bb2f06b2c6c5310eb4fbf8ebb4. The retained branch checkout
`beaaa-43364/backbond/scan-intake/server/app.py` and reviewed staging
`/opt/backbond/install-staging/beaaa-42799-dd0e1707-reviewed/backbond/scan-intake/server/app.py`
both currently hash to
8fddacc95447c68abf62c9a101f0654ca488f6752c93221fec1b0af1acda0e73.
The separate runtime-continuation checkout also started at the original checkpoint;
use the retained one-use branch for further work. The historical integration HEAD
70c008b16 and its app hash 91145fda differ; do not substitute them.
Live `ls /opt/backbond/scan-intake/server/app.py` still returns Permission denied.
Root/parent BEAAA-43362 retains live PID/source reconciliation and exact protected
target/access/budget confirmation. No live fingerprint inferred from snapshots.

## Implemented and verified

Disabled-by-default SQLite one-use primitive: atomic operator/request/exact-target
claim; non-resettable singleton consumption; dispatch_unknown durable before I/O;
integer micro-USD max10,000,000 and max100 started tests.

This continuation adds `paid_call`: stable case/phase/attempt identity and full
worst-case charge commit in the same BEGIN IMMEDIATE transaction before the
operation executes. A competing process cannot repeat that identity or overspend.
A failure/crash retains call_unknown and reserved spend. A returned response
also does not permit replay. No prompts, responses, credentials or target URLs
enter the paid-call ledger. `finish` closes paid admission without refund/reset
and without asserting success. Unknown dispatch cannot be terminalized merely
because time elapsed.

Verification: `python3 -m pytest -q tests/test_controlled_admission.py`: 26 passed.
Includes actual child-process os._exit during the admitted operation; restart
rejects repeat and retains spend. Also concurrent identical attempts, concurrent
competing USD6 calls, commit-before-I/O observation from a separate SQLite
connection, unknown/invalid bound rejection, failure/restart, and terminal closure.
`git diff --check`: passed. These are primitive-level checks, NOT completed
end-to-end intake/scanner acceptance evidence.

## Exact remaining implementation

1. In `backbond/scan-intake/server/app.py`, authenticate selected operator using
   service-held token hash/actor configuration (existing `_role_actor` is an
   authentication pattern, not authorization to reuse a release-role token).
   Bind immutable selected request identity and full exact target configuration.
   Do not read actor identity from caller assertions; do not arm placeholders.
   Preserve both general holds; ordinary/public callers stay closed. Avoid any
   legacy connectivity call escaping the budget. Existing connection-check
   endpoint also needs explicit closure during controlled admission.
2. Wire the durable dispatch transition at actual queue launch, not just HTTP
   acceptance: `qa_runtime.ScanQueue._launch`, queued promotion and BAC recovery
   must never launch consumed/unknown controlled work a second time. Queue
   currently has no controlled-admission hook. Keep existing DB/quota identities.
3. Propagate service-controlled bounded scope into scanner execution. The Starter
   execution path is `scanners/customer_agent_walker.py`:
   `customer_agent_probe_run_fn`, target `runner.complete`, multi-turn dispatch,
   and `scanners/starter_judge.py:OpenRouterJudgeClient.complete` for judge I/O.
   Trace the actual runner transport and every internal retry before wiring.
   Use stable attempt IDs and `paid_call` immediately before each single paid
   network attempt, with an independently valid COMPLETE worst-case price bound.
   Unknown pricing, unbounded output/tools, or insufficient budget must stop
   before I/O. A synthetic cap flag or observed after-call usage is insufficient.
   Intake and scanner are separate services: do not assume a shared SQLite path
   exists. Resolve the service-authorized persistent guard transport/storage
   before attempting to propagate it; never put protected values in artifacts.
4. Add end-to-end tests for wrong actor/request/target, concurrent/replayed HTTP
   submissions, lost acceptance, queued/BAC recovery, restart/dispatch_unknown,
   ordinary caller closure, all paid boundary types, truthful incomplete terminal
   result, and preservation of status/report access and quota state.
5. Implement/test request-preserving idempotent install/rollback commands.
   Disable reservation before restoring code; retain reservation/paid-call/quota
   DB state and both holds. Never automatically redispatch unknown work.

No completed candidate claim or QA acceptance request until all five are wired.
Post the completed candidate SHA and indexed evidence to existing BEAAA-43363;
use that same issue for interim evidence with explicit PARTIAL labeling.
No deploy/dispatch until independent QA acceptance and Root scope confirmation.

## Queue boundary checkpoint (2026-10-22 next heartbeat)

Added optional service-owned `ScanQueue.before_dispatch(scan_id, payload)` at
`_launch`, shared by direct submit, queued promotion and acknowledged BAC recovery.
The callback executes before scanner start. Exceptions preserve dispatch_unknown
and the active slot; payloads are cleared. No app configuration enables this hook
in this checkpoint. No token, target or admission hold was changed.

Focused verification: 27 tests passed in tests/test_controlled_admission.py.
New queue integration check observes the committed reservation from an independent
connection inside scanner start; a lost acceptance and queued-path replay invoke
scanner start exactly once. Existing 26 primitive checks remain passing.

Next implementation requires choosing the service-authorized cross-service guard
transport/storage; a local intake SQLite path is not established scanner storage.
Parent/CTO must identify the supported shared persistent store or authenticated
guard endpoint and its existing service authorization. Exact target provisioning
still belongs to Root; no placeholder may be armed. Remaining steps above apply.

## PR1214 source reuse checkpoint (2026-10-22 Root source reference)

Reused exact source from 8286d6b5d3c517507a1a1ab70b719d66de301ea5:
scanners/evaluator_budget.py, customer_agent_walker.py, starter_judge.py,
live_probe_orchestrator.py; backbond/scan-intake/tests/test_evaluator_budget_43195.py;
docs/evaluator-budget-43195.md. No private entrypoint, UID, credentials, unit,
network exception, target configuration or activation copied.

The evaluator ledger belongs in scanner persistent storage and uses normal scan_id.
This supersedes the previous assumption that a newly designed distributed guard
is necessarily required. It does NOT prove combined target+judge spend: the reused
ledger covers evaluator charges, while controlled_admission is still an unwired
intake primitive. A full USD10 combined cap, stable paid attempt replay prevention,
and max100 case enforcement at actual target dispatch remain unproven.
No current production interface/source identity is inferred from this source reuse.

Focused verification: python3 -m pytest -q
backbond/scan-intake/tests/test_evaluator_budget_43195.py
tests/test_controlled_admission.py -> 58 passed. No paid calls.
Next code unit: selected-operator HTTP and queue binding with immutable normal
scanner request identity, preserving both general holds. Parent/CTO43362 still
owns exact target/access and current deployed-interface confirmation. Candidate
remains partial; Root/CTO routes completed candidate to QA43363 (no denied comment
retry). No deployment or completed acceptance claim.


## Expired private allowance removal (2026-10-22)

Base identity: 51e2982e7 on the retained beaaa-43364 branch; no live source
identity inferred. Removed _private_acceptance and both route bypasses. Closed
QA_READY now rejects the former loopback/token/code/email allowance before any
connectivity I/O or scan reservation. QA_TERMS_APPROVED remains independently
required for scans. Status/report routes and persisted quota state are unchanged.

Verification: python3 -m pytest -q
backbond/scan-intake/tests/test_findings_dispatch.py tests/test_controlled_admission.py
-> 58 passed in 8.54s. Seven added tests execute the actual route function ASTs
with real intake store/queue for IPv4/IPv6 loopback and ordinary remote callers,
plus the separate terms hold. These are deterministic route-level tests, not a
full ASGI or deployed-service test. No paid calls or deployment.

CTO comment dd1666d1 supplies the proposed transport: opaque permit/request/target
digest on existing service-authenticated connectivity-check and /scans APIs;
Scanner validates tenant against its own persistent ledger. No reverse callback
or shared intake SQLite assumption. Implementation can proceed disabled; exact
Root target/executor and Scanner service-owner deploy/storage authority remain
unconfirmed. Do not reuse old notes treating interface design as unanswered.

Next code unit: disabled authenticated permit/request/target-digest boundary on
existing Scanner API, then selected-operator intake wiring and combined persistent
connectivity/target/judge pre-I/O budget. This checkpoint does not complete QA
defect 2: the controlled HTTP gate remains unwired. Defects 3/4 and exact disabled
install/rollback also remain. No completed candidate or acceptance claim.

## Judge paid-I/O checkpoint (2026-10-22)

OpenRouterJudgeClient accepts a validated controlled_permit and requires stable
case identity on controlled calls. It retains the existing pinned-model input
bound, provider price ceilings, no fallback and max512 output. The shared permit
commits the full conservative bound before the one HTTP operation. A timeout,
HTTP error or unreadable response cannot restore the call identity or budget.
judge_starter_case supplies case.case_id. Walker forwards runner.controlled_permit
when present and avoids creating an independent evaluator allowance for that path.
Ordinary evaluator behavior is unchanged. This does NOT wire target paid calls.

Focused verification: permit + evaluator tests, 42 passed in 3.99s. New checks:
reservation visible inside mock HTTP, same case replay blocked, exhausted combined
budget/missing identity/unknown model/input overflow all deny before HTTP, and
HTTP timeout remains consumed after reopening the persistent ledger. No paid I/O.

CTO split agreed in source issue: CTO owns new harness/controlled_permit_loader.py,
scripts/controlled_permit_state.py, tests/test_controlled_permit_loader.py,
docs/deploy/controlled-permit-state.md and Scanner api/ transport files. Engineer
avoids api/ and retains intake/ledger plus target/judge/orchestrator hooks.
Interface agreed: runner.controlled_permit is a validated ScannerPermit, using
scan_id and complete canonical OperatorAdmission.fingerprint(payload); no caller
cost/caps. Never assume loader deployment or storage authorization is complete.

Next exact source unit: enforce controlled target calls at actual single-attempt
transport, stable case/turn phase identity, service-owned complete worst-case cost
bound and rejection of unsupported/unbounded runner/tool/retry paths before I/O.
Then propagate the validated permit in orchestrator context and verify both phases
share the USD10/max100 ledger. Coordinate CTO transport before install/rollback.
PR1215 remains partial and ineligible for merge/deploy. No activation or live call.

## Canonical identity correction (2026-10-22)

ScannerPermit.call now rejects test IDs outside existing STARTER_V5_CASES before
accounting/reservation/I/O. Pack rows, scoring and versions unchanged. Focused
real 32-hex scan-ID checks cover all 100 canonical cases, shared preflight budget,
replay, and fabricated/case-changed/whitespace IDs with zero accounting or I/O.
Verification: admission + loader suites, 62 passed in 10.79s; diff check clean.
Next: supported controlled connection-check integration, followed by target paid
transport wiring. PR1215 remains partial; activation disabled.

## Legacy connectivity closure (2026-10-22)

Starting source 8f2f61c10. The legacy intake connection_check route now rejects
whenever controlled_operator is configured, before code resolution and any
connectivity I/O. It has no metered permit transport, so it cannot safely serve
controlled checks. Both global admission holds remain unchanged.

Focused verification: findings-dispatch suite 40 passed in 18.10s, including six
new cases across QA_READY=0/1 and IPv4/IPv6 loopback/remote callers. No paid I/O.
Next exact unit: implement supported controlled preflight transport from intake
to the existing authenticated Scanner connectivity-check API using its persistent
permit ledger. Then complete target single-attempt paid transport and combined
budget tests. Candidate remains partial, disabled and ineligible for activation.

## Controlled walker closure (2026-10-22)

Starting source afb592930. Walker now rejects any controlled_permit before
runner.complete, complete_conversation, progress callbacks or judge construction:
controlled_target_transport_unavailable. Judge metering alone cannot cap target
transport retries. This explicit temporary closure must be replaced by verified
per-attempt target metering before controlled execution can work. Ordinary runs
are unchanged. Focused tests/test_controlled_walker_transport.py: 3 passed in0.66s.
No paid I/O, activation, install, holds, scoring or corpus changes.

Paperclip checkpoint POST /api/issues/60b60fd8-a4e3-4d54-825d-ba06fefa5ea5/comments
failed HTTP403 Forbidden in this runtime. Next action: resume same branch with
metered controlled connectivity and target transport, then combined cap tests and
disabled install/rollback. Partial PR1215 remains ineligible for merge/deploy.

## Scanner-local preflight bridge (2026-10-22)

Resumed confirmed PR1215 origin head 8c94edf4283a1da639f5a9a5b027bcf53075737d.
Added execute_preflight in harness/controlled_permit_loader.py: authenticated
server-owned tenant/opaque permit lookup, complete target fingerprint validation,
and persistent sole preflight reservation before the supplied bounded operation.
Database connection closes on all outcomes; unknown outcome remains consumed.
This is an internal bridge only: HTTP routes remain closed and no transport is
claimed bounded by this helper. No activation, paid I/O, deployment, corpus or
scoring change. No production/live source identity inferred.

Focused checks: python3 -m pytest -q tests/test_controlled_permit_loader.py
 tests/test_controlled_admission.py -> 64 passed in 9.48s. Added offline tests
observe committed spend/check_unknown from inside operation, reject wrong target,
wrong tenant and unknown price before I/O, and forbid replay after success/timeout.

Next action: integrate a service-owned single-attempt priced target transport
with this preflight bridge and the controlled walker, then wire the authenticated
intake connection check and verify combined target/judge USD10/100-test accounting.
PR1215 remains partial, not ready for QA or merge. Exact Root target confirmation
and activation remain with parent BEAAA-43362. Return todo to Scanner Engineer;
no live continuation is queued in this run.

## Fixed-price target adapter checkpoint (2026-10-22)

Added harness/controlled_target_transport.py. Service-only immutable
FixedRequestPrice must bind the complete execution payload digest and a complete
per-request cost ceiling, including any tools/downstream work. No HTTP/config/env
policy loader and no price defaults are provided. Token-priced/unbounded targets
remain unsupported. Adapter constructs the existing base REST runner internally;
no subclasses, alternate protocols, conversation adapters or custom callbacks.
The existing runner retains scoped egress, no redirects/retries, bounded streaming
and credential handling. Adapter reserves persistent shared spend before invoking
that runner for either preflight or a stable case/phase target call.

Verification: python3 -m pytest -q tests/test_controlled_target_transport.py
 tests/test_controlled_walker_transport.py tests/test_controlled_permit_loader.py
 tests/test_controlled_admission.py -> 75 passed in 11.41s. New offline checks
prove combined preflight+target spend leaves insufficient judge budget, reservation
visible before operation, replay denied, lost response consumed across restart,
unknown/invalid price and changed target/protocol rejected. Tests mock the base
runner method; they do not establish deployed HTTP or actual target pricing.

Next exact action: bind a service-owned target pricing policy only once the full
per-request upper bound is known, then integrate this adapter at controlled
connection-check and walker case/turn boundaries. Keep unsupported paths closed.
Do not remove the walker closure merely because this adapter exists. Complete
integrated HTTP/queue/target/judge tests and disabled install/rollback before QA.
PR1215 remains partial/ineligible for merge/deploy; no activation or paid I/O.
Return todo to the same Scanner Engineer; no continuation run is queued.

## Real HTTP boundary verification (2026-10-22)

Starting reviewed PR1215 head 9869ceaf42cb9aba610397404ef8ba9fda214bdd.
Added four offline integration cases executing the actual base HTTP runner, with
only ScopedEgressAllowlist.client network creation replaced by httpx.MockTransport.
Verified exact endpoint/auth/body, committed combined spend and call_unknown at
the send boundary, no redirected/automatic second request on HTTP307/503/timeout,
and replay refusal from a reopened permit after every outcome, without refunds.
No real network request or protected target was used. These tests establish the
base adapter send boundary, not deployed pricing or end-to-end admission wiring.

Verification: python3 -m pytest -q tests/test_controlled_target_transport.py
 tests/test_controlled_walker_transport.py tests/test_controlled_permit_loader.py
 tests/test_controlled_admission.py -> 79 passed in 12.50s.

Next exact action: integrate the disabled adapter into the controlled Scanner
connectivity-check path with a service-owned exact-target price policy; absent
that policy, reject before I/O. Then wire walker case/turn boundaries and complete
combined HTTP/queue/target/judge acceptance and disabled install/rollback.
Exact protected target and full per-request price remain Root/parent43362-owned.
Walker closure remains; draft PR1215 is incomplete and ineligible for activation.

## Metered walker boundary (2026-10-22)

Starting PR1215 head 194b88fe2. Controlled Starter V5 requires exact
BoundScannerPermit and ControlledTargetTransport types before any callback or
judge construction. Single turns use canonical case ID/target; multi-turn cases
use canonical ID/target-turn-N and bounded messages through the base single-request
runner. No conversation/tool adapter or retry path is used. Temporary message
payload is cleared on every outcome. Ordinary execution remains unchanged.

Focused verification: walker, target transport and policy suites: 34 passed in
10.38s; git diff --check clean. Offline 100-case integration exercises all canonical
cases with target and judge sharing one persistent ledger; asserts exact spend,
100 distinct tests and replay rejection before another target operation. Another
walker check spends USD10 in preflight and denies first case before target I/O.
No real network or paid call occurred.

Still PARTIAL: the orchestrator does not construct/inject the service-owned
adapter, so runtime controlled execution remains closed. Exact next action:
wire disabled service-owned policy/adapter construction into orchestrator using
the same full payload digest, then complete operator admission and disabled
request-preserving install/rollback integration. Keep existing PR1215; do not
claim complete QA acceptance or activate. Parent43362 retains exact live target
and Root confirmation. No deployment, arming, holds or quota changes.

## Explicit service-owned orchestrator target binding (2026-10-22)

The retained checkout already contained walker commit 3b7297484. Added an optional
service-only controlled_target_config argument to run_scan. Controlled descriptors
now require exact full-payload binding to the loaded permit, resolved vault secret
and endpoint before runner construction. Policy path and digest are never read
from endpoint_config. Missing configuration stays closed. The walker receives
both permit and metered transport. Existing finally cleanup closes the permit.

Verification: 41 focused policy/walker/orchestrator tests passed; diff check clean.
All operations were offline. No deployment, arming, dispatch, holds or quota changes.
Still partial: service runtime must supply the explicit configuration at its owned
startup boundary; admission and disabled state-preserving install/rollback remain.
Next action: wire service startup configuration and retain default-closed behavior
with an integration test, then complete the install/rollback command on PR1215.
Exact live target/price and activation remain parent43362/Root-owned.

## Supported local one-time arming (2026-10-22)

Starting b3e48400e, added loader arm_once and CLI arm-once accepting the exact
original protected record via stdin. BEGIN IMMEDIATE verifies all persisted
metadata/binding, expiry, disabled/unconsumed/unclaimed state and empty attempt
ledgers, then commits enablement with an irreversible arming marker. Disable
retains that marker; even unused disabled reservations cannot be rearmed.
No HTTP administration, reset, DB identity change, hold change or live action.
Seven focused offline tests passed, including real CLI subprocess/replay,
expired/rebound/consumed/unknown/preflight refusals and disable/rearm refusal.
Diff check passed. Documentation now describes the supported local command.

PARTIAL: service startup configuration/default-closed full HTTP integration and
request-preserving disabled install/rollback remain incomplete. Existing
one-check/100-case and protocol/expiry/proof-order evidence must be integrated
at exact candidate before handoff to QA43363. No complete candidate claim.
Next single action: wire service-owned startup configuration into runtime launch
with a default-closed HTTP integration test, retaining both holds. Keep PR1215.
Exact live target/policy and runtime authority remain parent43362/Root-owned.

## Production worker startup source checkpoint (2026-10-22)

Starting eb5e75002, wired the actual production _prober_target to load and forward
service-owned ARE_CONTROLLED_TARGET_CONFIG only for controlled descriptors.
Private absolute config file and parent ownership/modes, no symlink, bounded
size and duplicate-key validation; missing config rejects before orchestrator.
No permit arming or protected value reads occurred. Ordinary calls ignore config
and retain their original run_scan keyword signature.

Verification: startup + target policy suites: 31 passed in 1.26s; diff check clean.
Tests compile the actual worker function with inert dependencies; no deployed
HTTP integration or service-runtime acceptance is claimed.
Next exact action: wire intake controlled_operator from protected service startup
configuration with both holds enforced, then disabled install/rollback integration.
Final master/mail reconciliation and integrated QA43363 handoff remain. PR1215
is partial and ineligible for merge/deploy; no live operation or paid I/O.

## Protected intake startup checkpoint (2026-10-22)

Added controlled_startup.load_operator and optional QA_CONTROLLED_OPERATOR_CONFIG
startup wiring in app.py. Configuration contains exactly ledger_path, actor,
request_id, target and token_sha256. File and immediate parent must be private,
service-owned, absolute and non-symlink; JSON is bounded and rejects duplicates.
Existing SQLite storage is opened mode=rw, required tables and persisted binding
are checked; missing storage is never provisioned. Both QA_READY and
QA_TERMS_APPROVED must be 0. Loading does not arm or reset any reservation.

Verification: PYTHONPATH=backbond/scan-intake/server:. python3 -m pytest -q
backbond/scan-intake/tests/test_controlled_startup.py
backbond/scan-intake/tests/test_controlled_model_guard.py -> 10 passed in 1.87s.
Initial invocation without PYTHONPATH failed collection; corrected invocation above.
No live calls, protected configuration reads, arming, deployment or quota changes.

PARTIAL: loader currently retains the startup-thread SQLite connection; the queue
uses a separate dispatch thread. Next exact action: implement per-operation
connection ownership for the operator adapter and prove startup-to-queue behavior
with an offline HTTP/queue integration test. Then finish disabled request-preserving
install/rollback, master reconciliation, and QA43363 handoff. PR1215 is not ready
for merge/deploy. Exact live target/access remains parent43362/Root-owned.

## Thread-owned intake connection correction (2026-10-22)

Continued retained HEAD 5e4dcad7e (the two entries in Root's earlier receipt
were already incorporated by that commit; no files moved or reset). Production
PersistentOperator now opens existing mode=rw SQLite storage per claim, scan bind
and dispatch operation, closes it in finally, and retains only immutable binding
and storage path at startup. No connection crosses the startup/queue thread.

Focused verification: 11 startup tests passed. Added actual start_scan route AST
plus real store/queue and protected startup loader: ordinary token denied, selected
request accepted once, lost Scanner response retains dispatch_unknown, restart
replay denied, status remains readable and QA_READY stays 0. Separate thread
invokes dispatch then restart denies redispatch. Disabled admission check uses a
valid exact payload, proving the disabled state itself denies claim. No network,
paid calls, live source/config reads, arming, deployment or quota changes.

PARTIAL: disabled request-preserving idempotent install/rollback command and its
offline checks still need completion, followed by exact integrated candidate
verification and existing QA43363 handoff. The route test uses actual function AST
and real queue/store, not a deployed ASGI observation. No completed candidate or
QA acceptance claim. Parent43362/Root retains exact live target/access authority.

## Offline disabled install/rollback verification (2026-10-22 CTO)

Retained 7b1c82217; no checkout reset or source substitution. Added subprocess CLI
regression tests for proposed provision-disabled/disable/arm-once commands against
private temporary storage. Same-record provisioning and repeated disable preserve
all SQL state; changed binding fails without mutation; consumed dispatch_unknown
and 123 micro-USD reserved spend survive disable, reprovision and failed rearm.
Unconsumed disable also permanently rejects rearm. No live installation performed.

Verification: eight existing controlled suites (admission, loader, Scanner startup,
target policy, target transport, walker, intake startup and model guard) passed:
134 passed in 24.39s. New CLI suite: 2 passed in 2.87s. Initial CLI collection
import and fixture payload omissions were corrected before the passing run.

PARTIAL, source review only: api/app.py production connectivity route still calls
connectivity_check_handler without controlled_transport. The explicit handler
transport tests therefore do not establish production natural-preflight wiring.
Controlled preflight remains 503, safely closed. CTO must wire protected startup
policy to that authenticated route and prove actual ASGI check -> scan behavior
before deployment eligibility. Documentation's earlier integration limits remain
applicable. Master/mail reconciliation is also not complete; origin/master local
ref c4fe716c07c5ae56396dbfde2f0211ccc9205595 is reference-only, not a fresh remote
or live fingerprint. QA43363 receives this exact partial head for independent
source review, with the gap named rather than a completion/dispatch claim.
Root/parent43362 continues to own live source/target/access/budget confirmation.

## CTO scoped resume 2026-10-22 — source review handoff

Preserved all uncommitted edits from e1bd2f6d; committed as 6404406cb.
Merged freshly fetched origin/master 63b1036ed75b1f4fb2ac4f6f085e0f291adc49df
in ef38782ae. Resolved app conflict by retaining closed public admission and
removing the expired private allowance. Preserved both sets of Findings tests.
Scanner worker progress changes merged automatically; ordinary regression passes.
Mail adapter is byte-identical to master (SHA256
078076327d281d2a4a8dabb7317349c4913115afb0b4ac7888b3aeb1944ccf27).
This is source identity, not installed/live identity.

Protected config now contains digests and policy references only. Complete
execution payload travels in memory from authenticated natural preflight and
scan handlers to the bound worker; the descriptor alone cannot authorize a
changed payload. Natural preflight shares Scanner's persistent target/judge cap.
Separate intake and Scanner ledgers are intentional, with matching exact digest.

Fixed QA b27c4d04 ordering: authentication/binding, holds, legacy/proof and Scanner
identifier checks precede quota reservation; successful reservation precedes
atomic one-use claim. A losing claim cancels only its unsent quota reservation.
No consumption reset/rearm/retry is introduced. Real proof-store tests cover
legacy refusal and IntakeError with intake consumed=0 and no scan dispatch.

No prior regression process remained. Retained pytest failure cache named the two
lost-scan tests but no complete prior result survived; both reproduced PASS before
changes. Earlier reported 143 focused tests remain historical, not final QA.
On integrated source: 12 real HTTP cases passed (synchronous and durable worker),
covering one natural check, 100 canonical cases, combined cap, changed/expired
binding, lost check/scan responses and restart denial, config/SQLite secret absence,
and real-proof claim ordering. All I/O used inert offline transports. In their
combined run 51/52 passed; the sole failure was an old AST fixture's first-claim
assertion also running on replay. Corrected that fixture; all 40 Findings route
tests then passed in 6.39s. No production code changed after the 12 HTTP passes.
97 ordinary handler/production-worker/connectivity/startup tests passed in 5.83s.
Both merged prober-progress regression cases passed in the earlier integrated run.
Initial combined run exposed obsolete AST preflight/claim-order assumptions;
updated those and retained denied unmetered/public paths. git diff --check passed.

Exact final git head is provided in the issue/PR handoff (this document cannot
self-embed its commit). Draft PR1215 stays draft. QA43363 owns independent exact-head
source review; parent43362/Root owns exact live target, actual source, pricing,
state/deployment/arming plan and runtime authority. No deploy, protected config
read, arming, paid call, public opening, schedule change or report send occurred.

## Root-requested QA corrections 2026-10-22

Continued exact d8f22c65e. Permit tests now import the repository root explicitly
and use STARTER_V5_CASES IDs. Saturated-count fixture isolates the numeric 100-case
limit; noncanonical-ID denial has a separate zero-I/O assertion. Combined target
and judge USD10, restart/replay, concurrency, unknown-cost and timeout assertions
remain enforced. No production guard or allowance changed.

Binding now executes inside the guarded route. A binding exception before Scanner
I/O records launch_failed and returns the original newly issued status capability.
The consumed permit and scan record remain; terminal unsent status frees the active
slot without rearm, refund or retry. Four real-proof/store HTTP regressions cover
before-write and lost-bind-response failures with synchronous/durable Scanner
fixtures, stable polling, zero scan I/O, zero active slots and reload replay denial.
The deployment plan also explicitly states QueueFull remains consumed.

Focused validation: 12 permit tests pass from both repository root and the tests
subdirectory; combined permit + new HTTP regressions: 16 passed, 12 deselected.
Findings route regression rerun is justified by the changed start_scan exception
boundary. Exact result and commit appear in the issue handoff. No unaffected
ordinary/100-case suite rerun, paid CI, deployment, arming, live calls or sends.

## Accepted @4 form correction (2026-10-22 CTO wake)

Starting checkout and remote PR1215 both resolve to c16be1a23e7fd2be662345470954bbd7e11fd29d,
not the historical starter-42618-integration checkout. This correction is on the
existing feat/beaaa-43364-one-use branch. Root comments 757e6a13 and f353ebe2
supersede the earlier qualified source pass for form membership.

The controlled API and walker now require starter-100@4. Immediately after the
existing reserve_starter_cases call, the orchestrator binds the permit to the
existing form authority for the same scan/tenant/agent/pack. The permit ledger
persists the complete manifest SHA256 and immutable scope in a new singleton
controlled_accepted_form table. Each paid call rereads the saved form, checks its
pinned digest and selected 100 IDs before test/spend accounting. No supplied ID
list, V5 alias, redraw, grading change or target change is used. Changed or
replaced forms and changed scope cannot replace the binding after restart.
Existing one-use, phase deduplication, unknown-state and USD10 accounting remain.

Offline evidence uses the real @4 assembler and real bound permit. It verifies
100 unique IDs, ten per family, accepted/attempted ledger identity equality,
nonselected valid bank and arbitrary ID rejection with no additional spend/I/O,
shared natural-check/case budget, invalid prices, durable replay/unknown denial,
same-scan form retention, and rejection of another real hash-valid form replacing
the selected form. The HTTP fixture now exercises the fixed REST @4 path and
requires exactly 101 target operations (one check plus 100 cases) and 100 judges.
All operations are injected; no settings, actual target or model calls are used.

Fixture isolation incident: the first @4 HTTP-fixture verification inherited the
FormStore default and wrote synthetic forms to the local beai-agent-owned
/var/lib/are-scanner/variable-forms.sqlite3 (observed mtime 12:58 UTC). This is not
live Scanner provenance or evidence of a production scan. No protected Scanner
runtime was inspected. The fixture now pins BACKBOND_VARIABLE_FORMS_DB to its
pytest temporary directory. The default-path file was left untouched to avoid
deleting unknown state. Root owns any later classification/cleanup of that file;
this is not authority to reopen the closed runtime read window.

Target helper 3d73 and its normalized request are unchanged. CI5/CI3 fixture
repairs, diagnostic39778366627 and existing isolated queues are preserved. No
manual CI rerun, paid fallback, deployment, release build, allowance, activation,
report or send was performed. PR1215 remains draft pending exact-source canonical
QA43363, required isolated CI/protected integration, then actual image/provenance
and Root's release/state decision. General admission stays closed and schedule
PAUSED. This source correction does not wait on future runtime observations.
