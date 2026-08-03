# ADR-0069: Provisional report-only tau-bench support/CRM pilot

BLUF: Add three locally authored, deterministic support/CRM scenarios behind a standalone report-only evaluator. Do not register them or feed their verdicts into the BackBond Score.

- Ticket: BEAAA-20725
- Classification: rule-execution under `scanner-implementation-supervision-v1` because the change wraps an upstream method using existing Agent Interface and mock-tool rules without changing scoring or contract output.
- Contract anchor: `are-scanner-api-v1@v1.3.0`; no contract delta.
- Scanner version: `0.5.0`
- Corpus version: `tau_pilot_support_crm@0.1.0-provisional`
- Probe-set version: `tau-pilot-support-crm@0.1.0-provisional`
- Rubric point ID: none; report-only and unregistered.
- N=5 calibration delta: not applicable to score bands while this evaluator is report-only and disconnected from all scoring/registry dispatch. The focused seeds 0..4 determinism run is the operational N=5 evidence for this provisional family.
- CTO co-sign: not required for this rule-execution candidate. Any later scoring or registry activation is a separate gated change.

The evaluator preserves the complete transcript, mock-tool call log, authorization results, and final database state. Verdicts require both the exact expected state and exact expected call sequence, which prevents plausible transcript text from masking an incorrect action.

FAL audit: Focus passes because this is evidence infrastructure for bounded agent underwriting. Alignment passes because it makes no protection promise and cannot affect a score. Linkage passes through the existing BackBond Scanner evidence, reproducibility, and report-only vocabulary.

Registry integration and final identifiers remain blocked on BEAAA-20356. Rollback requires deleting `evals/provisional_tau_support_crm.py`, its pilot mapping and tests, this ADR, and the provenance note. There is no migration, persisted state, scoring delta, or runtime-default change.
