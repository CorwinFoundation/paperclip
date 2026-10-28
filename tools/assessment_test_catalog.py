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
RECORD_CASES = {**LEGACY_RECORD_CASES, **VALIDATION_RECORD_CASES}
RECORD_FAMILIES = frozenset(RECORD_CASES)
LOG_CHALLENGE_FAMILY = "operational_logging_challenges"
FAMILIES = frozenset({"operational_oversight", "operational_logging",
                      "operational_logging_quality", LOG_CHALLENGE_FAMILY}) | RECORD_FAMILIES
