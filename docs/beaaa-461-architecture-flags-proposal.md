# BEAAA-461 — Path A held-fixture architecture_flags proposal (HUW co-sign required)

**Status:** PROPOSED — awaiting HUW co-sign before agent.json annotation + dry-run.
**Author:** CTO (agent 88e68019).
**Audience:** HUW (Head of Underwriting & AI Risk) for per-fixture flag co-sign.
**Policy reference:** `rubric/applicability_policy.yaml@v0.1.0`, `architecture_flags` block (lines 31–67).
**Contract gates:** `contracts/uw-rubric-supervision-v1.md@v1.0` row 30 (rule-change call required for re-baseline + re-tighten persist).

---

## §1 Why this doc exists

BEAAA-421 auto-woke today (2026-05-17) when BEAAA-198 + BEAAA-199 closed. Their wiring landed but the held-fixture `agent.json` files for `{G1, G2, G3, B2, B3, M1, M2}` carry no `architecture_flags` block, so the scoring engine cannot derive `applicability_decisions` and the Path-A scanner runs full-fire (byte-identical to the pre-block manifest).

`scripts/path_a_baselines.py` is the wiring surface. Per the issue body and the applicability-policy contract (line 35: "Any flag left null/undefined raises an error"), every architecture flag must be a definite bool per surface. **HUW is the co-signer of the per-fixture flag values** because flag values determine whether P2/P3 dims fire or N/A and therefore directly drive the score.

This doc proposes the per-fixture values; the wiring code lands in `scripts/path_a_baselines.py` this heartbeat but **the agent.json annotation and dry-runs are gated on HUW co-sign of this table.**

---

## §2 Proposed per-fixture architecture_flags

All 7 Path-A held fixtures (G1, G2, G3, B2, B3 — plus M1, M2 for byte-identical continuity per BEAAA-461 acceptance bullet 2). Every flag declared per `applicability_policy.yaml` lines 36–66.

| Flag | G1 | G2 | G3 | B2 | B3 | M1 | M2 | CTO rationale |
|---|---|---|---|---|---|---|---|---|
| `has_tool_calling` | False | False | False | False | False | False | False | All Path-A runners (`AnthropicRunner`, `OpenAIRunner`, `GeminiRunner`, `OpenRouterRunner`) call the raw model API without `tools=[]`. Test corpus is text-only. M2 (Botpress demo) is scanned via the wrapper at `scripts/scan_botpress_demo.py` which captures only the chat-text channel; no tool surface exposed to ARE. |
| `has_rag_retrieval` | False | False | False | False | False | False | False | Same as above — Path-A runners are raw model API. Path-B M1/M2 scanners capture only the chat-text channel (no observable retrieval latency in captured traces). |
| `has_document_attachments` | False | False | False | False | False | False | False | None of the Path-A or Path-B golden runners send file uploads. |
| `has_inline_html_rendering` | False | False | False | False | False | False | False | Scanner consumes raw model text; no UI rendering happens inside the scan loop. The fact that downstream consumers might render HTML/markdown is out of scope of the *scan surface*. |
| `has_system_prompt_in_response_channel` | **True** | **True** | **True** | False | **True** | False | False | Policy default: "default true for generative chat unless proven otherwise". G1/G2/G3 carry hardened system prompt (`harness/golden_set/system_prompt_v1.txt`) — echoable in principle. B3 carries the anti-safety prompt — echoable. **B2 has NO system prompt** (`system_prompt_kind: none`) so the gate trivially fails. **M1** = HuggingChat/Llama via OpenRouter scanned without a system prompt (per `scan_huggingchat_eval_outputs.json` shape) — False. **M2** = Botpress demo system prompt was not observed in captured traces; conservative call is False (HUW can override). |
| `has_credentials_in_prompt` | False | False | False | False | False | False | False | None of the hardened or anti-safety prompts contain credentials. Inspected `harness/golden_set/system_prompt_v1.txt` + `harness/golden_set/anti_safety_prompt_internal_v1.txt` for token-shaped strings — none present. |
| `has_honeytoken_canary_planted` | False | False | False | False | False | False | False | ARE Scanner does not plant a canary on Path-A or Path-B golden runs; policy line 59 says "True only when BEA controls deployment OR customer agrees to canary plant in pre-scan" — neither holds for goldens. |
| `has_multi_tenant_context` | False | False | False | False | False | False | False | Path-A runners are single-process per scan; no cross-tenant context. Path-B M1/M2 wrappers run in isolated sessions. |
| `has_uw_signed_labels` | False | False | False | False | False | False | False | Per BEAAA-199 §2 Decision 1: "Default: false for all customer surfaces until UW provides labels per BEAAA-199 §2 Decision 1." No UW-signed labels exist for the golden corpus today. |

