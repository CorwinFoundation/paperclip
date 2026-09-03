# Full-scan execution layer

This runbook defines what the scanner actually executes when a caller submits a `full` scan. It supersedes any use of the historic ?1,180 tests? claim as an executed-test count.

## Current registered inventory

The registry contains 2,242 rows. It exposes 2,202 active executable rows, 2,213 target-bound runtime rows before identity collapse, and 2,201 distinct runtime tests.

The accepted BEAAA-18720 inventory contributes exactly 2,000 registered, `reported_not_scored` tests across 87 families:

- 1,782 cases use the pinned `content_candidate` adapter;
- 218 cases reuse an already executable family/case adapter;
- all 2,000 retain the accepted inventory hash, oracle, applicability, and provenance metadata; and
- no candidate changes measured score math or claims a rubric mapping.

The remaining 201 distinct runtime tests come from existing registered content outside the accepted inventory. The active executable row count is a registry/published-count gate; use the 2,201 distinct-runtime figure when answering how many different tests a full scanner run performs.

## Request and execution flow

1. `POST /scanner/v1/scans` validates the tenant, endpoint, credential, seed, and full-scan request.
2. The API returns `202` immediately with a `scan_id` and records `queued` status.
3. A bounded background worker builds the active registry snapshot and invokes every active case against the submitted endpoint.
4. Every case writes an atomic JSON evidence document, including payload, target response, expected behavior, judge derivation, verdict, hashes, timing, and budget delta.
5. The dispatcher reconciles expected, attempted, terminal, and evidenced case IDs. Missing workers and runner errors are explicit incomplete rows; nothing silently disappears.
6. The scanner writes a signed `manifest.json` containing all case references, verdict counts, reconciliation counts, model versions, registry hash, and budget accounting.
7. `GET /scan/{scan_id}` returns `queued`, `running`, `complete`, `incomplete`, or `failed`. A behavioral failure is a completed test with verdict `fail`; infrastructure or coverage failure makes the analysis incomplete.

The legacy four-prompt walker is not run before a full registry scan and cannot make a full scan complete. Smoke scans retain their existing legacy path.

## Approved initial budget

The initial evaluation authorization is enforced in code, before calls are made:

- maximum target calls: `6,000`;
- maximum judge calls: `4,000`;
- maximum estimated judge cost: `$99.99`;
- maximum wall clock: `6 hours`;
- maximum reserved judge output: `256 tokens` per call.

The 1,782 cases on the candidate adapter plan 2,805 target calls and 2,468 judge calls; reused adapters remain within the residual budget. Judge cost is conservatively reserved before each call. If any ceiling would be crossed, the affected case is written as `incomplete` with `budget_exceeded:<limit>`, and no over-budget call is sent.

Environment overrides:

```text
ARE_FULL_SCAN_MAX_TARGET_CALLS
ARE_FULL_SCAN_MAX_JUDGE_CALLS
ARE_FULL_SCAN_MAX_JUDGE_COST_USD
ARE_FULL_SCAN_MAX_WALL_CLOCK_SECONDS
ARE_FULL_SCAN_JUDGE_MODEL
ARE_FULL_SCAN_JUDGE_MAX_OUTPUT_TOKENS
ARE_FULL_SCAN_EVIDENCE_DIR
```

The default OpenRouter judge is pinned to `anthropic/claude-haiku-4.5`. If OpenRouter is unavailable, an Anthropic credential can select the pinned direct Anthropic runner. Judge calls open only the provider?s scoped egress route for the duration of that call.

## Evidence layout

Default Linux storage:

```text
/var/lib/are-scanner/evidence/<job_id>/manifest.json
/var/lib/are-scanner/evidence/<job_id>/<test_id>/<case_id>.json
```

Stable API references use the equivalent `evidence://<job_id>/...` form. Credentials are never included in evidence, manifests, logs, or result objects.

The signed manifest is the source of truth for what ran. The published executed-test count must equal all of these values:

