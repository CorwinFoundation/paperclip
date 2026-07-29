---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This evidence expands the candidate probe inventory without changing the BackBond Scanner scoring or runtime surface."
contracts_referenced: ["are-scanner-api-v1.md@v1.0", "scanner-implementation-supervision-v1.md@v1.0"]
ticket_refs: ["BEAAA-18738", "BEAAA-18739", "BEAAA-18740", "BEAAA-18741", "BEAAA-18742"]
proof_points_cited: []
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["No rule change, contract-shape change, tamper-resistance change, external-bound asset, or promotion."]
---

# BEAAA-18720 explicit content reconciliation

**BLUF:** Four accepted explicit catalogs add exactly 500 behaviorally distinct cases to the byte-preserved 1,500-row parent, producing a validated 2,000-row candidate inventory across 87 families.

## Exact reconciliation

- Parent rows: 1,500
- Parent SHA-256: `67dd7bb6c65c28dcc633e5b169c30b5ca0ac7d84fdfe5cef4e3540a7de56866e`
- Accepted catalog rows: `{"BEAAA-18739": 125, "BEAAA-18740": 125, "BEAAA-18741": 125, "BEAAA-18742": 125}`
- Accepted catalog SHA-256: `{"BEAAA-18739": "c8d7b56cf922e467381f81f8cb5908a9c631bfbf960731128ef0d1befabe3c8f", "BEAAA-18740": "787943e3c8d12563d9d19d6ff748a2efd067bd0daf5cfef44311a8b96e32a6c9", "BEAAA-18741": "bd318eeb605586764dad5bfd2367babfb39e96220a139c4204e612f49ba53187", "BEAAA-18742": "35d1ff32e25b07f5c9e60a7dd8f6defa06aa1bac4c5e255bc99a66252da9ef67"}`
- Added rows: 500
- Unified rows: 2,000
- Semantic-overlap ledger rows: 2,000
- Unified families: 87
- Unique stable IDs/content hashes/semantic fingerprints: 2,000 / 2,000 / 2,000
- Unique normalized added-case skeletons: 500
- Repeated normalized added-case skeletons: 0
- Parent/new pairs >=0.50: 0
- Maximum parent/new normalized-token similarity: `0.4348` (bb18741.sandbox_process_network_boundary.11 vs bb18717.file_upload_parser_boundary.07)
- New/new pairs >=0.50 investigated: 85
- Maximum new/new normalized-token similarity: `0.6333` (bb18739.multilingual_policy_consistency.01 vs bb18739.multilingual_policy_consistency.02)
- Unexplained near-overlap pairs: 0

## Added family counts

```json
{
  "accessibility_assistive_action_integrity": 25,
  "approval_preview_mutation_binding": 25,
  "checkpoint_restore_integrity": 25,
  "code_patch_scope_preservation": 25,
  "code_review_regression_detection": 25,
  "concurrent_shared_state_isolation": 25,
  "context_truncation_fail_safe": 25,
  "data_residency_locality_boundary": 25,
  "evaluation_awareness_behavior_consistency": 25,
  "human_override_takeover_relinquishment": 25,
  "long_context_instruction_hierarchy_retention": 25,
  "multilingual_policy_consistency": 25,
  "provenance_chain_of_custody": 25,
  "resource_budget_enforcement": 25,
  "sandbox_process_network_boundary": 25,
  "structured_output_contract_semantics": 25,
  "time_sensitive_decision_freshness": 25,
  "voice_consent_recording_boundary": 25,
  "voice_identity_channel_transfer": 25,
  "voice_turn_taking_interruption_recovery": 25
}
```

## Unified family counts

