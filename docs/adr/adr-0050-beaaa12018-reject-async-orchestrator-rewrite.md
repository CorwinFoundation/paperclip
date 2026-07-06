---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Reject the third-party code-review recommendation to rewrite scanners/live_probe_orchestrator.py from synchronous to full asyncio. The orchestrator's synchronous, sequentially-ordered replicate loop and monkeypatchable monotonic wall-clock cap are load-bearing for the BackBond Scanner's reproducibility guarantee (golden Δ0; ±1 on total and every subscore). Event-loop concurrency would trade a nondeterminism surface for no measurable Phase-1 throughput gain, so the observation is acknowledged but the fix is declined."
contracts_referenced: ["are-scanner-api-v1@v1.10.0 (no change)", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-12018", "BEAAA-12017"]
proof_points_cited: ["scanners/live_probe_orchestrator.py:189-204 (MAX_WALL_CLOCK_SECONDS + _now monotonic indirection)", "scanners/live_probe_orchestrator.py:524 (sequential k_replicates loop)", "scanners/live_probe_orchestrator.py:247-255 (seed vs surface_seed separation)", "tests/integration/test_invocation_context_e2e.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No Underwriter co-sign and no Founder gate required: this ADR changes NO code, NO contract, NO rubric/scoring math, NO pillar weights (25/25/25/25), NO bands, NO scored:false surface. It records a decision to preserve the status quo. Golden Δ0 by construction (zero diff to any scoring-path file)."]
---

# ADR-0050 — Reject the async rewrite of `live_probe_orchestrator.py`

**Status:** ACCEPTED (decision: REJECT the recommendation)
**Owner:** CTO
**Ticket:** BEAAA-12018 (action item #2 from the third-party `are-scanner` code-review triaged on BEAAA-12017)

## Context

A board-attached third-party code-review of `are-scanner` observed — correctly —
that `scanners/live_probe_orchestrator.py` is fully synchronous: blocking `for`
loops, a custom `time.monotonic()` wall-clock cap, and zero `async`/`await`. It
recommended a rewrite to full `asyncio` concurrency.

The observation is accurate. The CEO triage (BEAAA-12018) flagged the *fix* as
RISKY and explicitly instructed: assess the trade-off against our
determinism/reproducibility guarantee before any change; if async risks
nondeterministic scan outputs, reject with a written rationale rather than
auto-adopt. This ADR is that rationale.

## What the synchronous design buys us (verified against `@master`)

Three properties of the current orchestrator are load-bearing for the Scanner's
Supreme Law — same agent + same scanner version ⇒ same BackBond Score within ±1
on the total **and** ±1 on every subscore, enforced as golden Δ0 in CI:

1. **Deterministic replicate ordering.** `run_scan` folds K replicates through a
   single sequential loop (`for _replicate_idx in range(scan_request.k_replicates)`,
   line ~524). The `run_pass_k` / pass^k statistics and per-pillar spread that
   feed admittance (ADR-0041 variance band) are computed over replicates in a
   fixed order. Sequential execution makes that order an invariant of the code,
   not of the scheduler.

2. **Monkeypatchable, deterministic wall-clock cap.** `MAX_WALL_CLOCK_SECONDS`
   (1800s, Phase-1 sizing) is measured from the first `_now()` call, and `_now()`
   is a module-level indirection over `time.monotonic()` specifically so tests
   advance time deterministically without pausing the runner
   (lines 189-204). The cap is exercised in unit tests by monkeypatching `_now`.

3. **Strict seed separation.** The canonical scoring `seed` and the
   probe-surface-rotation `surface_seed` are deliberately never mixed (ADR-0046,
   lines 247-255). Determinism of the scoring RNG is a hard boundary.

## Why full asyncio is the wrong lever here

- **It converts a code invariant into a scheduler behavior.** Under an event
  loop, replicate tasks interleave at `await` points. Even with careful `gather`,
  the *fold* into pass^k statistics would need explicit re-sorting by
  `replicate_idx` to stay stable — i.e. we would add async machinery and then
  spend effort defeating its only benefit (concurrency) to preserve ordering.
  Net: more nondeterminism surface, zero ordering gain.

- **It reintroduces wall-clock coupling into tests.** Event-loop timers do not
  compose with the `_now` monkeypatch pattern; the deterministic cap test would
  have to be rewritten around loop time, weakening a reproducibility guard.

- **The orchestrator is not the latency bottleneck.** The two genuinely
  I/O-bound stages are already isolated in separate processes: the Prober
  (`_prober_target`, customer-agent probing over the network) and the Scorer
  subprocess (`spawn_scorer`). The orchestrator loop is coordination and folding,
  not network wait. Async in the fold path buys no wall-clock at Phase-1 sizing
  (MAX_TOKENS_PER_SCAN = 1e6, 30-min cap, design-partner K).

- **This is adversarial-input-handling code.** Per our technical principles the
  orchestrator must be treated as adversarial by default. Adding task-scheduling
  nondeterminism to code whose outputs price real risk raises both
  reproducibility and tamper-analysis cost for no offsetting benefit.

## Decision

**Reject** the full-asyncio rewrite of `live_probe_orchestrator.py`. Keep the
synchronous, sequentially-ordered design.

## Accepted alternative (if latency ever becomes a real constraint)

Do **not** reach for event-loop async in the scoring-fold path. The safe lever
is bounded, deterministic parallelism at the **subprocess / replicate boundary**
— dispatch replicates concurrently, then re-materialize a fixed order
(sort by `replicate_idx`) **before** any pass^k fold — gated behind:

1. A measured latency SLO breach that justifies the change (not speculative).
2. A fresh ADR + Underwriter visibility (any change near the scoring fold).
3. Golden N=5 Δ0 proof across the frozen calibration roster on total and all
   four pillars, per the standing reproducibility procedure.

Until all three hold, the orchestrator stays synchronous.

## Consequences

- No code, contract, rubric, weight, band, or `scored:false` change. Golden Δ0 by
  construction. No UW co-sign / no Founder gate (status-quo-preserving decision).
- The third-party observation is closed as *acknowledged, fix declined with
  rationale* — not silently dropped.
- Future performance work on the live-scan path has a documented, reproducibility-
  safe pattern to follow instead of the rejected one.
