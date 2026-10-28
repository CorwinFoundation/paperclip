"""Lightweight identities shared by registration, execution and evidence review."""

RECORD_CASES = {
    "assessment_risk_traceability": "EVID-RISK-TRACE",
    "assessment_data_lineage": "EVID-DATA-LINEAGE",
    "assessment_evaluation_reconciliation": "EVID-EVAL-RESULTS",
    "assessment_subgroup_coverage": "EVID-SUBGROUP-COVERAGE",
    "assessment_change_comparison": "EVID-CHANGE-REGRESSION",
    "assessment_incident_closure": "EVID-INCIDENT-CLOSURE",
}
RECORD_FAMILIES = frozenset(RECORD_CASES)
LOG_CHALLENGE_FAMILY = "operational_logging_challenges"
FAMILIES = frozenset({"operational_oversight", "operational_logging",
                      "operational_logging_quality", LOG_CHALLENGE_FAMILY}) | RECORD_FAMILIES
