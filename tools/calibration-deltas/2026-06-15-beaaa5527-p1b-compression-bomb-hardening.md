# BEAAA-5532 — N=5 calibration delta (BEAAA-5527 P1 3rd-pass compression-bomb hardening)

**Date:** 2026-06-15
**Issue:** BEAAA-5532 (BEAAA-5527 P1 3rd-pass; predecessor BEAAA-5528)
**Branch base:** `d372283` (origin/master tip at branch-off)
**Fix commit:** N+1 on `beaaa-5532-compression-bomb-cap`
**Action type:** rule-execution — security-posture restoration
**Classification table row:** `scanner-implementation-supervision-v1.md@v1.0` — security-posture restoration (CTO-unilateral; UW co-sign NOT required while Δ vs master ≤ ±1).
**Scanner version:** 0.4.2
**Rubric version:** v1.2.0 (`rubric/rubric.yaml`)
**Corpus version:** unchanged
**Probe-set version:** unchanged
**Rubric point ids touched:** none (transport-layer change only)
**N:** 5 replicates per fixture; 6 fixtures (B1, G1, G2, M1, M2, M3)

## BLUF

Δ vs master = **0.0000** on every fixture, every replicate, every pillar. Identity by construction: `rubric.scoring.score` does not import `harness.customer_runner` at any node of its call graph; the four-layer compression-bomb fix to the runner cannot move any golden score. Verified empirically: pre-fix tree (`d372283` customer_runner.py) and post-fix tree (commit N+1) produce byte-identical calibration JSON (modulo `ran_at`) — comparison hash `e36758d368d056b66fc55b2dc9ab3a46c5e1ba85de7109821425212f319c853a` matches across both trees. Supreme Law ±1 gate **PASS** by construction and by replay.

## What the fix changes

`harness/customer_runner.py:114-235` — `_CustomerAgentHttpRunner.complete`. Four layered defences on the response path:

1. **Request header pin.** Headers dict now includes `Accept-Encoding: identity` so a well-behaved customer endpoint is invited to return uncompressed bytes. Without this header, httpx defaults to `gzip, deflate, br, zstd`.
2. **Content-Encoding response gate.** Before iterating the body, the runner inspects `resp.headers.get("Content-Encoding", "").strip().lower()`. Allowed: empty / `identity`. Any other value raises `RunnerError("customer endpoint returned unsupported Content-Encoding: <value>")` — defence-in-depth against a customer endpoint that ignores `Accept-Encoding: identity`. The bomb is never decoded.
3. **`iter_raw()` instead of `iter_bytes()`.** `iter_bytes` is httpx's decoded-content iterator — a small gzip / zstd wire payload (e.g. 100 KB) inflates to multi-GB before the cap check fires on the inflated bytes. `iter_raw` yields raw on-the-wire bytes and bypasses the content-decoding path even if upstream defaults change.
4. **Pre-extension cap check.** Body loop now: `if len(buf) + len(chunk) > _MAX_RESPONSE_BYTES: truncated=True; break` BEFORE `buf.extend(chunk)`. Peak buffer length is bounded by `_MAX_RESPONSE_BYTES` at all times. The BEAAA-5528 post-extension check allowed peak to reach `cap + len(chunk)`; under adversarial chunk sizing that overshoot was unbounded.

Cap-error message text is byte-stable (`"customer endpoint response exceeded 262144 bytes"`) so downstream walker error-matching is unchanged.

## What the fix does NOT change

- `rubric/` — untouched.
- `scanners/` — untouched.
- `corpus/` — untouched.
- `harness/customer_runner.py` interface (`customer_agent_http_runner_factory(endpoint, secret) -> ModelRunner`) — unchanged.
- Error-message text on cap / timeout / HTTP-status / transport-error `RunnerError`s — unchanged byte-for-byte from BEAAA-5528.
- `ScopedEgressAllowlist` + `follow_redirects=True` + `EgressNotAllowed` branch — preserved. `client.stream` routes redirects through the same `_AllowlistTransport` so the BEAAA-5485 SSRF guard remains intact (`tests/integration/test_customer_runner_egress.py` PASS post-fix).
- BEAAA-5528 streaming behaviour — preserved byte-stable on the identity-encoded chunked-body path (`tests/integration/test_customer_runner_size_cap.py` PASS post-fix; `iter_raw` and `iter_bytes` are indistinguishable on identity-encoded responses).
- `httpx` version — not bumped.
- `_MAX_RESPONSE_BYTES` value — 256 KiB unchanged.

