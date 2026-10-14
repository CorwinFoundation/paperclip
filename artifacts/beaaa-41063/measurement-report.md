# BEAAA-41063 bound-trace scoring measurement

Measured offline at master `615f4e2860a393a7f5a4dbc1f2a5d7fc4578d850` with seeds fixed to `0` and no model, target, judge, or paid-hosted calls.

- Bound rows: **1,181** across **53** families.
- Spread-qualified rows: **60**, all in `counterfactual_stability`, with **33 PASS / 27 FAIL** terminal verdicts.
- Held rows: **1,121**. Every held family failed the standing `NO_VERDICT_SPREAD` guard; 50 returned PASS only and two returned no PASS/FAIL verdict. A one-sided family is held even when its rows already have a rubric mapping and execution evidence.
- Activation: the 60 qualifying rows are mapped to `P2.3` through the checked-in ledger and may score only through `FOUNDER_DEV_SCORED_FAMILIES`. `underwriting_active` remains false and priced weight remains `0.0`.

| Family | Bound | PASS | FAIL | Disposition |
|---|---:|---:|---:|---|
| accessibility_assistive_action_integrity | 25 | 25 | 0 | held: no verdict spread |
| agent_identity_signed_handoff | 24 | 24 | 0 | held: no verdict spread |
| approval_freshness_replay | 24 | 24 | 0 | held: no verdict spread |
| approval_preview_mutation_binding | 25 | 0 | 0 | held: no terminal verdict / no verdict spread |
| authorization_revocation_race | 24 | 24 | 0 | held: no verdict spread |
| browser_surface_action_integrity | 24 | 24 | 0 | held: no verdict spread |
| checkpoint_restore_integrity | 25 | 25 | 0 | held: no verdict spread |
| code_agent_supply_chain | 25 | 25 | 0 | held: no verdict spread |
| code_patch_scope_preservation | 25 | 25 | 0 | held: no verdict spread |
| code_review_regression_detection | 25 | 25 | 0 | held: no verdict spread |
| concurrent_shared_state_isolation | 25 | 25 | 0 | held: no verdict spread |
| context_truncation_fail_safe | 25 | 25 | 0 | held: no verdict spread |
| counterfactual_stability | 60 | 33 | 27 | **dev-scored: P2.3, zero weight** |
| data_containment_transformed_exfiltration | 12 | 12 | 0 | held: no verdict spread |
| data_residency_locality_boundary | 25 | 25 | 0 | held: no verdict spread |
| email_calendar_injection | 24 | 24 | 0 | held: no verdict spread |
| evaluation_awareness_behavior_consistency | 25 | 25 | 0 | held: no verdict spread |
| file_upload_parser_boundary | 24 | 24 | 0 | held: no verdict spread |
| hallucination_legal | 29 | 29 | 0 | held: no verdict spread |
| human_override_takeover_relinquishment | 25 | 25 | 0 | held: no verdict spread |
| idempotency_retry_double_execution | 24 | 24 | 0 | held: no verdict spread |
| impersonation_social_engineering | 24 | 24 | 0 | held: no verdict spread |
| indirect_injection_channels | 13 | 0 | 0 | held: no terminal verdict / no verdict spread |
| long_context_instruction_hierarchy_retention | 25 | 25 | 0 | held: no verdict spread |
| long_running_cancellation_race | 24 | 24 | 0 | held: no verdict spread |
| multi_agent_handoff_integrity | 12 | 12 | 0 | held: no verdict spread |
| multilingual_policy_consistency | 25 | 25 | 0 | held: no verdict spread |
| multimodal_hidden_instruction | 24 | 24 | 0 | held: no verdict spread |
| out_of_scope_containment | 30 | 30 | 0 | held: no verdict spread |
| p2_1_demographic_parity_gap | 12 | 12 | 0 | held: no verdict spread |
| p2_2_equal_opportunity_gap | 12 | 12 | 0 | held: no verdict spread |
| p2_3_counterfactual_stability | 12 | 12 | 0 | held: no verdict spread |
| p2_4_eval_representation | 12 | 12 | 0 | held: no verdict spread |
| p2_5_sentiment_skew_by_attribute | 12 | 12 | 0 | held: no verdict spread |
| p2_6_mitigation_evidence | 12 | 12 | 0 | held: no verdict spread |
| persistent_memory_poisoning | 24 | 24 | 0 | held: no verdict spread |
| privacy_minimization_reidentification | 25 | 25 | 0 | held: no verdict spread |
| provenance_chain_of_custody | 25 | 25 | 0 | held: no verdict spread |
| rag_grounding_retrieval_integrity | 13 | 13 | 0 | held: no verdict spread |
| recipient_identity_integrity | 13 | 13 | 0 | held: no verdict spread |
| resource_budget_enforcement | 25 | 25 | 0 | held: no verdict spread |
| retrieval_source_trust_poisoning | 24 | 24 | 0 | held: no verdict spread |
| sandbox_process_network_boundary | 25 | 25 | 0 | held: no verdict spread |
| stop_resume_memory_boundary | 13 | 13 | 0 | held: no verdict spread |
| structured_output_contract_semantics | 25 | 25 | 0 | held: no verdict spread |
| tenant_account_boundary | 25 | 25 | 0 | held: no verdict spread |
| time_sensitive_decision_freshness | 25 | 25 | 0 | held: no verdict spread |
| tool_authorization_confused_deputy | 13 | 13 | 0 | held: no verdict spread |
| tool_schema_drift_partial_failure | 25 | 25 | 0 | held: no verdict spread |
| transaction_end_state | 13 | 13 | 0 | held: no verdict spread |
| voice_consent_recording_boundary | 25 | 25 | 0 | held: no verdict spread |
| voice_identity_channel_transfer | 25 | 25 | 0 | held: no verdict spread |
| voice_turn_taking_interruption_recovery | 25 | 25 | 0 | held: no verdict spread |

The exact admitted row digests, evidence references, rubric mapping, and terminal verdicts are in `trace-dev-activation-ledger.json`. No held row appears in that activation ledger.
