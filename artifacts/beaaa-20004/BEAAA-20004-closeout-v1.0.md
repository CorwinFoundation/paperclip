---
artifact_type: implementation_evidence
owner: scanner_engineer_3
persona_audience: internal
phase: development
canonical_terms_only: true
focus_claim: "BEAAA-20004 adds deterministic, content-free metadata decisions for the synthetic voice lane without changing BackBond Scanner scoring or contract output."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-20004", "BEAAA-19989", "BEAAA-19572"]
proof_points_cited: ["determination/rules/voice_perils_cel_v1.json", "determination/voice_cel.py", "tests/test_beaaa20004_voice_cel.py", "artifacts/beaaa-20004/n5-calibration-delta.json"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-20004 text-only CEL voice peril rules

## BLUF

The pinned `voice-perils-cel@1.0.0` pack compiles four approved metadata-only peril intents and emits only pass, fail, or indeterminate with the pin on every result. A closed evaluator rejects unknown evidence fields and recursively rejects content-bearing keys before evaluation and before emission.

## Classification and scope

- Deliverable: a CEL rule-pack implementation for the CTO that produces deterministic synthetic-voice metadata determinations within 1.5 engineering days.
- Classification: rule-execution, classification-table row “implementing a probe within an existing rubric point.”
- Contract: `contracts/are-scanner-api-v1.md@v1.0`; output shape and scoring are unchanged.
- Scanner version: `0.5.0`.
- Corpus version: `0.1.0`.
- Probe-set-version: `voice-perils-cel@1.0.0`.
- Rubric point IDs: `VOICE.CONSEQUENTIAL_ACTION_CONFIRMATION`, `VOICE.PII_DISCLOSURE_SIGNAL`, `VOICE.AI_DISCLOSURE`, `VOICE.UNAUTHORIZED_TRANSFER`.
- CTO co-sign: not required for rule-execution.
- Upstream eval version: not applicable; no external eval or judge is used.

## Verification

Focused command:

`python3 -m pytest -q tests/test_beaaa20004_voice_cel.py`

Result: `119 passed in 0.71s`.

N=5 golden command:

`python3 scripts/release_gate_n5.py --baseline ee2abc0ac3a21b07b3ad790e6f7907dc268a595f --candidate e744422b9bb70882bc1e1db1be5c1eeadc46c264 --phase development --today 2026-07-31 --out-dir artifacts/beaaa-20004/n5 --json-out artifacts/beaaa-20004/n5-calibration-delta.json`

Result: PASS on the 8/9 baselined roster across seeds 0-4; B2 remains excluded by the standing BEAAA-10199 decision. Every total and pillar delta is `0`, and every candidate spread is `0`.

The command's report-writing phase returned nonzero after completing the gate because a relative `--out-dir` triggers a final `Path.relative_to` formatting error. Both JSON and Markdown reports were written completely; `passed: true`, `single_turn_delta_passed: true`, and `single_turn_spread_passed: true` are recorded in `n5-calibration-delta.json`.

## Boundary proof

- The pack contains only intent identifiers and CEL predicates over non-negative metadata counters.
- Missing required counters return indeterminate, never pass.
- The recursive negative corpus covers every forbidden key at depths 0-3 through both mappings and lists.
- Key normalization rejects punctuation/case variants such as `spoken-PII`.
- Outputs are closed to six allowlisted fields and contain `rule_pack_version` on every path.
- No audio model, cloning model, adversarial-audio logic, content reconstruction, LLM judge, pricing mapping, or billable mapping exists in this change.

## Rollback

Unregister `voice-perils-cel@1.0.0` and retain no voice determination. Do not substitute a proxy pass.

## FAL and self-audit

Focus: passed; the internal artifact supports the deterministic evidence layer. Alignment: passed; it makes no external product or coverage claim. Linkage: passed through the BackBond Scanner contract and evidence-completeness controls. Glossary, Carrier Rule, reproducibility, evidence, tamper-resistance, and contract-shape checks passed.
