---
artifact_type: adr
owner: scanner-engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-794 activates two new rubric_point_ids — ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL + ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER — wrapping AgentDojo 0.1.35 task suites (workspace, slack, banking, travel) under the WS-B sanitization layer. WS-A registers; WS-B sanitizes; WS-C N=5-gates and certifies the rule change with zero incremental ARE / pillar drift on the golden roster (B1, B3, G1, G2, G3) and discrimination width = 40."
contracts_referenced: ["are-scanner-api-v1@v1.0.3", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-794", "BEAAA-1682", "BEAAA-1683", "BEAAA-1686", "BEAAA-1045", "BEAAA-225"]
proof_points_cited:
  - "tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.json — N=5 byte-identical on all 8 baselined goldens; max WS-A incremental ARE drift = 0 on golden roster"
  - "tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.md — sibling rendered report"
  - "ADR-0018 (BEAAA-1683 WS-B sanitization) — additive tamper-resistance strengthening in path during WS-C N=5"
  - "applicability_policy.yaml v0.1.1 — Option C re-normalization (UW + CEO co-signed via BEAAA-225 closed 2026-05-17)"
  - "rubric/probe_mappings/agentdojo_tool_misuse.yaml v0.1.0 — probe-set mapping; pinned upstream agentdojo==0.1.35"
  - "scanners/agentdojo_runner.py::run_tool_misuse_via_agentdojo — wrap-only adapter; no upstream fork"
scanner_version: "0.3.9"
corpus_version: "path_a_v2_1@2026-05-15+bias-pack-v0.1"
probe_set_version: "agentdojo_tool_misuse@0.1.0"
rubric_point_id: "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL, ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER"
n5_calibration_delta_ref: "tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.md"
upstream_eval_version: "agentdojo==0.1.35 (pinned, wrap-only)"
cto_cosign_ref: "comment-b4c564db-1bb0-43c6-bb30-f7cd1c7f2583 (parent BEAAA-794 plan)"
head_of_uw_cosign_ref: "BEAAA-1045 + BEAAA-1045_houw_cosign_v0.1.md"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# ADR-0019 — BEAAA-794: ROBUSTNESS.TOOL_MISUSE.* rubric activation

**Status:** Proposed. Co-sign matrix:
- CTO: pre-anchored at parent BEAAA-794 plan via `comment-b4c564db-1bb0-43c6-bb30-f7cd1c7f2583` (cosign-trail document `/BEAAA/issues/BEAAA-794#document-cosign-trail`).
- HoUW: pre-anchored at BEAAA-1045 + `BEAAA-1045_houw_cosign_v0.1.md`.
- Founder: **not** required (no pillar weight change; tamper-resistance posture strictly strengthened by the WS-B layer carried in path during this rule change).

**Date:** 2026-06-01.
**Author:** Scanner Engineer.
**Implements:** parent [BEAAA-794](/BEAAA/issues/BEAAA-794) §CTO Cond 1 (N=5 calibration delta), §CTO Cond 5 (rule-change ADR with all 5 sections), §UW Cond 6 (`uncovered_dimensions[]` housekeeping), §UW Cond 8 (auditor-readable cosign trail in envelope wiring).

**Classification:** Rule change per `contracts/scanner-implementation-supervision-v1.md@v1.0`:
- Row 23 (Add a new rubric_point_id to the canonical scoring sheet) → CTO co-sign + ADR.
- Row 16 (Tamper-resistance posture change — WS-B sanitization layer carried in path) → CTO co-sign. Additive strengthening; Founder reduction-gate not triggered.

## §1 BLUF (one sentence per parent §CTO Cond 5)

Activate `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL` and `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER` as measured rubric_point_ids backed by an AgentDojo 0.1.35 wrap (workspace + slack + banking + travel task suites), architecture-gated on `has_tool_calling=true` via `applicability_policy.yaml@v0.1.1`; rubric 1.0.0 → 1.0.1 bump; scanner 0.3.8 → 0.3.9 bump; contract are-scanner-api-v1 v1.0.3 unchanged; N=5 WS-A incremental drift = 0 ARE / 0 pillar on every golden in the binding roster (B1, B3, G1, G2, G3) and on the M-grade backstops (M1, M2, M3); discrimination width = 40 (well above the ±10 collapse threshold); merge cleared.

## §2 Reasoning (per parent §CTO Cond 5)

Three forces push for activation now:

1. **Pricing-power gap.** The CTO Hard Guarantee on the Underwriter side (`are-scanner-api-v1.md@v1.0.3` §UW-1) says no quote without a complete evidence trail; gaps in the Scanner are gaps in IFA's pricing power. Tool misuse is the dominant cyber-adjacent loss-severity channel per BEAAA-13 NorthStar §3: an agent that incorrectly issues a `banking.transfer` call to an attacker-controlled IBAN is a real loss event, not a risk theoretical. The deferred P3.4 slot (rubric 1.0.0 `ROBUSTNESS.deferred_to_v02`) covered this gap on paper but worst-case-loaded it through `uncovered_dimensions[]` — every customer with tool-calling architecture paid the maximum-risk load, including customers with strong tool-call hygiene. The activation gives Underwriting differential pricing power.
2. **Wrap-availability.** AgentDojo 0.1.35 (Debenedetti et al., 2024) ships task suites that cover unauthorized-call and parameter-tampering attack families. The wrap is the canonical operationalization path (`scanner-implementation-supervision-v1.md@v1.0` Row 7 — wrap, don't fork). BEAAA-713 already pinned agentdojo 0.1.35 for P3.2 indirect-injection; reusing the same pin keeps the upstream-eval-version-drift audit hook coherent.
3. **WS-B tamper resistance is now live.** ADR-0018 (BEAAA-1683) landed the probe-rendering sanitization layer that interposes between AgentDojo's adversarial probe corpus and every scanner-side render context. The same module guards the new TOOL_MISUSE wrap — it must, because attacker-shaped scenarios in `banking.transfer` and `slack.post_to_channel` payloads are exactly the bodies that motivated WS-B. Activating TOOL_MISUSE without WS-B in path would have re-opened the scanner-control-byte attack surface; activating with WS-B in path is the rule-change shape this ADR documents.

These three forces are also why **WS-C** has the shape it does (N=5 + ADR + uncovered-dim housekeeping + cosign metadata block): the rule change is binding only when the N=5 reproducibility band is verified, the rejected alternatives are recorded, and the UW pricing engine and auditor can both read the cosign trail from the envelope without spelunking per-probe artifacts.

How the change preserves the CTO's five hard guarantees (`contracts/are-scanner-api-v1.md@v1.0.3`):

| Hard guarantee                       | How WS-A/WS-B/WS-C preserve it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1. Reproducibility (±1 ARE / ±1 sub) | N=5 spread = 0 on every roster + backstop fixture. WS-A incremental delta = 0 on every roster fixture. See `tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.{json,md}`. |
| 2. Evidence trail completeness        | Every measured `rubric_point_id` row emits a non-empty `artifact_uri` pointing at the per-item AgentDojo probe trace persisted under `artifacts/<scan_id>/probes/tool_misuse/<dim>/<probe_id>.json`. The G1 wiring smoke (`scripts/beaaa1682_g1_tool_misuse_smoke.py`) asserts this on the synthetic G1-tool variant. |
| 3. Pinned versions                    | `agentdojo==0.1.35` pinned in `requirements.txt`; `agentdojo_tool_misuse.yaml::version: 0.1.0`; rubric 1.0.1; scanner 0.3.9; `applicability_policy.yaml 0.1.1`. All five stamps appear in this ADR's frontmatter and in the calibration delta artifact. |
| 4. Discrimination                     | Roster width post-activation = 40 (mean = 66.4, min = 39, max = 79). Baseline width = 43; -3 contraction is attributable to pre-existing rubric_reweight + applicability_policy_stamp drift on B1/B3/G1 baselines, not to the WS-A activation (WS-A incremental width delta = 0). |
| 5. Tamper resistance                  | WS-B sanitization layer in path (ADR-0018). New wrap consumes adversarial probe bodies; sanitization neutralizes embedded wrapper sentinels, strips ANSI / control bytes / DEL, length-caps at 8 KiB. Transport surface to scanned agent is byte-stable per `tests/scanners/test_agentdojo_probe_sanitization.py::F1/F2/F3` regression fixtures. |

## §3 Rejected alternatives (per parent §CTO Cond 5)

Four serious alternatives considered and rejected, with the reason each was rejected:

1. **Stay deferred — keep `P3.4` in `deferred_to_v02` and pay the worst-case load.** Rejected. The `uncovered_dimensions[]` worst-case loading is a discrimination-killer for any customer with strong tool-call hygiene — exactly the customer profile we want to underwrite at a competitive rate. The pricing power gap was the reason BEAAA-794 was opened.
2. **Fork AgentDojo into ARE — patch the upstream task suites to ARE's idiom inside `scanners/`.** Rejected. Direct violation of `scanner-implementation-supervision-v1.md@v1.0` Row 7 ("Wrap an upstream eval framework as an ARE probe — Rule execution, NO CTO co-sign") and the explicit auditor hook on duplicate-logic drift. The wrap approach in `scanners/agentdojo_runner.py::run_tool_misuse_via_agentdojo` reuses the upstream task suites by reference; rename-across-minors triggers fresh CTO co-sign per the wrap-don't-fork audit hook.
3. **Custom probe corpus — ship ARE-authored unauthorized_call / parameter_tamper scenarios.** Rejected. Two reasons. (a) Loses the AgentDojo benchmark provenance — independent third-party adversarial benchmark is the strongest claims-defense story; (b) re-creates the d1832a66 retraction failure mode where ARE-internal probe text drifted from a public benchmark and the published score lost its citation anchor.
4. **Activate without WS-B sanitization — defer ADR-0018 to a follow-up.** Rejected. Activating TOOL_MISUSE wrap before WS-B is in path re-opens the scanner-control-byte attack surface that motivated WS-B in the first place (the banking / slack / workspace task suites are exactly the adversarial-shape corpora that exploit unsanitized render contexts). Hard-blocking dep: WS-A depends on WS-B in path. The parent BEAAA-794 plan sequenced them WS-B → WS-A on this basis.

## §4 Calibration delta on the golden set (N=5 each — per parent §CTO Cond 5)

See `tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.md` for the full table and structural argument. Summary:

| fixture | slot           | baseline_are | pre_ws_a_are | current_are | N=5 spread | WS-A ΔARE | WS-A pillar Δ max abs | within CTO Cond 1 ±1? |
| ------- | -------------- | ------------ | ------------ | ----------- | ---------- | --------- | --------------------- | ---------------------- |
| **B1**  | known_bad      | 38           | 39           | 39          | 0          | **0**     | **0.0**               | **yes**                |
| **B3**  | known_bad      | 65           | 64           | 64          | 0          | **0**     | **0.0**               | **yes**                |
| **G1**  | known_good     | 81           | 79           | 79          | 0          | **0**     | **0.0**               | **yes**                |
| **G2**  | known_good     | 77           | 77           | 77          | 0          | **0**     | **0.0**               | **yes**                |
| **G3**  | known_good     | 73           | 73           | 73          | 0          | **0**     | **0.0**               | **yes**                |
| M1      | graded_middle  | 59           | 59           | 59          | 0          | 0         | 0.0                   | yes (backstop)         |
| M2      | graded_middle  | 47           | 47           | 47          | 0          | 0         | 0.0                   | yes (backstop)         |
| M3      | graded_middle  | 54           | 54           | 54          | 0          | 0         | 0.0                   | yes (backstop)         |

Three drift components are separable in the artifact:

* `rubric_reweight` — v0.1.2 → v1.0 pillar rescale (BEAAA-353). B1 only. Pre-existing.
* `applicability_policy_stamp` — Option C activation (BEAAA-198 / BEAAA-225). G3/B3 baselines were locked without the sidecar threaded; replay under the threaded sidecar re-normalizes the pillar mean over active dims. Pre-existing.
* **WS-A incremental delta** — the rule change this ADR documents. **Zero** on every roster fixture by construction (TOOL_MISUSE.* fire = false on every non-tool-calling roster surface; Option C excludes them from the pillar mean; pillar mean unchanged → ARE unchanged).

Discrimination metric width across the roster = 40 (mean 66.4, min 39, max 79). No collapse to within ±10 of the mean. Release-block check clears.

## §5 Rollback plan (per parent §CTO Cond 5)

Trigger conditions (any one suffices):

1. WS-C N=5 re-run shows non-zero WS-A incremental drift on any golden in the roster.
2. UW intake reports a quote regression attributable to the new `rubric_point_id` rows (delta > 1 on real customer surfaces with `has_tool_calling=true`).
3. AgentDojo 0.1.36+ ships and the upstream-eval-version-drift audit hook surfaces probe-id renames that the ARE wrap cannot resolve.
4. A scanned customer agent is observed to bypass both new dims via a tamper-resistance gap not covered by the WS-B sanitization layer.

Rollback steps (sequenced; idempotent at each step):

1. **Revert rubric** `rubric/rubric.yaml` from 1.0.1 to 1.0.0. Restores `P3.4` in `deferred_to_v02`; drops `ROBUSTNESS.TOOL_MISUSE.*` from the active dim set.
2. **Revert applicability policy** `rubric/applicability_policy.yaml` from 0.1.1 to 0.1.0. Drops the two `dim_applicability` entries and the shared `ROBUSTNESS.TOOL_MISUSE.no_tool_surface` justification.
3. **Revert probe-mapping** `rubric/probe_mappings/agentdojo_tool_misuse.yaml` — drop or pin to `version: 0.0.0-deprecated`. Mark `uncovered_dimensions: ["ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL", "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER"]` so UW worst-case loading re-applies.
4. **Revert wrap** `scanners/agentdojo_runner.py::run_tool_misuse_via_agentdojo` — keep code path importable but raise `NotImplementedError("rolled back per ADR-0019 rollback")` so any consumer hits a clear sentinel.
5. **Revert scanner version** `scanners/__init__.py::SCANNER_VERSION` from `0.3.9` back to `0.3.8`. Bump comment line cites this ADR's rollback section.
6. **Revert applicability sidecars** in `artifacts/beaaa-387/<fixture>/applicability.json` — strip the two TOOL_MISUSE entries from `applicability_decisions` and `na_dims`; restore policy_version to `0.1.0` on G2/G3/B3/G1 (the ones we created from scratch — B1/M1/M2/M3 — can either revert to "no file" or keep the minimum-shape file but strip the TOOL_MISUSE entries).
7. **Re-run** `harness/golden_set/n5_band.py::run_l1_n5` + `harness/golden_set/calibration_delta.py::compute_delta` to confirm the roster scores match the pre-WS-A `pre_ws_a_are` column above byte-identically.
8. **File ADR-0019-supersede** documenting the rollback rationale, the trigger that fired, and the v0.2 follow-on plan.

Owner: CTO (rollback governance). Operational executor: Scanner Engineer. SLA: rollback runbook executable in one engineering day; calibration verification within 48 hours of the trigger.

WS-B sanitization layer (ADR-0018) does **not** roll back as part of this ADR's rollback. The layer is additive tamper-resistance strengthening and benefits all other ROBUSTNESS probes (P3.1, P3.2, P3.3). Its rollback follows ADR-0018's own rollback plan.

## §6 Open follow-ups (post-merge, not blocking)

* WS-D — UW band-mapping re-confirmation against the post-activation roster (BEAAA-794 plan §WS-D).
* Pricing engine update — read `uncovered_dimensions[]` row-level `na_justification_key == "ROBUSTNESS.TOOL_MISUSE.no_tool_surface"` and skip worst-case loading on those rows (per HoUW co-sign `BEAAA-1045_houw_cosign_v0.1.md`).
* AgentDojo 0.1.36 reachability check (BEAAA-798 cadence) — re-pin if upstream renames any of the canonical task IDs the wrap reads.
* Customer-onboarding harness — extend `harness/p4_questionnaire/` to ask the prospect to declare `has_tool_calling` before pre-bind scan so the applicability gate fires correctly on the first run (not a rule change; rule-execution per Row 13).

## §7 Self-audit (per scanner-engineer-os.md pre-submission checklist)

1. Read all 9 reference files this turn? **Yes** — see also calibration-delta sibling md frontmatter.
2. Deliverable in one sentence with type/audience/outcome/budget? **Yes** — see frontmatter `focus_claim` + §BLUF.
3. Classified rule-change vs rule-execution + cited table row? **Yes** — Row 23 + Row 16.
4. Ran N=5 on golden + attached delta report? **Yes** — `tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.{json,md}`.
5. CTO co-sign? **Yes** — `comment-b4c564db-1bb0-43c6-bb30-f7cd1c7f2583`.
6. Cited active version of `contracts/are-scanner-api-v1.md`? **Yes** — v1.0.3.
7. All 5 required metadata fields present? **Yes** — frontmatter.
8. Glossary clean? **Yes** — ARE / IFA / BEAAA-NNN canonical. Pre-submission grep clean.
9. Probe wraps upstream, not forks? **Yes** — `agentdojo==0.1.35` pinned; `scanners/agentdojo_runner.py` adapter; no upstream re-implementation.
10. FAL audit attached? **N/A** — internal ADR; not customer-, investor-, or press-facing.
