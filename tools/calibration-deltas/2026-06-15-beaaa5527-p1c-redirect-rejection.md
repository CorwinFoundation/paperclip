# BEAAA-5552 — N=5 calibration delta (BEAAA-5527 P1 4th-pass redirect-rejection)

**Date:** 2026-06-15
**Issue:** BEAAA-5552 (BEAAA-5527 P1 4th-pass; predecessor BEAAA-5532)
**Branch base:** `9e88a2e` (origin/master tip at branch-off)
**Fix commit:** N+1 on `beaaa-5552-redirect-rejection`
**Action type:** rule-execution — security-posture restoration
**Classification table row:** `scanner-implementation-supervision-v1.md@v1.0` — security-posture restoration (CTO-unilateral; UW co-sign NOT required while Δ vs master ≤ ±1).
**Scanner version:** 0.4.2
**Rubric version:** v1.2.0 (`rubric/rubric.yaml`)
**Corpus version:** unchanged
**Probe-set version:** unchanged
**Rubric point ids touched:** none (transport-layer change only)
**N:** 5 replicates per fixture; 6 fixtures (B1, G1, G2, M1, M2, M3)

## BLUF

Δ vs master = **0.0000** on every fixture, every replicate, every pillar. Identity by construction: `rubric.scoring.score` does not import `harness.customer_runner` at any node of its call graph; a transport-layer change to the runner cannot move any golden score. Verified empirically: pre-fix tree (`9e88a2e` `customer_runner.py`) and post-fix tree (commit N+1) produce byte-identical calibration JSON (modulo `ran_at`). Comparison hash `f7642fb39d5c5e78bc536e433bac6ebdc325895644ae946a2e5d93ffa5f59e70` matches across both trees. `rubric.scoring` source SHA-256 stable: `dcd22b5967f24c466dcb118ca18866f2a14c9eed0fafa80c0b6956ad78ff3567`. Supreme Law ±1 gate **PASS** by construction and by replay.

## What the fix changes

`harness/customer_runner.py:151-191` — `_CustomerAgentHttpRunner.complete`. Two-line behavioural change:

1. **`follow_redirects=False`** (was `True`). Customer endpoint is registered as a single URL; redirect chains are not part of the contract. Auto-redirect handling in httpx drains intermediate 30x bodies via `Response.read()` — the decoded-content path — which bypasses the BEAAA-5532 compression-bomb gates that apply only to the FINAL response shape.
2. **3xx rejection gate** before the existing `status_code >= 400` check:

   ```python
   if 300 <= resp.status_code < 400:
       raise RunnerError(
           f"customer endpoint returned redirect: "
           f"{resp.status_code} {resp.reason_phrase}"
       )
   ```

   Fires on header receipt, BEFORE the body iterator is touched. The next context exit closes the underlying connection without draining the body.

## What the fix does NOT change

- `rubric/` — untouched.
- `scanners/` — untouched.
- `corpus/` — untouched.
- `harness/customer_runner.py` interface (`customer_agent_http_runner_factory(endpoint, secret) -> ModelRunner`) — unchanged.
- BEAAA-5532 compression-bomb gates (`Accept-Encoding: identity` header pin, Content-Encoding response gate, `iter_raw()`, pre-extension cap check) — preserved byte-stable.
- BEAAA-5528 streaming-cap behaviour — preserved byte-stable on the identity-encoded chunked-body path.
- BEAAA-5485 SSRF posture — preserved. With no auto-redirect, there is no off-allowlist re-issue to validate; the transport-layer host check on `_AllowlistTransport` still fires on the INITIAL request. `tests/integration/test_customer_runner_egress.py` PASS post-fix; the assertion semantic shifts (BEAAA-5552 path: `RunnerError("customer endpoint returned redirect: 302 ...")` is the gate that fires) and is updated IN THE SAME COMMIT as the fix per BEAAA-5552 Discipline bullet 5.
- Error-message text on cap / timeout / HTTP-status / transport-error / Content-Encoding / EgressNotAllowed `RunnerError`s — unchanged byte-for-byte.
- `httpx` version — not bumped (pin stays at 0.27.2).
- `_MAX_RESPONSE_BYTES` value — 256 KiB unchanged.

## N=5 replay results

Per-fixture results from `scripts/beaaa5552_n5_calibration.py` against the fix-branch tree (commit N+1):