```json
{
  "accessibility_assistive_action_integrity": 25,
  "agent_identity_signed_handoff": 24,
  "ai_disclosure": 12,
  "approval_freshness_replay": 24,
  "approval_preview_mutation_binding": 25,
  "authorization_revocation_race": 24,
  "browser_surface_action_integrity": 24,
  "cbrn_refusal": 20,
  "checkpoint_restore_integrity": 25,
  "citation_integrity_legal": 80,
  "code_agent_supply_chain": 25,
  "code_patch_scope_preservation": 25,
  "code_review_regression_detection": 25,
  "concurrent_shared_state_isolation": 25,
  "confidence_calibration": 100,
  "consequential_action_confirmation": 15,
  "context_truncation_fail_safe": 25,
  "counterfactual_stability": 60,
  "credential_secret_output_log_handling": 12,
  "cross_session_data_leak": 20,
  "customer_defined_risk_pack": 12,
  "cyber_misuse_refusal": 20,
  "data_containment_transformed_exfiltration": 12,
  "data_residency_locality_boundary": 25,
  "email_calendar_injection": 24,
  "evaluation_awareness_behavior_consistency": 25,
  "file_upload_parser_boundary": 24,
  "hallucination_legal": 125,
  "harmful_output_golden": 30,
  "human_escalation_flagging": 20,
  "human_override_takeover_relinquishment": 25,
  "idempotency_retry_double_execution": 24,
  "impersonation_social_engineering": 24,
  "indirect_injection": 10,
  "indirect_injection_channels": 13,
  "insecure_code_generation": 20,
  "long_context_instruction_hierarchy_retention": 25,
  "long_running_cancellation_race": 24,
  "mcp_tool_integrity": 12,
  "multi_agent_handoff_integrity": 12,
  "multilingual_policy_consistency": 25,
  "multimodal_hidden_instruction": 24,
  "out_of_scope_containment": 30,
  "p1_1_factuality_golden": 30,
  "p1_3_consistency_golden": 20,
  "p1_4_refusal_golden": 20,
  "p1_5_calibration_golden": 20,
  "p2_1_demographic_parity_gap": 12,
  "p2_2_equal_opportunity_gap": 12,
  "p2_3_counterfactual_stability": 12,
  "p2_4_eval_representation": 12,
  "p2_5_sentiment_skew_by_attribute": 12,
  "p2_6_mitigation_evidence": 12,
  "p3_2_indirect_injection_rob": 12,
  "p3_3_secret_exfiltration_rob": 12,
  "p3_5_multiturn_jailbreak": 20,
  "p3_6_output_attack": 20,
  "peril_data_containment": 6,
  "peril_recipient_integrity": 5,
  "peril_tool_grounded_factuality": 13,
  "persistent_memory_poisoning": 24,
  "pii_scan_time_detection": 20,
  "privacy_minimization_reidentification": 25,
  "provenance_chain_of_custody": 25,
  "rag_grounding_retrieval_integrity": 13,
  "recipient_identity_integrity": 13,
  "resource_budget_enforcement": 25,
  "retrieval_source_trust_poisoning": 24,
  "sandbox_process_network_boundary": 25,
  "secret_exfil": 13,
  "self_contradiction_pairs": 60,
  "stop_instruction_v1": 1,
  "stop_resume_memory_boundary": 13,
  "structured_output_contract_semantics": 25,
  "system_prompt_extraction": 20,
  "tenant_account_boundary": 25,
  "time_sensitive_decision_freshness": 25,
  "tool_authorization_confused_deputy": 13,
  "tool_misuse_param_tamper": 12,
  "tool_misuse_scope_escape": 12,
  "tool_misuse_unauthorized_call": 12,
  "tool_schema_drift_partial_failure": 25,
  "transaction_end_state": 13,
  "voice_adversarial_pack": 12,
  "voice_consent_recording_boundary": 25,
  "voice_identity_channel_transfer": 25,
  "voice_turn_taking_interruption_recovery": 25
}
```

## Substantive clusters per added family

```json
{
  "accessibility_assistive_action_integrity": 24,
  "approval_preview_mutation_binding": 25,
  "checkpoint_restore_integrity": 25,
  "code_patch_scope_preservation": 24,
  "code_review_regression_detection": 23,
  "concurrent_shared_state_isolation": 25,
  "context_truncation_fail_safe": 25,
  "data_residency_locality_boundary": 23,
  "evaluation_awareness_behavior_consistency": 25,
  "human_override_takeover_relinquishment": 25,
  "long_context_instruction_hierarchy_retention": 25,
  "multilingual_policy_consistency": 24,
  "provenance_chain_of_custody": 25,
  "resource_budget_enforcement": 24,
  "sandbox_process_network_boundary": 21,
  "structured_output_contract_semantics": 25,
  "time_sensitive_decision_freshness": 25,
  "voice_consent_recording_boundary": 24,
  "voice_identity_channel_transfer": 25,
  "voice_turn_taking_interruption_recovery": 25
}
```

## Controls per added family

