"""Lightweight identities shared by registration, execution and evidence review."""

LEGACY_RECORD_CASES = {
    "assessment_risk_traceability": "EVID-RISK-TRACE",
    "assessment_data_lineage": "EVID-DATA-LINEAGE",
    "assessment_evaluation_reconciliation": "EVID-EVAL-RESULTS",
    "assessment_subgroup_coverage": "EVID-SUBGROUP-COVERAGE",
    "assessment_change_comparison": "EVID-CHANGE-REGRESSION",
    "assessment_incident_closure": "EVID-INCIDENT-CLOSURE",
}
VALIDATION_VERSION = "assessment-evidence-validation@1"
VALIDATION_RECORD_CASES = {
    "assessment_risk_test_coverage": "EVID-RISK-TEST-COVERAGE",
    "assessment_mitigation_result_binding": "EVID-MITIGATION-RESULT-BINDING",
    "assessment_split_overlap": "EVID-SPLIT-OVERLAP",
    "assessment_subject_independence": "EVID-SUBJECT-INDEPENDENCE",
    "assessment_class_error_rates": "EVID-CLASS-ERROR-RATES",
    "assessment_statistical_support": "EVID-STATISTICAL-SUPPORT",
    "assessment_performance_claim_consistency": "EVID-PERFORMANCE-CLAIM-CONSISTENCY",
    "assessment_change_impact_coverage": "EVID-CHANGE-IMPACT-COVERAGE",
    "assessment_release_acceptance": "EVID-RELEASE-ACCEPTANCE",
}
VALIDATION_METHODS = {
    "EVID-RISK-TEST-COVERAGE": "Reconcile reviewed per-risk case requirements with bound original native test observations.",
    "EVID-MITIGATION-RESULT-BINDING": "Reconcile mitigation effectiveness and residual-risk decisions with original verification outcomes.",
    "EVID-SPLIT-OVERLAP": "Compare approved dataset partitions for overlapping content fingerprints and explicit shared-reference exceptions.",
    "EVID-SUBJECT-INDEPENDENCE": "Compare approved dataset partitions for prohibited shared subjects using the reviewed unit of independence.",
    "EVID-CLASS-ERROR-RATES": "Recompute binary classification error rates, abstentions and balanced accuracy against approved criteria.",
    "EVID-STATISTICAL-SUPPORT": "Recompute Wilson 95% binomial accuracy bounds under reviewed independent-observation assumptions.",
    "EVID-PERFORMANCE-CLAIM-CONSISTENCY": "Compare reviewed exact-match accuracy claims and limitations with the same release, dataset and conditions.",
    "EVID-CHANGE-IMPACT-COVERAGE": "Reconcile declared component changes, impact review and required regression observations.",
    "EVID-RELEASE-ACCEPTANCE": "Reconcile exact-snapshot release approval, regression outcomes, explicit exceptions and deployment chronology.",
}
VALIDATION_METRICS = frozenset({
    "required_observation_count", "missing_case_count", "failed_required_case_count", "uncovered_risk_count",
    "exception_count", "partition_count", "overlap_count", "allowed_reference_overlap_count", "shared_subject_count",
    "positive_count", "negative_count", "true_positive_count", "true_negative_count", "false_positive_count",
    "false_negative_count", "abstention_count", "false_positive_rate_ppm", "false_negative_rate_ppm",
    "positive_error_rate_ppm", "negative_error_rate_ppm", "balanced_accuracy_ppm", "independent_subject_count",
    "dependent_record_count", "wilson_lower_ppm", "wilson_upper_ppm", "interval_width_ppm",
    "claim_count", "unsupported_claim_count", "changed_component_count", "uncovered_change_count",
    "maximum_positive_error_ppm", "maximum_negative_error_ppm", "minimum_balanced_accuracy_ppm",
    "minimum_independent_count", "minimum_accuracy_lower_ppm", "maximum_interval_width_ppm",
    "maximum_overstatement_ppm",
    "missing_class_count", "unsupported_assumption_count", "unsupported_class_map_count",
})
LIFECYCLE_VERSION = "assessment-lifecycle-checks@1"
LIFECYCLE_RECORD_CASES = {
    "assessment_monitoring_coverage": "EVID-MONITORING-COVERAGE",
    "assessment_monitoring_trend": "EVID-MONITORING-TREND",
    "assessment_multiclass_metrics": "EVID-MULTICLASS-METRICS",
    "assessment_subgroup_uncertainty": "EVID-SUBGROUP-UNCERTAINTY",
    "assessment_governance_actions": "EVID-GOVERNANCE-ACTIONS",
    "assessment_competence_coverage": "EVID-COMPETENCE-COVERAGE",
}
LIFECYCLE_METHODS = {
    "EVID-MONITORING-COVERAGE": "Reconcile approved completed monitoring windows, result identity, freshness and required sample coverage.",
    "EVID-MONITORING-TREND": "Compare ordered, comparable monitoring accuracy with the first supplied period and approved regression criteria.",
    "EVID-MULTICLASS-METRICS": "Recompute multiclass confusion counts, per-class precision, recall and F1 with explicit abstention and denominator handling.",
    "EVID-SUBGROUP-UNCERTAINTY": "Recompute reviewed subgroup exact-match error rates and Wilson 95% accuracy intervals under explicit independence assumptions.",
    "EVID-GOVERNANCE-ACTIONS": "Reconcile required audit and management reviews, linked action ownership, deadlines and reviewed completion records.",
    "EVID-COMPETENCE-COVERAGE": "Reconcile the reviewed active-role roster with required courses and scoped, current competence completion records.",
}
LIFECYCLE_METRICS = frozenset({
    "required_window_count", "observed_window_count", "missing_window_count", "stale_window_count",
    "insufficient_window_count", "maximum_age_seconds", "minimum_window_count", "maximum_drop_ppm",
    "maximum_observed_drop_ppm", "regressed_window_count", "baseline_accuracy_ppm",
    "class_count", "abstention_count", "abstention_rate_ppm", "missing_denominator_count", "insufficient_class_count",
    "minimum_class_support", "minimum_precision_ppm", "minimum_recall_ppm", "minimum_f1_ppm",
    "maximum_abstention_ppm", "required_group_count", "insufficient_group_count", "dependent_record_count",
    "unsupported_assumption_count", "minimum_independent_count", "minimum_group_lower_ppm",
    "maximum_interval_width_ppm", "maximum_gap_ppm", "observed_group_gap_ppm",
    "required_review_count", "missing_review_count", "review_count", "action_count", "open_action_count",
    "overdue_action_count", "closed_action_count", "missing_evidence_count", "required_assignment_count",
    "covered_assignment_count", "missing_completion_count", "expired_completion_count",
}) | frozenset(
    f"class_{i}_{metric}" for i in range(16)
    for metric in ("support", "predicted", "true_positive", "false_positive", "false_negative", "abstention",
                   "precision_ppm", "recall_ppm", "f1_ppm")
) | frozenset(f"confusion_{i}_{j}" for i in range(16) for j in range(16)) | frozenset(
    f"group_{i}_{metric}" for i in range(16)
    for metric in ("count", "correct", "error_rate_ppm", "wilson_lower_ppm", "wilson_upper_ppm", "interval_width_ppm")
) | frozenset(f"window_{i}_{metric}" for i in range(366) for metric in ("count", "accuracy_ppm", "drop_ppm"))
ASSURANCE_VERSION = "assessment-assurance-checks@1"
ASSURANCE_RECORD_CASES = {
    "assessment_incident_notification": "EVID-INCIDENT-NOTIFICATION",
    "assessment_supplier_assurance": "EVID-SUPPLIER-ASSURANCE",
    "assessment_impact_assessment_coverage": "EVID-IMPACT-ASSESSMENT-COVERAGE",
}
ASSURANCE_METHODS = {
    "EVID-INCIDENT-NOTIFICATION": "Reconcile reviewed incident reporting decisions, escalation deadlines and original submission/delivery receipts.",
    "EVID-SUPPLIER-ASSURANCE": "Reconcile supplied component versions and digests with current supplier approvals, assigned responsibilities and agreement evidence.",
    "EVID-IMPACT-ASSESSMENT-COVERAGE": "Reconcile intended uses, affected groups, risks and supplied changes with current reviewed impact assessments and mitigation or acceptance decisions.",
}
ASSURANCE_METRICS = frozenset({
    "required_incident_count", "missing_incident_count", "reportable_incident_count", "exempt_incident_count",
    "required_notification_count", "delivered_notification_count", "missing_notification_count",
    "overdue_notification_count", "overdue_escalation_count", "maximum_submission_delay_seconds",
    "maximum_delivery_delay_seconds", "required_component_count", "missing_component_count",
    "approved_component_count", "supplier_count", "expired_review_count", "responsibility_count",
    "agreement_count", "required_context_count", "missing_context_count", "covered_context_count",
    "impact_risk_count", "mitigated_risk_count", "accepted_risk_count", "impact_change_count",
    "missing_evidence_count",
})
RECORD_CASES = {**LEGACY_RECORD_CASES, **VALIDATION_RECORD_CASES, **LIFECYCLE_RECORD_CASES, **ASSURANCE_RECORD_CASES}
RECORD_FAMILIES = frozenset(RECORD_CASES)
LOG_CHALLENGE_FAMILY = "operational_logging_challenges"
FAMILIES = frozenset({"operational_oversight", "operational_logging",
                      "operational_logging_quality", LOG_CHALLENGE_FAMILY}) | RECORD_FAMILIES
