---
artifact_type: adr
owner: scanner-engineer
co_gate: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Extend bias-pack (BBQ n=200 + DoNotAnswer n=100) to Path-B deployed-agent fixtures M1 (HuggingChat/Llama-3.3-70B via OpenRouter) and M2 (Botpress demo) as v0.3 L2 live re-baseline. B1, M3, G2 deferred to BEAAA-793 (v0.3.1). Path-B adapter contract pinned: ModelRunner abstract interface is the sole integration surface; run_bias_pack() requires no pipeline changes."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1.md@v1.1"]
ticket_refs: ["BEAAA-759", "BEAAA-793", "BEAAA-758", "BEAAA-749"]
proof_points_cited: [
  "harness/pipeline.py::run_bias_pack — already polymorphic via ModelRunner abstract interface",
  "harness/runner.py::OpenRouterRunner (M1 via _build_huggingchat) + BotpressChatRunner (M2)",
  "scripts/beaaa759_l2_bias_pack_run.py — v0.3 L2 runner (BEAAA-759 commit on feature/beaaa-759-bias-pack-v0.3-v1-path-b)",
  "tools/calibration-deltas/2026-06-23-beaaa759-bias-pack-v0.3-rebaseline.md — M1 58 / M2 48, N=5 spread 0.0, discrimination spread 31",
  "BEAAA-759 CTO calibration sign-off cmt f3f4456e and HoUW post-run ratification cmt 35b9f923",
  "tests/fixtures/golden_agents/M1/agent.json — vendor: huggingface_via_openrouter, expected_bmi: 58",
  "tests/fixtures/golden_agents/M2/agent.json — vendor: botpress, expected_bmi: 48"
]
version: 0.3
prior_version: "adr-0013-bias-pack-v0.1.md (v0.1 pack design + golden calibration)"
self_audit_passed: true
status: ACCEPTED — N=5 live run passed; M1 HoUW post-run ratification cleared; canon merge delegated to BEAAA-7766
escalations_noted: [
  "co-sign gate: CTO + HoUW per scanner-implementation-supervision-v1.md@v1.1 (L2 live re-baseline is rule-execution-class). HoUW co-sign ref: 57d8268d (accepted 2026-05-26 12:02Z). CTO co-sign ref: b54c1143 (2026-05-27). Board approval ref: dc4c92a8 (approved).",
  "post-run HoUW A2 routing cleared: M1 BIAS_FAIRNESS -1.5625 ratified in BEAAA-7762 cmt 35b9f923; no Founder escalation because no fixture |delta| > 3pt.",
  "B1 (Pi.ai/Inflection) deferred to BEAAA-793: no InflectionRunner in harness/runner.py; net-new runner = vendor-SDK-class work scoped to v0.3.1.",
  "M3 (Mistral) + G2 (OpenAI gpt-4o) deferred to BEAAA-793: vendor-billing-class unresolved per CTO Q4 = defer per option B (5/31 deadline fit).",
  "v0.3.1 child BEAAA-793 filed 2026-05-27 (id: 74cad770), status: todo, blockedByIssueIds: [BEAAA-759]. CTO C11: must pre-merge."
]
---

# ADR-bias-pack-v0.3 — Path-B live re-baseline for M1 + M2

**STATUS: ACCEPTED — §4 calibration delta populated from the 2026-06-23 live N=5 run; M1 HoUW ratification cleared in BEAAA-7762 cmt `35b9f923`.**

| field | value |
|---|---|
| issue | [BEAAA-759](/BEAAA/issues/BEAAA-759) |
| owner | Scanner Engineer (`d8bbc3b5`) |
| co-gate | CTO (`88e68019`) + HoUW (upfront `57d8268d` accepted) — rule-execution per `scanner-implementation-supervision-v1.md@v1.1` |
| Founder visibility | No — no pillar weight change, no rubric point added/removed; B1 InflectionRunner deferred (would trigger if net-new vendor SDK added) |
| status | accepted 2026-06-23; pending only canon-master merge/reachability on [BEAAA-7766](/BEAAA/issues/BEAAA-7766) |
| supersedes | Partial — replaces M1 + M2 `uncovered_dimensions` proxy status in v0.2 standing table (`2026-05-26-beaaa758-bias-pack-v0.2-rebaseline.md`) |
| deferred scope | B1 (Pi.ai), M3 (Mistral), G2 (OpenAI gpt-4o) → [BEAAA-793](/BEAAA/issues/BEAAA-793) v0.3.1 |

## 1. BLUF — Decision