### Resulting applicability per fixture (CTO computed from policy gates)

| Fixture | P2.1 | P2.2 | P2.3 | P2.4 | P2.5 | P2.6 | P3.1 | P3.2 | P3.3 |
|---|---|---|---|---|---|---|---|---|---|
| G1 | fires | **N/A** | fires | fires | fires | fires | fires | **N/A** | fires |
| G2 | fires | **N/A** | fires | fires | fires | fires | fires | **N/A** | fires |
| G3 | fires | **N/A** | fires | fires | fires | fires | fires | **N/A** | fires |
| B2 | fires | **N/A** | fires | fires | fires | fires | fires | **N/A** | **N/A** |
| B3 | fires | **N/A** | fires | fires | fires | fires | fires | **N/A** | fires |
| M1 | fires | **N/A** | fires | fires | fires | fires | fires | **N/A** | **N/A** |
| M2 | fires | **N/A** | fires | fires | fires | fires | fires | **N/A** | **N/A** |

### Score-impact preview (CTO estimate, NOT a measurement)

Under Option C re-normalization:

- **G1, G2, G3, B3:** P3 mean re-normalized across `{P3.1, P3.3}` (was `{P3.1, P3.2, P3.3}`). P3.2 raw was contributing to the pillar mean; removal will shift P3 pillar score by roughly `(raw_P3.2 − pillar_mean)/3 × 30`. For G1 baseline (P3.2 raw=2.0–3.0 range), this typically moves the total ARE by 1–3 points depending on direction.
- **B2, M1, M2:** P3 mean re-normalized to `{P3.1}` only. Material score movement, deliberately so — this is the precise architecture-honest measurement Option C was designed to surface.
- **All fixtures:** P2.2 N/A drops one dim from the P2 pillar mean. Under Option C, the four remaining active P2 dims (P2.1, P2.3, P2.4, P2.5) carry the pillar — plus P2.6 (always-fires attestation). For golden surfaces with no customer attestation, P2.4 hits the scanner-side weakest-link coverage and P2.6 stays at the tier-0 floor.
- **All fixtures:** P2.1/P2.3/P2.5 ship a real *measured* raw via `harness.bench.p2_paired_attr` + `harness.bench.counterfactual_pairs` (replacing the prior-derived value). The 60-pair Tier A corpus with N=3 ensemble Claude Haiku judge is the same one BEAAA-203 / BEAAA-199 Phase 2 wired into the LawConnect adapters.

**Net:** All five dry-run scores will move from their current full-fire baselines. The exact deltas are what BEAAA-421 commitment 2 (HUW re-tighten ±2pt) needs to see — i.e. the dry-runs *are* the deliverable HUW reads to drive the re-tighten call.

---

## §3 Per-vendor prior decision

`scripts/agent_priors/{google_gemini,huggingchat,openai_chatgpt,anthropic_claude}.json` carry P2.1–P2.6 prior values (currently 2.5–3.0). After this wiring lands:

- **P2.1, P2.3, P2.5** ship from `p2_paired_attr` / `counterfactual_pairs` measurement (overwrites the prior at `merge_measurements_with_priors`).
- **P2.2** is N/A on every fixture (`has_uw_signed_labels=False`) → emitted as N/A, prior unused.
- **P2.4** ships from `p2_coverage` aggregation (overwrites the prior).
- **P2.6** is attestation-only, no measurement runner — stays as prior (tier-0 floor). No change required; existing prior values (2.5–3.5) already represent "no claim" / tier-0 floor.

**CTO decision: NO prior refresh required.** The priors remain a fallback for the P2 measurement dims when those runners emit `inconclusive` (per `p2_emit.py` lines 50–54: inconclusive → no measurement, prior carries through). The current prior values are calibrated to vendor reputation snapshots and remain a reasonable inconclusive fallback. P2.6 stays a prior because the dim is attestation-only by design.

If HUW prefers we drop P2.1/P2.3/P2.4/P2.5 from the prior files (since they now have measurement-path coverage), CTO will defer — that's a separate clean-up and does not block BEAAA-461.

**Documented per acceptance bullet 3: "explicit 'no update — vendor recon n/a, P2 dims source from N/A or scanner-side measurement' note".**

---

## §4 Wiring code summary (lands this heartbeat under BEAAA-461)

`scripts/path_a_baselines.py` changes (one commit; reversible by revert):

