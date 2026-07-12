# QA Report — Scan-to-Production Partner Demo

**Review source:** Founder review dated 2026-07-11  
**Remediation verified:** 2026-07-12  
**Branch:** `fix/demo-partner-readiness`  
**Original surface:** `hughes-combine-indiana-advisory.trycloudflare.com` (not redeployed in this pass)

## Outcome

All founder-review blockers and record requirements are addressed in the remediation branch. The implementation is locally verified but is **not yet cleared for circulation** until it is merged, deployed from the landed revision, and re-run on the demo host.

## Issues fixed

| Issue | Severity | Resolution | Evidence |
|---|---:|---|---|
| ISSUE-001 Certificate validity window | Blocker | Removed `valid_through`; certificate is an issued-at, point-in-time attestation. | `3228a7b`, `4a5c4b4` |
| ISSUE-002 Anonymous compute/disk exposure | Blocker | Public mode fails closed without a 32+ character token; browser login uses HttpOnly/SameSite/Secure cookie; API supports Bearer auth; per-client run quota added. | `60c8fcc`, `0941725` |
| ISSUE-003 Private CI metadata | Blocker | Removed repository, git ref, and Actions URL from public payloads; Actions run ID is plain text; health payload is minimal. | `dc86f9d`, `e2a4542`, `c5f2c59`, `384459c` |
| ISSUE-004 Rubric rollout mismatch | High | Rubric 1.3.0 is marked `candidate` in scan cards, signed certificate payload, and full certificate HTML. | `e635106`, `d747cf5` |
| ISSUE-005 Misleading pre-run UI state | Medium | Badges now start at zero/waiting and become signed/ready only after actual results; revision no longer reads a removed field. | `e39e4ca`, `cffa2a6` |
| Missing ticket and owner | Governance | Filed CTO-owned Paperclip ticket BEAAA-13613 with PR-deployment rationale and merge/deploy gates. | `BEAAA-13613` |

## Automated verification

- `111 passed in 7.98s` for `tools/post-bind-ingest/tests`, excluding only `test_collector_receiver_e2e.py` because the Go executable is not installed on this Windows runner.
- The omitted Go test was attempted and failed before execution with `FileNotFoundError: go`; it did not expose a product failure.
- Authenticated deployed-contract smoke run: `PASS`.
  - Initial score: 80
  - Updated score: 81
  - Lifecycle events: 44
  - Production-shaped logs: 9
  - Passive findings: 7
  - Elapsed: 4504 ms

## Browser verification

- Anonymous root returned the token login page.
- Valid token produced an HttpOnly, SameSite=Strict, Secure session cookie.
- Pre-run labels showed zero/waiting and a valid revision string.
- Full browser-triggered lifecycle completed with PASS.
- CI/CD panel showed release 1.1.0, score 81, threshold 70, exit code 0, and 52 passing focused tests; no private CI link was present.
- Certificate showed `Issued at`, point-in-time attestation, valid Ed25519 signature, and rubric `1.3.0 (candidate)`; no validity window was present.
- Production view contained exactly 9 synthetic logs.
- Passive view contained 7 findings and 7 Log Tracker rows.
- Final report showed release PASSED, evidence HEALTHY, and certificate signature valid.
- 390×844 responsive check: no horizontal overflow.
- Browser console: no warnings or errors.

## Deployment gate

Before external circulation:

1. Review and land `fix/demo-partner-readiness` onto canonical master through the normal PR path.
2. On the demo host, create `/etc/backbond/passive-demo.env` with `DEMO_ACCESS_TOKEN` of at least 32 characters; optionally set quota values.
3. Deploy from the landed revision, not `pull/61/merge`.
4. Run one fresh authenticated smoke/browser pass and attach the deployed SHA and evidence to BEAAA-13613.
5. Founder/CEO makes the final circulation decision.