**Extend bias-pack measurement (BBQ n=200 + DoNotAnswer n=100) to Path-B fixtures M1 (HuggingChat/Llama-3.3-70B-Instruct via OpenRouter) and M2 (Botpress public chat demo) via L2 live re-baseline.** Both fixtures have live external endpoints and are measured directly via the `ModelRunner` abstract interface — no pipeline changes to `harness/pipeline.py::run_bias_pack` are required. The extension is entirely L2 runner selection in `scripts/beaaa759_l2_bias_pack_run.py`.

Co-sign classification: **rule-execution** (L2 live re-baseline moves ARE scores on deployed fixtures). CTO co-sign `b54c1143` + HoUW upfront accept `57d8268d` + board approval `dc4c92a8` all cleared 2026-05-27. Post-run HoUW A2 routing per delta magnitude.

**Path-B adapter contract (pinned for BEAAA-793 v0.3.1 re-use):**

| contract point | value |
|---|---|
| integration surface | `ModelRunner` abstract interface: `model_id() -> str` + `complete(prompt: str) -> str` |
| pipeline entry | `run_bias_pack(runner: ModelRunner, *, judge_runner, requested_pillars, bbq_corpus_path, donotanswer_corpus_path, ensemble_n, seed)` — no signature change needed |
| M1 runner | `OpenRouterRunner` / `HTTPEndpointRunner` via `_build_huggingchat()` in `scripts/path_b_endpoints.py` |
| M2 runner | `BotpressChatRunner` via `_build_botpress()` in `scripts/path_b_endpoints.py` |
| B1 runner | NOT YET IMPLEMENTED — requires `InflectionRunner` class (deferred BEAAA-793) |
| runner builder | `_build_path_b_subject_runner(fixture_id)` in `scripts/beaaa759_l2_bias_pack_run.py` |
| archive invariant | archive-before-overwrite: `.bias-pack-v0.1-prior.json` sibling for `eval_outputs.json`, `baseline.json`, `baseline_metadata.json` |
| provenance key | `_bias_pack_v0_3_rebaseline` |
| calibration threshold | ±1 ARE total + ±1 per pillar (CTO C9 per `are-scanner-api-v1.md@v1.0`) |
| discrimination scope | `["G1", "G3", "B3", "M1", "M2"]` — v0.3 measured set post-run |

## 2. Reasoning

### 2.1 Why M1 + M2 before B1 / M3 / G2

[BEAAA-758](/BEAAA/issues/BEAAA-758) v0.2 bias-pack shipped B3, G1, G3 (Path-A deterministic replay) and explicitly deferred M1, M2 (Path-B endpoints, no runner-at-the-time coverage) plus B1, M3, G2 (vendor-billing-class). v0.3 closes the M1 + M2 gap under the 2026-05-31 Phase 1 deadline imposed by CEO `b828d919` 2026-05-27.

Runner availability at the time of v0.3 intake:

| fixture | vendor | runner class in harness/runner.py | verdict |
|---|---|---|---|
| M1 | HuggingChat/Llama via OpenRouter | `OpenRouterRunner` / `HTTPEndpointRunner` | ✅ available |
| M2 | Botpress demo | `BotpressChatRunner` | ✅ available |
| B1 | Pi.ai (Inflection) | none — `InflectionRunner` not implemented | ❌ deferred BEAAA-793 |
| M3 | Mistral | `OpenRouterRunner` (possible) | ❌ deferred — vendor-billing-class unresolved per CTO Q4 |
| G2 | OpenAI gpt-4o | `OpenRouterRunner` (possible) | ❌ deferred — vendor-billing-class unresolved per CTO Q4 |

CTO Option B co-sign `b54c1143`: M1+M2 by 5/31, B1+M3+G2 → v0.3.1 with InflectionRunner + Q4-resolved billing class.

### 2.2 Why no pipeline changes required

`harness/pipeline.py::run_bias_pack` signature:

```python
def run_bias_pack(
    runner: ModelRunner,
    *,
    judge_runner: ModelRunner,
    requested_pillars,
    bbq_corpus_path,
    donotanswer_corpus_path,
    ensemble_n,
    seed,
) -> tuple[list, dict]:
```

The `runner` parameter is the `ModelRunner` abstract interface — not a concrete class. `BotpressChatRunner` and `OpenRouterRunner` both implement `ModelRunner`. Path-B extension is therefore entirely at the L2 script layer: select the correct runner per fixture, pass it to `run_bias_pack`. No pipeline modification = no additional pipeline-level co-sign tier.

This was discovered mid-intake (CTO Q2=(i) "extend `run_bias_pack`" initially suggested pipeline work; L2 inspection confirmed polymorphism already present). CTO informed in cmt `b54c1143` via option B approval.

### 2.3 Calibration threshold correction

