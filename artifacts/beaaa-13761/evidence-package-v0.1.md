---
artifact_type: closeout_receipt
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-13761 evidence package separates fetchable execution-layer code from still-blocked deployment and reference-agent validation."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-13761", "BEAAA-13801"]
proof_points_cited: ["branch beaaa-13761-execution-evidence", "artifacts/beaaa-13761/registry/current-canonical-registry.json", "artifacts/beaaa-13761/full-tier-controlled-dispatch/manifest.controlled-full-tier-dispatch-smoke.json", "Inspect AI official docs", "Garak official docs", "PyRIT official docs"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["BEAAA-13801 remains open; parent cannot close until deployment evidence lands", "Known-good/known-bad reference-agent bands are not proven by this heartbeat"]
---

# BEAAA-13761 Evidence Package v0.1

BLUF: this package makes the implementation fetchable and proves registry/dispatcher/worker plumbing in a clean branch, but it does not satisfy the CEO close gate. Deployment remains blocked by BEAAA-13801, Appendix A is missing from the repo checkout, and known-good/known-bad band validation is not complete.

## A. Branch And Files

- Branch: `beaaa-13761-execution-evidence`
- Base: `origin/master` at `ad1b172` when the worktree was created
- Commit: filled after push in issue comment
- Verification: `PYTHONPATH=. pytest -q tests/test_probe_registry.py::ProbeRegistryBaselineTest::test_beaaa13741_ops_loop_stability_row_is_executable tests/test_full_scan_executor_beaaa13681.py tests/test_canonical_test_registry_beaaa13762.py tests/test_framework_worker_registry_beaaa13682.py tests/test_loop_stability_budget_guard_beaaa13733.py tests/test_applicability.py tests/test_scoring.py` -> `76 passed in 20.26s`

Files added/changed in branch at artifact time:

- ` M api/full_scan_executor.py`
- ` M api/scan_handlers.py`
- ` M rubric/applicability_policy.yaml`
- ` M rubric/rubric.yaml`
- ` M rubric/scoring.py`
- ` M scanners/live_probe_orchestrator.py`
- ` M tests/fixtures/golden_agents/G1/applicability.json`
- ` M tests/fixtures/golden_agents/G2/applicability.json`
- ` M tests/fixtures/golden_agents/M1/applicability.json`
- ` M tests/fixtures/golden_agents/M2/applicability.json`
- ` M tests/fixtures/synthetic_eval_outputs.json`
- ` M tests/test_applicability.py`
- ` M tests/test_full_scan_executor_beaaa13681.py`
- ` M tests/test_probe_registry.py`
- ` M tests/test_scoring.py`
- ` M tools/active_test_registry.py`
- ` M tools/probe-registry/active_registry.json`
- ` M tools/probe_registry.py`
- `?? api/full_suite_manifest.py`
- `?? artifacts/beaaa-13761/`
- `?? harness/bench/loop_stability_budget_guard.py`
- `?? rubric/probe_mappings/hallusquatting_promptware.yaml`
- `?? tests/probe_sets/p5_hallusquatting_promptware_v1.jsonl`
- `?? tests/test_canonical_test_registry_beaaa13762.py`
- `?? tests/test_hallusquatting_promptware_beaaa13692.py`
- `?? tests/test_loop_stability_budget_guard_beaaa13733.py`
- `?? tools/canonical_test_registry.py`
- `?? tools/framework_workers.py`

## B. Registry

- Registry version: `scanner-test-registry-baseline-2026-07-13/v0.1`
- Registry row hash: `f4d3859d3b680b193fbf0edf4c7698399264d3d2434cb0f013cb8c5f248cd18b`
- Registry file SHA-256: `c65b3b9c6f81b34d9e784a4115f028c042d1862143a9fb4b68ecc3c0b16ab45d`
- Total registry rows: `474`
- Unique `production_active` executable cases: `286`
- Source mode: `probe_registry_fallback_appendix_a_source_missing`

Disposition counts:
- `executable_not_connected`: `101`
- `fixture_only`: `76`
- `needs_validation`: `7`
- `production_active`: `286`
- `roadmap`: `4`

Per-family active executable counts:
- `GOVERNANCE.PII.HANDLING_ATTESTATION`: `1`
- `OPS.LOOP_STABILITY.BUDGET_GUARD`: `1`
- `P1.1`: `44`
- `P1.2`: `6`
- `P1.3`: `29`
- `P1.4`: `21`
- `P1.5`: `21`
- `P1.6`: `1`
- `P2.1`: `3`
- `P2.2`: `1`
- `P2.3`: `1`
- `P2.4`: `1`
- `P2.5`: `6`
- `P2.6`: `1`
- `P3.1`: `34`
- `P3.2`: `25`
- `P3.3`: `19`
- `P3.5`: `4`
- `P3.6`: `5`
- `P4.1`: `1`
- `P4.2`: `1`
- `P4.3`: `1`
- `P4.4`: `1`
- `P4.5`: `1`
- `P4.6`: `1`
- `ROBUSTNESS.PII.SCAN_TIME_DETECTION`: `20`
- `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER`: `12`
- `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`: `24`

Appendix A reconciliation:
- The checkout does not contain `IFA/scanner-test-registry-baseline-2026-07-13.csv` or `backbond/operations/paperclip/paperclip_updates_2026-07-13/scanner-test-registry-baseline-2026-07-13.csv`; the registry emitted in fallback mode from in-repo probe/rubric/harness sources.
- Therefore the CEO-stated Appendix A target of 61 families / about 715 T1 cases is not reconciled in this heartbeat. The current honest repo-proven number is 286 unique production-active executable cases across 28 families in the canonical registry artifact, and 281 active executable rows in `tools/active_test_registry.py` used by the dispatcher manifest.

## C. Full-Tier Manifest

- Controlled target: `controlled.full-tier.dispatch-smoke.v0`
- Manifest hash: `sha256:327b87d206d0c25dfdeb88284c4abf8f772cff1668aec43ecc97a386671f808b`
- Expected: `281`
- Applicable: `281` (all active executable rows in `tools.active_test_registry`)
- Attempted: `281`
- Terminal: `281`
- Evidenced: `0`
- Release vanish gate passed: `True`

Divergence: all 281 active rows reached terminal `incomplete` because the controlled run used an empty worker map to prove fail-closed dispatcher behavior. This is useful plumbing evidence, not a measured behavioral pass.

## D. Reference Agents

- Known-good reference agent definitions: not delivered in this heartbeat.
- Known-bad reference agent definitions: not delivered in this heartbeat.
- Per-family expected bands: helper exists in `api/full_scan_executor.py::build_controlled_validation_report`, but no complete controlled-agent N=5 outputs are attached here.
- Delivery date: needs a child implementation task with real controlled agents; I am not simulating this evidence.

## E. OSS Reuse Survey And Budgets

- Inspect AI: reuse as the primary substrate for agentic/task evals where its task/solver/scorer model fits; official docs describe it as a broad eval framework for coding, agentic tasks, reasoning, knowledge, behavior, and multimodal understanding. Source: https://inspect.aisi.org.uk/
- Garak: reuse for vulnerability scanning families; official project/docs describe probes for hallucination, data leakage, prompt injection, misinformation, toxicity, jailbreaks, and related weaknesses. Sources: https://github.com/NVIDIA/garak and https://reference.garak.ai/
- PyRIT: reuse for red-team automation where attack orchestration is needed; official docs describe it as an automated and human-led red-teaming framework for generative AI systems. Source: https://azure.github.io/PyRIT/
- Promptfoo/AgentDojo/tau-bench/fairness custom/governance custom remain worker-specific adapters under the pinned worker registry; Inspect is not replacing them wholesale because BackBond needs per-family evidence schemas, failure-mode coverage, and deterministic fail-closed accounting across heterogeneous test assets.

Budget ceilings for the first measured controlled run:
- Max target calls: 281 cases x 1 replicate for dispatcher acceptance; N=5 band validation is separately capped at 281 x 5 = 1,405 target calls per reference agent before any judge fanout.
- Judge cost: $0 for dispatcher plumbing run; measured runs must predeclare judge model/version and cap judge invocations to one deterministic judge call per case unless a ticket explicitly approves ensemble-of-N.
- Wall clock: dispatcher acceptance target <= 2 hours for 281 cases with unavailable-worker fail-closed accounting; measured framework run target <= 8 hours per reference agent before quarantine/escalation.

## F. Deployment Sequence

- Deployment path: ADR-0057/operator-mediated push pipeline, no self-hosted production runner, no production-box egress pull, no manual app build on-box.
- Owner/action: `local-board` must execute BEAAA-13801, two operator-mediated push releases for `srv1749248` and post deploy records.
- Date: cannot be scheduled by CTO in this heartbeat because BEAAA-13801 is a board/operator task currently `todo` and unassigned to an agent.
- Parent disposition: block BEAAA-13761 on BEAAA-13801 until deploy records prove registry/worker versions are live.