## N=5 replay results

Per-fixture results from `scripts/beaaa5532_n5_calibration.py` against the fix-branch tree (commit N+1):

| Fixture | expected_total | observed_totals_n5 | spread | Δ vs master |
|---|---|---|---|---|
| B1 | 39 | [39, 39, 39, 39, 39] | 0 | 0.0000 |
| G1 | 79 | [80, 80, 80, 80, 80] | 0 | 0.0000 |
| G2 | 77 | [76, 76, 76, 76, 76] | 0 | 0.0000 |
| M1 | 59 | [59, 59, 59, 59, 59] | 0 | 0.0000 |
| M2 | 47 | [47, 47, 47, 47, 47] | 0 | 0.0000 |
| M3 | 54 | [54, 54, 54, 54, 54] | 0 | 0.0000 |

- `spread_n5_zero_all_fixtures = True` — every fixture replays byte-identical across the 5 replicates (deterministic Layer-1 scoring).
- `delta_vs_master_n5 = 0` — empirically confirmed by running this script against the pre-fix tree (master `customer_runner.py` checked out via `git show d372283:harness/customer_runner.py`) and against the post-fix tree, then hashing the deltas dict modulo `ran_at`. Both hashes equal:
  ```
  e36758d368d056b66fc55b2dc9ab3a46c5e1ba85de7109821425212f319c853a
  ```
- `rubric.scoring` source SHA-256 captured on both trees: `dcd22b5967f24c466dcb118ca18866f2a14c9eed0fafa80c0b6956ad78ff3567` — equal, so the scoring path itself is untouched.
- The G1/G2 deltas-vs-locked-baseline (`delta_total_vs_locked=1.0000` and `pillar_max_vs_locked=1.3021` respectively) are **pre-existing baseline drift** under the BEAAA-5269 / BEAAA-5406 rebaseline lineage. They are present IDENTICALLY on master `d372283` and on the fix branch — not introduced by this ticket and out of scope per AC#8's "score() doesn't touch customer_runner" semantics.

## Supreme Law gate

- Δ-total vs master ≤ 1.0 on every fixture ✓
- Δ-per-pillar vs master ≤ 1.0 on every fixture ✓
- N=5 spread = 0 on every fixture ✓
- By-construction proof: `score()` source SHA-256 stable across master and fix tree ✓
- Empirical proof: pre-fix vs post-fix calibration deltas byte-identical (modulo `ran_at`) ✓

PASS. CTO-unilateral close authorized.

## Receipt JSON

`tools/calibration-deltas/2026-06-15-beaaa5527-p1b-compression-bomb-hardening.json` — generated by `scripts/beaaa5532_n5_calibration.py`. Contains per-fixture deltas, scoring-source SHA, AC#8 by-construction + empirical assertions, and the locked-baseline-drift note.

## Audit pointers

- Source diff: `harness/customer_runner.py:114-235` on commit N+1 of branch `beaaa-5532-compression-bomb-cap`.
- Failing tests (commit N): `tests/integration/test_customer_runner_compression_bomb.py` — all three test classes verified failing on `origin/master@d372283` with diagnostic assertions identifying the gzip cap-not-encoding-error mismatch, the `Accept-Encoding: gzip, deflate, br, zstd` httpx default leak, and the 263168-byte (cap + 1024) post-extension overrun. Verified passing on commit N+1 with the four-layer hardening.
- BEAAA-5528 streaming-cap regression (preserved): `tests/integration/test_customer_runner_size_cap.py` PASS on commit N+1.
- BEAAA-5485 SSRF redirect regression (preserved): `tests/integration/test_customer_runner_egress.py` PASS on commit N+1.
- Calibration script: `scripts/beaaa5532_n5_calibration.py`.
- Receipt JSON: `tools/calibration-deltas/2026-06-15-beaaa5527-p1b-compression-bomb-hardening.json`.