```json
{
  "accessibility_assistive_action_integrity": {
    "negative": 18,
    "positive": 7
  },
  "approval_preview_mutation_binding": {
    "negative": 21,
    "positive": 4
  },
  "checkpoint_restore_integrity": {
    "negative": 20,
    "positive": 5
  },
  "code_patch_scope_preservation": {
    "negative": 18,
    "positive": 7
  },
  "code_review_regression_detection": {
    "negative": 21,
    "positive": 4
  },
  "concurrent_shared_state_isolation": {
    "negative": 21,
    "positive": 4
  },
  "context_truncation_fail_safe": {
    "negative": 20,
    "positive": 5
  },
  "data_residency_locality_boundary": {
    "negative": 20,
    "positive": 5
  },
  "evaluation_awareness_behavior_consistency": {
    "negative": 20,
    "positive": 5
  },
  "human_override_takeover_relinquishment": {
    "negative": 20,
    "positive": 5
  },
  "long_context_instruction_hierarchy_retention": {
    "negative": 20,
    "positive": 5
  },
  "multilingual_policy_consistency": {
    "negative": 16,
    "positive": 9
  },
  "provenance_chain_of_custody": {
    "negative": 18,
    "positive": 7
  },
  "resource_budget_enforcement": {
    "negative": 21,
    "positive": 4
  },
  "sandbox_process_network_boundary": {
    "negative": 21,
    "positive": 4
  },
  "structured_output_contract_semantics": {
    "negative": 21,
    "positive": 4
  },
  "time_sensitive_decision_freshness": {
    "negative": 20,
    "positive": 5
  },
  "voice_consent_recording_boundary": {
    "negative": 19,
    "positive": 6
  },
  "voice_identity_channel_transfer": {
    "negative": 18,
    "positive": 7
  },
  "voice_turn_taking_interruption_recovery": {
    "negative": 19,
    "positive": 6
  }
}
```

## Design-partner profiles

```json
{
  "browser_computer_use_agent": 10,
  "customer_support_agent": 159,
  "developer_coding_agent": 111,
  "enterprise_rag_assistant": 64,
  "generic_chat_assistant": 6,
  "multi_agent_orchestrator": 82,
  "multimodal_document_agent": 54,
  "transactional_action_agent": 129,
  "voice_realtime_agent": 75
}
```

## Capabilities and applicability

```json
{
  "capabilities": {
    "accessibility_assistive_action_integrity": 25,
    "approval_binding": 25,
    "checkpoint_restore": 25,
    "code_patch_generation": 25,
    "code_review": 25,
    "concurrent_state": 25,
    "context_budget": 1,
    "context_compaction": 3,
    "context_integrity": 11,
    "context_rehydration": 4,
    "delegated_context": 3,
    "document_integrity": 3,
    "evaluation_context_handling": 24,
    "evaluation_replay": 1,
    "freshness_authorization": 2,
    "freshness_disclosure": 1,
    "freshness_events": 2,
    "freshness_evidence": 11,
    "freshness_policy": 3,
    "freshness_provenance": 3,
    "freshness_time": 3,
    "human_takeover": 25,
    "long_context": 11,
    "long_context_actions": 2,
    "long_context_code": 1,
    "long_context_documents": 3,
    "long_context_tools": 2,
    "multilingual_context": 1,
    "multilingual_policy_consistency": 25,
    "persistent_memory": 1,
    "provenance_annotation": 1,
    "provenance_capture": 1,
    "provenance_chain": 8,
    "provenance_citation": 1,
    "provenance_export": 3,
    "provenance_handoff": 1,
    "provenance_ledger": 1,
    "provenance_lifecycle": 1,
    "provenance_signature": 2,
    "provenance_storage": 1,
    "provenance_transform": 4,
    "provenance_web": 1,
    "residency_controls": 25,
    "resource_budgeting": 25,
    "retrieval_integrity": 2,
    "sandbox_isolation": 25,
    "structured_output": 25,
    "temporal_context": 1,
    "tool_schema": 1,
    "voice_consent_recording_boundary": 25,
    "voice_identity_channel_transfer": 25,
    "voice_turn_taking_interruption_recovery": 25
  },
  "applicability": {
    "applicable_when_capability_declared": 500
  }
}
```

Every added case is schema-complete, evidence-bearing, deterministic, profile-declared, `reported_not_scored`, and N/A-never-PASS. Near-overlap dispositions preserve distinct event sequences, expected observations, failure predicates, and oracle fields.

## Scope

- Contract: `are-scanner-api-v1.md@v1.0`
- Classification: rule-execution, content-only
- Scanner runtime, registry, scoring, mappings, CI, GitHub, deployment, production, and published counts: unchanged
- Validation: `python3 tools/validate_beaaa18720_content.py`
