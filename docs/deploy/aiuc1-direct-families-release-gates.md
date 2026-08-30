# AIUC-1 direct-family integration and release gates

This release brings the six approved candidate commits onto the current
`origin/master` baseline and closes the missing production binding for:

- `authentication_bypass_resistance@0.1.0`: 32 cases, 128 target calls;
- `sensitive_data_disclosure_resistance@0.1.0`: 32 cases, 80 target calls.

Both families are synthetic, report-only, zero-weight, and judge-free. They
produce categorical evidence and must not change a score, band, eligibility,
pricing result, or the published 2,339 executed-test claim before separate
target-derived evidence promotion.

## Why the families were not deployed

The candidate contained the corpora, graders, mapping files, and a 14-row
observation adapter. It was delivered as an independently QA-approved capsule,
not as a merge or release. No downstream step moved the six commits to master.
More importantly, the candidate's default production registry still produced
zero authentication cases and a non-executable disclosure placeholder. Its
test invoked a manually supplied worker and therefore did not prove the
source-built production registry or registered-case executor could run the
64-case corpus.

The integration branch adds a deterministic direct-family materializer, binds
both families to the normal registered-case executor, preserves the existing
report-only boundary, and adds release/image/canary checks.

## Gate 1: source and focused QA

Required before push:

```text
python scripts/verify_aiuc1_direct_families_release.py
python -m pytest -v -ra \
  tests/test_authentication_bypass_resistance_beaaa26755.py \
  tests/test_beaaa26756_sensitive_data_disclosure.py \
  tests/test_day1_report_only_beaaa26757.py \
  tests/test_aiuc1_direct_families_production_wiring.py
```

The release verifier must report:

- 32 rows per family and 64 total;
- 208 planned target calls: 128 authentication and 80 disclosure;
- every row active, target-bound, and production-dispatchable;
- `execution_evidenced=false` until a real scan runs;
- zero scoring and the 2,339 published executed-test count unchanged;
- the shared registered-executor tamper pin valid.

## Gate 2: pull request and registry QA

The PR may merge only when all required checks are green, including:

- `AIUC-1 direct families / production binding and report-only invariants`;
- the full `tests / python pytest` job;
- `authoritative-probe-registry / drift`;
- any repository-required security/review checks.

Do not bypass CI or merge with a stale base. If master moves, update the branch,
rerun the source verifier, and re-run all required checks.

## Gate 3: immutable image

Run the manual `Scanner Immutable GHCR Image` workflow against the exact merged
master SHA. The image job must run
`scripts/verify_aiuc1_direct_families_release.py` inside the built image before
push. Record the image digest and retain the currently running image ID and OCI
revision as the rollback target.

The embedded release identity must match the merged SHA. A source checkout pass
cannot substitute for the in-image pass.

## Gate 4: controlled canary

Deploy first to the controlled canary target and run the normal full-scan path.
Export its final attempt manifest, then run:

```text
python scripts/verify_aiuc1_direct_families_canary.py \
  --manifest CANARY_MANIFEST.json \
  --require-all-pass
```

The gate requires exactly 64 unique direct-family rows, all complete, all PASS
for the controlled known-good target, 208 attributable target calls, zero judge
calls, evidence references for every row, no numeric result, and
`disposition=report-only` with `score_contribution=false`.

A customer-target FAIL is useful evidence and is not automatically a deployment
failure. The all-PASS requirement applies to the controlled known-good canary
used to validate routing and adapter integrity. Any missing, incomplete,
invalid, duplicated, unevidenced, scored, or wrongly replicated canary row
blocks promotion.

## Gate 5: production promotion and verification

Promote the same immutable digest. Then verify:

1. the public release-identity endpoint reports the merged SHA and expected
   scanner/corpus versions;
2. the running container uses the recorded immutable digest;
3. a controlled production full scan passes the same canary verifier;
4. evidence remains report-only and no score/band/pillar changes occur;
5. the prior production image and configuration remain immediately available.

Rollback on any identity, registry, routing, evidence, score-boundary, or
reconciliation failure. Rollback restores the retained image/configuration and
repeats release-identity and health verification.

## Listener follow-on gate

Listener ingestion is not activated by this scanner release. The compatibility
seam in `api/day1_report_only.py` accepts already-authorized observations, but
it must not receive raw or unsigned listener data.

Before activation, add and pass all of the following:

- signed `post-bind-egress-wire` verification and schema-version checks;
- explicit projection from normalized tool/action and disclosure events into
  the Day-1 boolean observation contract;
- replay and idempotency tests;
- tenant/deployment isolation and authorization checks;
- redaction/minimized-egress tests proving no credential, real PII, or raw
  transcript leaves the customer boundary;
- no-score-import and zero-score-contribution tests;
- source/emitted/quarantined reconciliation with durable evidence references.

Only then may the listener reuse the report-only worker. Listener findings stay
separate from scan results and from coverage or claim decisions.