- active executable registry cases;
- manifest `counts.expected`;
- manifest `reconciliation.attempted`;
- manifest `reconciliation.terminal`; and
- manifest `counts.evidence_rows`.

Any mismatch makes the analysis incomplete.

Four-field equality is necessary but not sufficient for acceptance. A full
scan passes only when that equality holds and there are no incomplete or
missing required cases. The signed manifest records these two effective legs
under `acceptance_gate`; `incomplete_false` and
`zero_unapproved_missing_required` expose the same fail-closed condition from
two operator-facing views. A URI-bearing failed row does not count as
successful evidence.

Approved skips are not supported by the async attempt-manifest path. The
`approved_skip_reasons` allowlist belongs to the separate full-suite payload
builder and does not make an async full-scan attempt bind-eligible. Any missing
required case fails this gate.

## VAPI production target

The controlled VAPI target is the existing assistant
`09aea361-04b3-4a62-bcb0-2f2582f11ec8` (`BackBond BEAAA-8459 Vapi`) at
`https://api.vapi.ai`. The production runner selects the native VAPI Chat
transport from that host and binds `scan_request.agent_id` as `assistantId`.
The VAPI private key remains in the normal in-memory credential-vault path and
is never written to evidence, manifests, or logs.

Every scanner case starts a fresh non-streaming chat with
`POST https://api.vapi.ai/chat` and `{assistantId, input, stream:false}`. Chats
are intentionally not chained with `previousChatId`: independent probes must
not share state. Every response must carry the exact requested `assistantId`
and at least one assistant output message, or the case fails closed as a runner
error. Redirects, compressed responses, oversized responses, and off-host
egress remain rejected by the shared customer-runner controls.

Fresh QA and deployment gates:

1. focused VAPI request-construction and fail-closed response tests pass;
2. existing generic-customer and Syllable runner regression tests pass;
3. the full unit/integration CI suite passes on the exact candidate commit;
4. the immutable image identifies that exact commit and passes runtime-closure checks;
5. production activation reports the exact candidate commit and image digest;
6. read-only VAPI inventory confirms the named assistant still exists;
7. one paid VAPI chat canary returns the exact assistant identity and usable output;
8. one judge canary succeeds through the configured OpenRouter route;
9. the full scan stays within 6,000 target calls, 4,000 judge calls, `$99.99`,
   and six hours;
10. the signed terminal manifest reconciles expected, attempted, terminal, and
    evidenced IDs with no silent omissions; and
11. the AIUC-1 direct-family subset contains exactly 64 unique complete PASS
    rows, 208 target calls, zero judge calls, report-only/zero-weight metadata,
    evidence references, and no score contribution.

Any identity, routing, budget, or reconciliation mismatch stops promotion and
triggers rollback to the prior immutable scanner image. Listener ingestion is a
separate later gate; a scan result alone does not enable listener processing.

## Legacy demo-agent evaluation

The former controlled target is the authenticated Syllable demo agent at `https://apichat.syllable.cloud`. Its two-step session transport remains supported for regression and rollback use, but it is not the VAPI production target defined above. The deployment host supplies judge credentials; no credential value is copied into the repository or evaluation artifact.

Before the full evaluation:

1. verify the API process is healthy on loopback;
2. run one target transport probe;
3. run one judge transport probe;
4. submit the full request with seed `42` and the 90-case registry snapshot;
5. poll until terminal;
6. verify `90 expected = 90 attempted = 90 terminal = 90 evidenced`;
7. verify target calls equal `110`, judge calls equal `90`, and estimated judge cost is at most `$99.99`;
8. retain the signed manifest and case evidence as the deployment acceptance artifact.

## Known boundary

This release makes the existing four bound families real. It does not pretend that framework mappings are executable. Each quarantined framework family must be activated in a later change by adding its target payload adapter, result parser, evidence schema, dependency pin, and controlled-agent validation. The registry gate will increase the published count automatically only after those requirements are satisfied.
