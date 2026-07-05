# ADR-0046: Probe Surface Rotation Wired Into the Live Walker (per-run `surface_seed` + WS1-B paraphrase plug)

- Status: Accepted (2026-07-05) — all three gates landed: Gate #1 reproducibility rotation-ON golden N=5 (seeds 0–4) delta = 0/0 across all pillars + total (CTO-re-verified); Gate #2 Underwriter cross-run band co-signed on BEAAA-11869; Gate #3 CTO ADR-0046 + merge-diff co-sign GRANTED on BEAAA-11863 (relay comment `0153205f`, read-the-diff verification). Implementation merged to canonical `origin/master` @ `1be55bf`.
- Date: 2026-07-05
- Deciders: CTO (rule-change decision — Option A on BEAAA-11859, co-signed on BEAAA-11863; ADR + merge-diff co-sign GRANTED gate #3, ref BEAAA-11863#0153205f), Underwriter (cross-run band co-sign GRANTED on BEAAA-11869), Scanner Engineer (implementation)
- Tickets: BEAAA-11859 (audit finding / decision gate), BEAAA-11863 (CTO ruling A, rule-change classification), BEAAA-11864 (this implementation), BEAAA-11735 (WS1-B paraphrase infra reused)
- Contract: `are-scanner-api-v1` — additive request field `surface_seed`; no scoring-math change, no pillar-weight change, **no canon §7 amendment** (Option A *meets* §7)
- Implementation branch: `beaaa-11864-surface-rotation` (cut from `origin/master` @ `13c0ca9`); merge pending co-signs

## Context

The BEAAA-11859 audit confirmed that surface rotation / paraphrase was **not** wired into the live probe path: the canary anti-gaming path was wired, but `paraphrase_probe()` had **zero non-test callers** and `customer_agent_walker.py:154` sent each probe's *canonical* prompt verbatim over the wire. Consequence (tamper-resistance defect): after a single scan, a vendor logging its own endpoint reconstructs ~100% of the static probe surface and can pre-train its agent to pass — the scan stops measuring latent behaviour and starts measuring memorization.

The CTO ruled **Option A** on the BEAAA-11863 decision gate: wire real per-run surface rotation, rather than Option B (canaries + result-sanitization), because B leaves the residual harvest exposure open exactly where harvest happens (the vendor's own request logs). Option A is a durable anti-harvest guarantee. The ruling is classified rule-change (tamper-resistance posture, ADR-tier) and is co-signed; it is **not** a canon §7 amendment — rotation *meets* §7's reproducibility law rather than relaxing it.

## Decision

Route every live probe's canonical prompt through the **WS1-B paraphrase bank** (`harness/probe_paraphrase.paraphrase_probe`, template bank `corpus/paraphrase/templates_v0_1.yaml`, `paraphrase-templates-v0.1`) at the walker boundary, keyed by a **per-run `surface_seed`** that is strictly separate from the canonical scoring `seed`.

1. **`ScanRequest.surface_seed: int | None`** (`scanners/live_probe_orchestrator.py`). Additive, optional, defaults `None`. Documented in-code and in `openapi.yaml` as STRICTLY SEPARATE from `seed` (the scoring RNG). The scoring RNG is never derived from it.

2. **Orchestrator stamp.** Immediately after runner construction, the orchestrator best-effort stamps `active_runner.surface_seed = scan_request.surface_seed`, mirroring the existing optional `set_scan_deadline` idiom. This carries the per-run seed to the Prober-side walker **without widening the fixed `ProbeRunFn(runner)` signature**.

3. **Walker plug** (`scanners/customer_agent_walker.py:154`). `_rotate_probe_surface(runner, spec.dim_id, spec.prompt)` calls `paraphrase_probe(probe_id=dim_id, scan_seed=surface_seed, canonical=prompt, family="generic")` and sends the returned `.text` over the wire. Selection is a pure function of `(probe_id, surface_seed, bank_version)` — deterministic, replayable, no live LLM at selection time.

4. **Edge seed policy** (`api/scan_handlers._resolve_surface_seed`). An explicit body `surface_seed` is honoured verbatim (exact replay). When absent **and** rotation is enabled (`ARE_PROBE_PARAPHRASE_RANDOMIZATION`), the server derives the seed from the **server-minted `scan_id`** (`sha256(scan_id)[:8]`): a vendor cannot pre-compute it (scan_id is server-assigned) yet replay is exact (scan_id is recorded in the forensic bundle). When rotation is disabled, `None` — canonical surface verbatim.

### Reproducibility invariant (why Option A meets §7)

Surface rotation lives entirely in the **Prober**, upstream of the recorded `eval_outputs` the **Scorer** consumes. The scoring math keys on the agent **response**, not the prompt phrasing. Therefore rotating the wire surface cannot move a score for a fixed agent behaviour. Two independent proofs:

- **Structural (gate #1):** the golden N=5 scores fixtures' pre-recorded `eval_outputs` directly, never invoking the walker. Flipping the paraphrase toggle ON leaves every delta `0` — `tools/calibration-deltas/2026-07-05-baseline-vs-rotationON.md` (rotation-ON median − paraphrase-OFF median, seeds 0-4, all six golden agents, `are_total` + all four pillars = 0; within ±1 band). This *is* the proof that `surface_seed` and the scoring RNG are isolated.
- **Behavioural:** `tests/test_walker_surface_rotation_beaaa11864.py` (AG-ROT-1..5) drives the real walker with a prompt-capturing runner and proves: OFF ⇒ byte-for-byte canonical wire prompts; ON ⇒ the wire surface rotates yet every rotated prompt embeds the canonical text verbatim; `raw` scores are identical OFF vs ON; same `surface_seed` ⇒ identical surface (replay); different `surface_seed` ⇒ can rotate differently.
- The `paraphrase-templates-v0.1` bank still passes the semantic-invariance gate (`scripts/release_gate_semantic_invariance.py` → PASS; receipt `tools/calibration-deltas/2026-07-04-beaaa11735-ws1b-v01-semantic-invariance.md`).

## Alternatives considered

- **Option B — canaries + result-sanitization (rejected by CTO).** Leaves the static probe surface fully harvestable from the vendor's own request logs; canaries detect *known* replays, not the underlying pre-training-to-pass exposure.
- **Widen `ProbeRunFn` to pass the seed as an argument (rejected).** Would touch every probe_run_fn caller and the IPC contract; the runner-stamp keeps the change local and the signature stable.
- **Client-chosen surface_seed only (rejected as default).** A vendor could pin a fixed seed and re-harvest; server derivation from the server-minted scan_id is the anti-harvest default, with explicit client seed reserved for replay.

## Consequences

- **Tamper resistance increases**: the wire probe surface rotates per run; a single-scan harvest no longer reconstructs the static surface. No tamper-resistance *reduction* anywhere (that would require Founder + CTO sign-off).
- **Scoring is unchanged and byte-stable** with rotation OFF (default) — Tier-1 rollback = toggle `ARE_PROBE_PARAPHRASE_RANDOMIZATION` off. The canonical scoring path is untouched.
- **Cross-run score band on non-golden customer agents** widens the per-run *surface* (not the score for fixed behaviour). Whether that band is priceable is an **Underwriter** question (gate #2) — routed for the binding cross-run band co-sign on the diff; not self re-banded.
- Additive `surface_seed` request field on `are-scanner-api-v1`; contract version disposition is the CTO's on the merge diff co-sign (gate #3).

## Verification

- `python3 scripts/release_gate_beaaa11864_rotation_on_n5.py` → `passed=True` (0/0 across B1/G1/G2/M1/M2/M3, all pillars + total, seeds 0-4).
- `python3 scripts/release_gate_semantic_invariance.py` → PASS.
- `python3 -m pytest tests/test_walker_surface_rotation_beaaa11864.py tests/integration/test_walker_customer_agent_v0_3.py tests/test_probe_paraphrase.py tests/test_canary_anti_gaming_beaaa11721.py -q` → green.
- Delta table (gate #1 evidence): `tools/calibration-deltas/2026-07-05-baseline-vs-rotationON.md`.

## Merge gates (all three required before merge)

1. **Reproducibility (CTO ADR co-sign, conditional):** clean ≤±1 rotation-ON N=5 vs OFF baseline — **satisfied** (0/0). CTO ADR co-sign executes on review of this table.
2. **Underwriter cross-run band co-sign** on the diff (pricing).
3. **CTO diff co-sign** on the merge (read the diff, not just the N=5 spread).
