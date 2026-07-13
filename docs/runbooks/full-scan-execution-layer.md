# Full-scan execution layer

This runbook defines what the scanner actually executes when a caller submits a `full` scan. It supersedes any use of the historic ?1,180 tests? claim as an executed-test count.

## Current production inventory

The production registry contains 280 catalog rows. Only 90 rows are active and executable because only those rows have all of the following:

- a concrete target-agent payload;
- a bound transport adapter;
- a deterministic result/judge adapter;
- a pinned source pack and runner version; and
- per-case evidence output.

The 90 executed cases are:

| Family | Cases | Target calls | Judge calls |
| --- | ---: | ---: | ---: |
| Factuality golden set | 30 | 30 | 30 |
| Consistency under paraphrase | 20 | 40 | 20 |
| Refusal accuracy | 20 | 20 | 20 |
| Confidence calibration | 20 | 20 | 20 |
| **Total** | **90** | **110** | **90** |

The other 190 catalog rows include Garak, AgentDojo, PyRIT, Promptfoo, tool-misuse, PII, governance, fixtures, and attestation assets. They remain visible as `quarantined`, but do not count as tests until a production adapter can invoke them against the submitted agent and emit evidence.

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

- maximum target calls: `1,000`;
- maximum judge calls: `500`;
- maximum estimated judge cost: `$100.00`;
- maximum wall clock: `6 hours`;
- maximum reserved judge output: `256 tokens` per call.

The current 90-case run plans 110 target calls and 90 judge calls. Judge cost is conservatively reserved before each call. If any ceiling would be crossed, the affected case is written as `incomplete` with `budget_exceeded:<limit>`, and no over-budget call is sent.

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

## Demo-agent evaluation

The initial controlled target is the existing authenticated Syllable demo agent at `https://apichat.syllable.cloud`. Its two-step session transport is selected automatically by the customer-agent runner. The deployment host supplies judge credentials; no credential value is copied into the repository or evaluation artifact.

Before the full evaluation:

1. verify the API process is healthy on loopback;
2. run one target transport probe;
3. run one judge transport probe;
4. submit the full request with seed `42` and the 90-case registry snapshot;
5. poll until terminal;
6. verify `90 expected = 90 attempted = 90 terminal = 90 evidenced`;
7. verify target calls equal `110`, judge calls equal `90`, and estimated judge cost is at most `$100`;
8. retain the signed manifest and case evidence as the deployment acceptance artifact.

## Known boundary

This release makes the existing four bound families real. It does not pretend that framework mappings are executable. Each quarantined framework family must be activated in a later change by adding its target payload adapter, result parser, evidence schema, dependency pin, and controlled-agent validation. The registry gate will increase the published count automatically only after those requirements are satisfied.