v0.3 intake originally cited ±0.5 ARE total as the calibration threshold. CTO C9 in co-sign `b54c1143` corrects to **±1 ARE total + ±1 per pillar** per `are-scanner-api-v1.md@v1.0` §C9. This ADR adopts the corrected threshold. Prior v0.2 standing table (`2026-05-26-beaaa758-bias-pack-v0.2-rebaseline.md`) was authored under the same ±1 total + ±1 per pillar standard.

### 2.4 Judge pin (inherited from ADR-0013)

| field | value |
|---|---|
| model_id | `claude-sonnet-4-6` |
| temperature | 0 |
| seed | per-run seed (0..4 for N=5; corpus seed=42 fixed) |
| ensemble_n | 3 |

Same pin as v0.1 (ADR-0013 §2.5) and v0.2 (BEAAA-758). Judge-pin invariant per Supreme Law.

## 3. Rejected alternatives

### A. Replay M1 + M2 from 2026-05-08 source artifacts instead of live re-scan

ADR-0013 §3 considered this. For v0.3 specifically: CTO Q1=(a) live re-scan was the explicit co-sign decision. Rationale: M1 (HuggingChat/Llama-3.3-70B via OpenRouter) and M2 (Botpress demo) are live endpoints with observable behavioral drift; a 2026-05-08 replay artifact would anchor bias scores to a 19-day-old model state. Live re-scan gives current measurement aligned with the v0.3 ship date. Cost delta (Q1=(a) vs replay) is BBQ+DNA judge spend only — not corpus re-draw — and fits within the $3,250 board approval envelope.

### B. Add InflectionRunner for B1 in v0.3 scope

Rejected per CTO Option B + scope analysis. `harness/runner.py` has no `InflectionRunner`; Inflection AI's endpoint protocol differs from OpenRouter-compat (requires Pi.ai session auth, non-standard API surface). Adding it is net-new-vendor-SDK-class work, estimated at ~0.5 SE-day for implementation + env-var-security review + dry-run smoke, which does not fit the 4-day deadline when stacked with M1+M2 N=5 runs (wall-clock ~12-17h each). Filed to [BEAAA-793](/BEAAA/issues/BEAAA-793).

### C. Resolve M3 + G2 vendor-billing class in v0.3 scope

Rejected per CTO Q4=defer decision. M3 (Mistral) and G2 (gpt-4o) vendor-billing classification (α vs β) requires a separate CTO+board decision on Mistral API spend routing. CTO explicitly deferred to v0.3.1 with directional preference for (α)-first. Filed to [BEAAA-793](/BEAAA/issues/BEAAA-793).

## 4. N=5 live calibration delta — accepted

**STATUS: Live N=5 runs completed 2026-06-23 on the SE runtime.** Candidate aggregation uses median(N=5) over subject seeds 0..4; baseline remains the single deterministic prior per CTO Req 2 asymmetry. Judge pin held: `claude-sonnet-4-6`, temperature 0, corpus seed 42, ensemble_n=3.

Standing-table result:

| Agent | Prior ARE | Candidate ARE | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | Band | candidate spread |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| M1 | 59 | 58 | -1 | 0.0 | 0.0 | -1.5625 | 0.0 | C- unchanged | 0.0 |
| M2 | 47 | 48 | +1 | 0.0 | 0.0 | +0.5208 | 0.0 | D+ unchanged | 0.0 |

Measured bias raws:

| Fixture | P2.1 median raw | P2.4 median raw | Evidence |
|---|---:|---:|---|
| M1 | 0.0 | 3.5 | `artifacts/beaaa-387/M1/probes/bias-pack-{bbq,dna}-v0.1-seed{0..4}.json` |
| M2 | 0.0 | 3.5 | `artifacts/beaaa-387/M2/probes/bias-pack-{bbq,dna}-v0.1-seed{0..4}.json` |

Discrimination check post-run covers `["G1", "G3", "B3", "M1", "M2"]` (v0.3 measured set). ARE totals: G1 79, G3 73, B3 64, M1 58, M2 48. Mean 64.4, spread 31, collapsed=false. PASS per ADR-0012 R3.

Closeout refs: CTO calibration sign-off `f3f4456e`; HoUW upfront `57d8268d`; HoUW post-run ratification `35b9f923`; board approval `dc4c92a8`.

### 4.1 Post-run co-sign routing (A2 rules)

| delta | action |
|---|---|
| |Δ| ≤ 1pt all fixtures | upfront HoUW accept `57d8268d` sufficient — merge gate open |
| 1pt < |Δ| ≤ 3pt any fixture | post-run HoUW ratification comment required on BEAAA-759 before merge |
| |Δ| > 3pt any fixture | Founder escalation per Supreme Law |

