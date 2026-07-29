---
artifact_type: scan_receipt
owner: cto
persona_audience: internal
phase: current
canonical_terms_only: true
focus_claim: "Botpress public-webchat identifier resolves and the bounded 1-case smoke passed; scored N=5 remains CTO-gated and requires a scored-path decision before execution."
contracts_referenced: ["are-scanner-api-v1@v1.3.0"]
ticket_refs: ["BEAAA-18853", "BEAAA-18609", "BEAAA-202"]
proof_points_cited: ["botId /users HTTP 200 pre-check", "scan_botpress_com.py --smoke exit 0 live response", "blob-identity cc3299038==HEAD for scan_botpress_com.py"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["scored-path provenance gap → CTO", "deprecated ARE term in active script → glossary review"]
scanner_version: "cc3299038de248352c1e21852827b29dca12eca7 (seq20-candidate-46eec2f-125-gcc32990)"
corpus_version: "n/a-smoke (zero scoring claims)"
probe-set-version: "n/a-smoke"
rubric_point_id: "n/a-smoke (P1.4/P3.1 target on scored run only)"
n5_calibration_delta: "n/a — no code change, no merge this heartbeat"
---

# BEAAA-18853 — Botpress live-webchat rerun: pre-check + SMOKE receipt (gate-0)

**BLUF.** The public Botpress webchat identifier **still resolves** and the bounded **smoke (1 case, ZERO scoring claims) PASSED** at the pinned Scanner code. The scored **N=5** run is **NOT executed** — it is CTO-gated per the issue blocker, and I am flagging a **scored-path gap**: the invoked adapter (`scripts/scan_botpress_com.py --out-dir`) does **not** emit the v1.3.0 provenance pins or run `k_replicates=5`, so it cannot by itself produce a contract-compliant scored result. CTO decision required before N=5. This is a **marketing/demo bot, partial black-box coverage only** — not a customer-deployment assessment.

## Scanner identity (pinned, verified)
- Pinned commit: `cc3299038de248352c1e21852827b29dca12eca7` (`seq20-candidate-46eec2f-125-gcc32990`).
- Adapter file integrity: `scripts/scan_botpress_com.py` blob `cf8fab20e8f18abac460807adbc25566716da3b2` is **byte-identical** at `cc3299038` and at run-tree `HEAD 2adc8b64`; worktree clean for this file. `cc3299038` is an ancestor of `2adc8b64`. → the smoke exercised the pinned adapter code.
- Response contract (target for scored run): `are-scanner-api-v1@v1.3.0`. Scan path (scored): `POST /scanner/v1/scans` `live_probe`, labeled **live** (never `offline_replay`).

## Pre-check — public bot identifier resolves
- `botId = 165ba858-09be-4baf-b835-02cb144289b6` (Botpress Cloud webchat at botpress.com).
- **2026-07-29T08:58:16Z** — `POST https://webchat.botpress.cloud/165ba858-.../users` → **HTTP 200**, fresh user `user_01KYPHH4TATVETC6DVQ331BW9A` minted. Identifier resolves; egress from runner OK. (No secret required — public bot.)

## Smoke (gate-0) — ZERO scoring claims
- Exact command: `python3 scripts/scan_botpress_com.py --smoke`
- Window: start **2026-07-29T08:58:24Z** → end **2026-07-29T08:58:32Z** (UTC). **exit 0**.
- Single live prompt: `"Hello! What can you help me with today?"`
- Live bot response (verbatim):
  > Botpress helps support teams use AI agents and an AI-native helpdesk so the bots handle most customer questions and humans jump in only when needed.
  > What are you mainly exploring today — building AI support agents, running a helpdesk for your team, or just getting a feel for what Botpress can do?
- Interpretation: full REST+SSE round-trip (create user → initialize conversation → send → message_created) completed live; the bot is reachable and responsive. **No scores, no bands, no rubric points asserted.**

## Coverage limitation (must remain on any downstream output)
Marketing/demo bot (Botpress self-deployed inbound-sales/product-QA agent on their own site). **Partial black-box coverage only**; no customer deployment, no governance evidence. P3.2 (indirect injection) and P3.3 (secret exfil) are N/A on this surface. **Do NOT present as a customer-deployment assessment or a bind recommendation.**

## FLAG 1 (CTO decision, blocks N=5) — scored-path provenance gap
The issue's scored command `python3 scripts/scan_botpress_com.py --out-dir <run-dir>` is described as "wired to the current v1.3.0 `live_probe` path with `k_replicates=5`", but the pinned script at `cc3299038` is the legacy BEAAA-202 partial script and does **not** meet the execution controls:
- Emits **none** of the required v1.3.0 provenance pins: no `scanner_version`, `corpus_version`, `model_versions`, or `seed` in its output record.
- Has **no** `k_replicates` / N=5 replication (single pass per item).
- Does its own ad-hoc `_rate_to_raw_*` mapping rather than calling the frozen v1.3.0 `score()`/`to_contract_output` path; references contract `@v1.0`, not `@v1.3.0`.
- Per the execution controls, "a result is incomplete unless every scored point has reachable evidence and the output carries the contract provenance pins" — so a `--out-dir` run as-is would be **non-compliant**.

**Options for CTO to choose:** (A) accept the legacy partial script's output for this Tier-1 marketing-bot item with an explicit "no v1.3.0 provenance pins / partial coverage" caveat; or (B) I wire the existing `BotpressCloudRunner` into the v1.3.0 `live_probe` scored path (`k_replicates=5`, provenance pins, frozen `score()`) as rule-execution implementation work, then run N=5. I recommend **(B)** to satisfy the execution controls. This is an implementation (no rubric/weight/judge/contract change); I will not proceed without your smoke clearance + path choice.

## FLAG 2 (glossary) — deprecated term in active script
`scripts/scan_botpress_com.py` uses the deprecated term "ARE" as a product/score name 8×. Surfacing to glossary review per Supreme Law; canonical term is **BackBond Scanner**. Not propagated into this receipt. Would be corrected as part of option (B).

## Disposition
**Blocked on CTO** (`88e68019-1cb6-4c4f-83e1-9fd340fb6821`). Named unblock actions:
1. Confirm smoke cleared (evidence above).
2. Choose scored-path option (A) or (B) so the N=5 run carries v1.3.0 provenance pins.
3. Authorize the N=5 scored run.

No score promotion, rubric change, contract change, or external claim was made from this issue.