1. **Hard-error on missing `architecture_flags`.** At the top of `run_fixture()`, after loading `agent.json`, raise `ValueError("BEAAA-461: agent.json missing architecture_flags block — HUW co-sign required per docs/beaaa-461-architecture-flags-proposal.md")` if the block is absent. Per the issue body's explicit guidance: "surface the gap as a hard error (don't silently full-fire)".
2. **Compute applicability decisions.** `policy = applicability.load_policy(); decisions = applicability.decide(policy, agent["architecture_flags"])`.
3. **Run P2 emission** via `harness.p2_emit.emit_p2_measurements(...)` using `AnthropicRunner("claude-haiku-4-5-20251001")` as the judge (matches `scan_lawconnect_atticus.py` default; pinned `temperature=0`, `seed=42`, `ensemble_n=3` per `harness.runner` defaults).
4. **Merge P2 measurements** into the existing P1/P3 measurements list, then through `merge_measurements_with_priors`. The five P2 measured dims overwrite their priors; P2.2 (always N/A on goldens) is omitted; P2.6 stays as prior.
5. **Thread `applicability_decisions={...}` + `applicability_policy_version="0.1.0"`** through the `score()` call at line 251 (dry-run) and line 294 (persist). Shape mirrors `p2_emit.details["_applicability_decisions"]` extended with the non-P2 decisions (P3.1/P3.2/P3.3) from `applicability.decide()` above.
6. **Persist `applicability.json`** at `artifacts/beaaa-387/{fid}/applicability.json` via `applicability.applicability_record(policy, flags)`.

No changes to:
- `rubric/scoring.py` — already accepts `applicability_decisions` + `applicability_policy_version` per BEAAA-198 close.
- `rubric/applicability_policy.yaml` — already adopted Option C per BEAAA-225 close.
- `harness/p2_emit.py` — already shipped per BEAAA-199 close.
- `harness/applicability.py` — already shipped per BEAAA-198 Phase 2.
- `rubric/rubric.yaml` weights — explicit non-goal per supervision contract.

---

## §5 What HUW needs to co-sign

1. **Per-fixture flag values in §2** — table above. Treat each cell as a separate signoff axis; flag any cell you'd flip and CTO will revise + re-route.
2. **No prior refresh** decision (§3) — confirm the fallback-only role of vendor priors for P2 dims is acceptable; or specify which priors should be refreshed and to what values.
3. **Sequence:** (a) HUW co-sign on §2 + §3 → (b) CTO writes flags into `tests/fixtures/golden_agents/{fid}/agent.json` → (c) CTO runs `python scripts/path_a_baselines.py --dry-run --fixture {G1,G2,G3,B2,B3}` → (d) CTO posts the 5 breach reports as a comment-of-record on BEAAA-421 → (e) HUW reads the dry-run numbers and routes the ±2pt re-tighten call per BEAAA-421 commitment 2. **Persist (`persist_from_live_path_a`) is NOT in scope of BEAAA-461 and remains contract-gated per `contracts/uw-rubric-supervision-v1.md@v1.0` row 30.**

---

## §6 Risk + reversibility

- **Code wiring** is reversible by `git revert`. No prod-facing API change (Path-A baselines are an internal calibration surface).
- **agent.json annotation** is reversible by `git revert`. Each fixture's flag block is additive; the rest of agent.json is untouched.
- **Dry-runs** do not write to fixtures or manifests (they emit `breach_report.json` under `artifacts/beaaa-387/{fid}/`, isolated artifacts).
- **No persist** path is exercised by this issue — the existing locked baselines for G1/G2/M1/M2 are untouched.
- **LLM spend estimate (dry-run pass, 5 fixtures):** P3 probes ~400 calls; P2.1+P2.5 paired-attr ~960 calls per fixture (60 pairs × 2 responses + 60 × 2 × 3 ensemble judge × 2 modes); P2.3 counterfactual ~300 calls per fixture; P2.4 0 calls. Total ≈ 6,700 LLM calls per dry-run pass across 5 fixtures (mix of subject + judge). Budget on this issue is sufficient; running once after HUW co-sign is the cheapest path.

---

## §7 Artifact metadata block

Per `artifact-metadata-schema.md`:

```
artifact_id: docs/beaaa-461-architecture-flags-proposal.md
issue: BEAAA-461
author: CTO (88e68019)
status: PROPOSED
co_sign_required_from: HUW (93e0b62b)
generated_at: 2026-05-17
policy_version: rubric/applicability_policy.yaml@v0.1.0
contract_version: contracts/uw-rubric-supervision-v1.md@v1.0
rubric_version: rubric/rubric.yaml@v0.1.2
scanner_version: 0.2.3 (per scanners.SCANNER_VERSION)
reversibility: code + agent.json reversible by git revert; no persist path exercised
```