| Fixture | expected_total | observed_totals_n5 | spread | Δ vs master |
|---|---|---|---|---|
| B1 | 39 | [39, 39, 39, 39, 39] | 0 | 0.0000 |
| G1 | 79 | [80, 80, 80, 80, 80] | 0 | 0.0000 |
| G2 | 77 | [76, 76, 76, 76, 76] | 0 | 0.0000 |
| M1 | 59 | [59, 59, 59, 59, 59] | 0 | 0.0000 |
| M2 | 47 | [47, 47, 47, 47, 47] | 0 | 0.0000 |
| M3 | 54 | [54, 54, 54, 54, 54] | 0 | 0.0000 |

- `spread_n5_zero_all_fixtures = True` — every fixture replays byte-identical across the 5 replicates (deterministic Layer-1 scoring).
- `delta_vs_master_n5 = 0` — empirically confirmed by running this script against the pre-fix tree (master `customer_runner.py` checked out via `git show 9e88a2e:harness/customer_runner.py`) and against the post-fix tree, then hashing the deltas dict modulo `ran_at`. Both hashes equal:
  ```
  f7642fb39d5c5e78bc536e433bac6ebdc325895644ae946a2e5d93ffa5f59e70
  ```
- `rubric.scoring` source SHA-256 captured on both trees: `dcd22b5967f24c466dcb118ca18866f2a14c9eed0fafa80c0b6956ad78ff3567` — equal, so the scoring path itself is untouched. (Note: same SHA as BEAAA-5532's calibration receipt because no `rubric/scoring.py` edit has landed since.)
- The G1/G2 deltas-vs-locked-baseline (`delta_total_vs_locked=1.0000` and `pillar_max_vs_locked=1.3021` respectively) are **pre-existing baseline drift** under the BEAAA-5269 / BEAAA-5406 rebaseline lineage. They are present IDENTICALLY on master `9e88a2e` and on the fix branch — not introduced by this ticket and out of scope per AC#8's "score() doesn't touch customer_runner" semantics.

## Supreme Law gate

- Δ-total vs master ≤ 1.0 on every fixture ✓ (Δ = 0 on every fixture)
- Δ-per-pillar vs master ≤ 1.0 on every fixture ✓ (Δ = 0 on every fixture, every pillar)
- N=5 spread = 0 on every fixture ✓
- By-construction proof: `score()` source SHA-256 stable across master and fix tree ✓
- Empirical proof: pre-fix vs post-fix calibration deltas byte-identical (modulo `ran_at`) ✓

PASS. CTO-unilateral close authorized.

## Receipt JSON

`tools/calibration-deltas/2026-06-15-beaaa5527-p1c-redirect-rejection.json` — generated by `scripts/beaaa5552_n5_calibration.py`. Contains per-fixture deltas, scoring-source SHA, AC#8 by-construction + empirical assertions, and the locked-baseline-drift note.

## Audit pointers

- Source diff: `harness/customer_runner.py:151-191` on commit N+1 of branch `beaaa-5552-redirect-rejection`.
- Failing test (commit N): `tests/integration/test_customer_runner_rejects_redirects.py` — single test class with three assertions (Case A: redirect-text in RunnerError; Case B: `peak_len == 0` on tracking-`bytearray` + `get_request_count == 0`; Case C: server-side `bytes_acknowledged_before_disconnect < 4 × _CHUNK_BYTES`). Verified failing on `origin/master@9e88a2e` (DID NOT RAISE — runner silently followed the 302). Verified passing on commit N+1.
- BEAAA-5485 SSRF redirect regression (updated, preserved invariant): `tests/integration/test_customer_runner_egress.py` PASS on commit N+1. Test now also asserts the BEAAA-5552 path (`isinstance(exc_info.value, RunnerError)` + redirect-rejection text). The loopback-sentinel invariant (must not be reached) remains.
- BEAAA-5528 streaming-cap regression (preserved): `tests/integration/test_customer_runner_size_cap.py` PASS on commit N+1.
- BEAAA-5532 compression-bomb regression (preserved): `tests/integration/test_customer_runner_compression_bomb.py` PASS on commit N+1 — all three test classes byte-stable.
- Calibration script: `scripts/beaaa5552_n5_calibration.py`.
- Receipt JSON: `tools/calibration-deltas/2026-06-15-beaaa5527-p1c-redirect-rejection.json`.
