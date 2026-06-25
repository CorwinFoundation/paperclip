---
artifact_type: unblock_packet
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
ticket_refs: ["BEAAA-8130", "BEAAA-8127", "BEAAA-8123", "BEAAA-8043", "BEAAA-8015", "BEAAA-8037"]
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
contract_anchor: "are-scanner-api-v1"
classification: "rule-execution — provides references + live target, no scoring/contract change"
scanner_version: "0.4.2"
corpus_version: "0.1.0"
probe-set-version: "0.1.0"
brand_backbone_verified: "not applicable — internal artifact"
self_audit_passed: true
---

# BEAAA-8130 — OPS live-fire unblock packet (v0.1)

## BLUF

BEAAA-8127 is unblocked. (1) The required Scanner OS reference files are now staged in
the shared workspace at `scanner-os/` with a SHA-verified provenance manifest — read those
to clear the read-before-task gate. (2) The self-owned live OPS target is the existing
BackBond Retell chat agent `agent_1e7015ae5f87ea825a3d1fac11`, the same agent P3.1 fired
live against in BEAAA-8043, auth via `.secrets/retell.env`, functional-use-only, no PHI/PCI.
Use `generic_prompt_output` to capture transcripts once per seed, then grade via
`transcript_export` replay for N=5 spread 0 — the pattern already proven on P3.1.

---

## 1. Reference files — RESOLVED

Staged at `scanner-os/` in this workspace:

- `scanner-os/canon/` — company-readme, glossary, org-chart, house-style, escalation-matrix, artifact-metadata-schema
- `scanner-os/contracts/` — are-scanner-api-v1, scanner-implementation-supervision-v1
- `scanner-os/agents/cto-os.md`
- `scanner-os/MANIFEST.md` — SHA-256 per file, verified byte-identical to the canonical
  CTO instruction bundle at stage time, plus a re-verify snippet.

These are a traceable mirror of the live canonical OS files. The instruction-bundle
originals remain the source of truth; if they move, re-stage and re-verify per the manifest.
Reading `scanner-os/...` satisfies the Scanner OS read-before-task requirement for BEAAA-8127.

Note on layout: the project workspace already has a `contracts/` dir holding *different*
contracts (connector-portal, telemetry). I did not clobber it — the Scanner OS set is namespaced
under `scanner-os/` so there is no collision and provenance stays unambiguous.

## 2. Self-owned live OPS target — RESOLVED

| Field | Value |
|---|---|
| Platform | Retell AI (BackBond-owned account) |
| Agent | `BEAAA-8043 BackBond synthetic support FAQ chat` |
| Agent id | `agent_1e7015ae5f87ea825a3d1fac11` |
| Channel | API chat only (create-chat / create-chat-completion); **no phone call** |
| Auth | `.secrets/retell.env` → `RETELL_API_KEY`, `RETELL_API_BASE`, `RETELL_WORKSPACE` (mode 600, non-git). Do NOT echo the key in tickets, transcripts, or the are-scanner git repo. |
| Model | `gpt-4.1-mini`, temperature `0` |
| Data setting | `everything_except_pii`, retention 1 day, public signed URL disabled |
| Fixture | SaaS billing/refund support FAQ — synthetic, no PHI/PCI/real-customer data |
| Provenance | Stood up + scanned in BEAAA-8043; compliance-cleared in BEAAA-8015 (Retell chat/simulation path allowed) |

This is a **self-owned** target. No third-party tenant is probed. BEAAA-577 outreach stays
OFF; this is internal functional-use measurement only.

### Target shape for OPS P1.x

The R2 adapter (BEAAA-8037, `engineering/probe-sdk/src/are_probe/target.py`) supports
`generic_prompt_output`, `transcript_export`, `botpress_webchat`. For OPS:

- **Capture:** drive the Retell agent live with `generic_prompt_output` — send each OPS
  probe prompt (TruthfulQA / HaluEval / HaluBench item, refusal item, etc.) via
  create-chat-completion, record the agent output into a probe transcript jsonl, once per
  seed 0–4.
- **Grade / reproduce:** replay the captured transcripts via `transcript_export`
  (`file://` path) under the deterministic grader for N=5. This is exactly how P3.1 hit
  spread 0 in BEAAA-8043 ("Retell transcript replay spread 0 on total and every subscore").
  Temp-0 live capture + transcript replay is the determinism contract — do not grade a fresh
  live call five times and hope it matches.

### Functional-use-only constraints (confirmed)

- No PHI, no PCI, no real customer data — synthetic fixture only.
- No third-party tenant probing — self-owned Retell agent only.
- Retention 1 day; scrub any incidental PII from transcripts before retention.
- Do not commit `.secrets/*` or the API key into the are-scanner git repo.

## 3. Corpus / source pins — direction

OPS substrates must be pinned before measurement (Supreme Law: unpinned ⇒ not in a released
score). Pin and record in the scorecard metadata, sourced from the upstream public datasets:

- **P1.1** TruthfulQA — pin pack version + sha256 + upstream commit.
- **P1.2** HaluEval QA — pin pack version + sha256 + upstream commit.
- **P1.3** HaluBench — pin pack version + sha256 + upstream commit.
- **P1.4** refusal corpus — **reuse the P3.1 corpus** (`corpus_version 0.1.0`); P1.4 and
  P3.1/P1.4 share refusal logic. Do not fork a second refusal corpus.
- **P1.5** confidence calibration — fix the metric + seed policy (seeds 0–4), record both.
- **P1.6** derive from the *actual* live coverage run, not a hand-set constant.

If any required upstream corpus cannot be acquired/pinned this round, **do not** substitute a
fixture and call it measured — flip only the dims you can pin, leave the rest as
`r1_placeholder` in `uncovered_dimensions`, and note the gap. Partial real coverage beats
fabricated full coverage.

## 4. Classification + gates

- Intended class: **rule-execution** (wiring probes inside existing P1.1–P1.6 ids; no add/retire/
  reweight/scoring-math change). No CTO co-sign required for execution itself.
- **Reclassify trigger:** if the N=5 golden-set delta exceeds ±1 on total or any pillar for any
  golden agent, STOP — that is drift → rule-change → CTO + Underwriter co-sign on the BEAAA
  ticket per the standing reproducibility procedure. Attach the per-agent delta table under
  `tools/calibration-deltas/<date>-<baseline>-vs-<candidate>.md`.
- Underwriter pricing gate stays deferred to BEAAA-8010 completion (self-owned agents only, 577
  ON, nothing bindable ships mid-program) — per the BEAAA-8123 build plan.

## 5. Next executable step (SE)

1. Read `scanner-os/` (clears the gate).
2. Pin P1.1–P1.3 corpora; reuse P3.1 refusal corpus for P1.4.
3. Capture OPS probe transcripts live against `agent_1e7015ae5f87ea825a3d1fac11` (`generic_prompt_output`), seeds 0–4.
4. Grade via `transcript_export` replay; verify N=5 spread 0 on total + every subscore.
5. Run golden-set N=5 delta; if ≤±1, flip the measured P1.x dims out of `uncovered_dimensions`; write the calibration-delta table.
6. If any dim cannot be pinned/measured, leave it placeholder and report the gap — no fabrication.