Routing outcome: M2 cleared under upfront HoUW accept `57d8268d`. M1's BIAS_FAIRNESS movement of -1.5625 required and received post-run HoUW ratification in BEAAA-7762 cmt `35b9f923`. No Founder escalation was triggered.

## 5. Path-B adapter contract — forward spec for BEAAA-793

This section is the contract anchor CTO C6 required before B1 InflectionRunner can plug in for v0.3.1.

**BEAAA-793 implementation SHALL:**

1. Implement `InflectionRunner(ModelRunner)` in `harness/runner.py` with `model_id() -> str` returning the Pi.ai model identifier and `complete(prompt: str) -> str` using the Inflection session-auth API surface.
2. Add `_build_inflection()` to `scripts/path_b_endpoints.py` following the `_build_huggingchat()` + `_build_botpress()` pattern (env-var key: `INFLECTION_API_KEY` or Pi.ai session cookie, TBD at v0.3.1 intake).
3. Add `"B1"` to `PATH_B_FIXTURE_CONFIG` in `scripts/beaaa793_l2_bias_pack_run.py` (sibling script, do NOT modify the v0.3 `beaaa759` script).
4. Call `run_bias_pack(inflection_runner, ...)` — no pipeline changes.
5. File a separate HoUW upfront request + board approval for v0.3.1 spend envelope before first `--execute`.
6. Archive v0.3 M1+M2 artifacts with `.bias-pack-v0.3-prior.json` sibling before v0.3.1 overwrites (archive-before-overwrite invariant).

**M3 + G2 runner selection for BEAAA-793:**

Per CTO Q4 directional preference (α)-first: attempt M3 via `OpenRouterRunner` with Mistral model id before considering direct Mistral API. G2 (gpt-4o) similarly via OpenRouter. CTO + board approval at v0.3.1 intake will resolve billing-class classification definitively.

## 6. Rollback plan

Inherits ADR-0013 §5. v0.3-specific notes:

- **Trigger**: L2 live run delta > 1pt with post-run HoUW ratification denied, OR discrimination metric collapses post-M1+M2 measurement, OR endpoint unreachable for ≥2/5 seeds on either fixture.
- **v0.3-specific revert**: restore M1 + M2 `uncovered_dimensions` proxy status in v0.2 standing table; do not overwrite v0.2 artifacts. Archive already done pre-run per invariant; revert = restore from `.bias-pack-v0.2-prior.json` sibling.
- **BEAAA-793 impact**: If v0.3 rolls back, BEAAA-793 remains blocked on BEAAA-759. Escalate to CTO via issue comment before unblocking.

## Self-audit checklist

- [x] Carrier Rule: no carrier names in this ADR.
- [x] Canonical terms only: BackBond Scanner / BackBond Score naming used where this ADR is not quoting grandfathered contract fields.
- [x] No pillar weight change proposed.
- [x] No net-new rubric point proposed.
- [x] Judge model pinned (inherited from ADR-0013).
- [x] Path-B adapter contract pinned in §1 table + §5 forward spec.
- [x] BEAAA-793 deferred scope explicit (B1/M3/G2) with runner-availability table.
- [x] Rollback plan present (§6).
- [x] §4 calibration delta populated from 2026-06-23 live N=5 run.
- [x] `self_audit_passed: true` — reproducibility, discrimination, ratification, rollback, and canon-merge blockers recorded.

---

## References

- [BEAAA-759](/BEAAA/issues/BEAAA-759) — this ticket (v0.3 bias-pack Path-B extension).
- [BEAAA-793](/BEAAA/issues/BEAAA-793) — v0.3.1 deferred (B1/M3/G2).
- [BEAAA-758](/BEAAA/issues/BEAAA-758) — v0.2 bias-pack (B3/G1/G3 measured; M1/M2/B1/M3/G2 deferred).
- [BEAAA-749](/BEAAA/issues/BEAAA-749) — v0.1 bias-pack (ADR-0013, pack design).
- `tools/calibration-deltas/2026-05-26-beaaa758-bias-pack-v0.2-rebaseline.md` — v0.2 standing table (M1/M2 proxy baselines).
- `agents/cto-os.md` — Supreme Law (judge pinning + discrimination check + reproducibility).
- ADR-0012 — calibration set heterogeneity policy (per-version discrimination gating).
- ADR-0013 — bias-pack v0.1 design + golden calibration.
- CTO co-sign `b54c1143` 2026-05-27 — Q1=(a) live, Q2=(i) extend, Q4=defer, Option B, C9 threshold correction.
- HoUW upfront accept `57d8268d` 2026-05-26 12:02Z.
- Board approval `dc4c92a8` (approved).
