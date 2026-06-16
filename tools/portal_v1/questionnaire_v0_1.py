"""BackBond governance questionnaire v0.1 schema (BEAAA-4902 -> BEAAA-4919).

Source artifact: BEAAA-4902 work product `953f1946-96aa-4492-83a1-90480b086feb`
(BEAAA-4902_governance_questionnaire_v0.1.md). This module is the executable
counterpart used by tools/portal_v1/portal.py to render the customer form,
validate submissions, build the submitted payload (control_id + schema_path
preserved), build a scanner-ready evidence bundle, and render the printable PDF.

Boundary (carried forward from BEAAA-4902): no scoring-math change, no
are-scanner-api-v1 output shape change, no new rubric points. This schema only
packages existing P4.x and GOVERNANCE.PII.HANDLING_ATTESTATION evidence.
"""

from __future__ import annotations

from typing import Any

QUESTIONNAIRE_VERSION = "0.1"
QUESTIONNAIRE_PARENT_TICKET = "BEAAA-4902"
QUESTIONNAIRE_IMPL_TICKET = "BEAAA-4919"

DEFAULT_EVIDENCE_STATES = ["not_implemented", "implemented_no_evidence", "implemented_with_evidence"]
PRODUCTION_STATUSES = ["not_deployed", "pilot", "production", "retired"]
PREFILL_SOURCES_ALLOWED = {
    "account_profile",
    "scanner_submission",
    "log_connector",
    "log_connector_and_policy",
    "source_connector",
    "source_and_deployment_connector",
    "deployment_connector",
    "incident_tooling",
    "policy_repository",
    "policy_and_incident_tooling",
    "system",
    "customer",
}

HEADER_INTRO = (
    "BackBond uses this questionnaire to confirm governance evidence for the AI "
    "agent you want rated and insured. Answer for the specific deployed agent, "
    "not the company as a whole. If a control is not implemented, select "
    "'not implemented' rather than describing future plans."
)

ACCOUNTABILITY_STATEMENT = (
    "I have authority to submit these attestations for this agent and understand "
    "BackBond may rely on them for underwriting, pricing, coverage eligibility, "
    "renewal review, and claim evidence."
)

HEADER_FIELDS: list[dict[str, Any]] = [
    {"key": "legal_entity_name", "label": "Legal entity name", "control": "text", "required": True, "prefill_source": "account_profile"},
    {"key": "customer_id", "label": "Customer ID", "control": "readonly", "required": True, "prefill_source": "account_profile"},
    {"key": "agent_id", "label": "Agent ID", "control": "readonly", "required": True, "prefill_source": "scanner_submission"},
    {"key": "agent_name", "label": "Agent name", "control": "text", "required": True, "prefill_source": "scanner_submission"},
    {"key": "production_status", "label": "Production status", "control": "enum", "options": PRODUCTION_STATUSES, "required": True, "prefill_source": "customer"},
    {"key": "attested_by_name", "label": "Attested by name", "control": "text", "required": True, "prefill_source": "customer"},
    {"key": "attested_by_title", "label": "Attested by title", "control": "text", "required": True, "prefill_source": "customer"},
    {"key": "attested_by_email", "label": "Attested by email", "control": "email", "required": True, "prefill_source": "customer"},
    {"key": "attestation_date", "label": "Attestation date", "control": "date", "required": True, "prefill_source": "system"},
    {"key": "accountability_statement", "label": "Accountability statement", "control": "accountability", "required": True, "prefill_source": "customer"},
]

# Evidence overlay (GOVERNANCE.PII.HANDLING_ATTESTATION) from the v0.1 artifact.
# This is NOT a separate question block in v0; it is rendered as evidence
# prompts attached to the referenced questions.
PII_OVERLAY: list[dict[str, Any]] = [
    {
        "label": "Data Processing Addendum or equivalent customer data terms",
        "attach_to_questions": [29],
        "required_when": "agent_processes_customer_pii",
    },
    {
        "label": "Retention and deletion SLA for agent logs and derived artifacts",
        "attach_to_questions": [2, 5],
        "required_when": "always",
    },
    {
        "label": "DSAR or deletion request workflow owner",
        "attach_to_questions": [29, 30],
        "required_when": "agent_processes_customer_pii",
    },
    {
        "label": "PII redaction/tokenization description for log samples",
        "attach_to_questions": [5],
        "required_when": "always",
    },
    {
        "label": "Incident response steps for data leak or wrong-recipient event",
        "attach_to_questions": [17, 18, 19],
        "required_when": "always",
    },
]

# ---------------------------------------------------------------------------
# Question schema. Each question carries:
#   number, control_id, section, prompt, prefill_source label, evidence prompt,
#   schema_paths (dict of value_key -> dotted path that the customer payload
#   must place each captured value under), and an `inputs` list describing the
#   form controls. Evidence schema_path is the dotted path where the resulting
#   evidence_refs land in the customer payload.
# ---------------------------------------------------------------------------

SECTIONS: list[dict[str, Any]] = [
    {"id": "P4_1", "title": "P4.1 Audit-Log Completeness"},
    {"id": "P4_2", "title": "P4.2 Model and Prompt Version Control"},
    {"id": "P4_3", "title": "P4.3 Kill Switch and Rollback"},
    {"id": "P4_4", "title": "P4.4 Incident-Response Playbook"},
    {"id": "P4_5", "title": "P4.5 Eval Re-Run Cadence"},
    {"id": "P4_6", "title": "P4.6 Owner Accountability Matrix"},
]


def _q(
    number: int,
    section: str,
    control_id: str,
    prompt: str,
    inputs: list[dict[str, Any]],
    schema_paths: dict[str, str],
    *,
    evidence_label: str | None,
    evidence_schema_path: str | None,
    prefill_source: str,
    char_cap: int = 500,
) -> dict[str, Any]:
    return {
        "number": number,
        "section": section,
        "control_id": control_id,
        "prompt": prompt,
        "inputs": inputs,
        "schema_paths": schema_paths,
        "evidence_label": evidence_label,
        "evidence_schema_path": evidence_schema_path,
        "prefill_source": prefill_source,
        "char_cap": char_cap,
    }


QUESTIONS: list[dict[str, Any]] = [
    # ---------- P4.1 Audit-Log Completeness ----------
    _q(
        1, "P4_1", "P4.1.Q01",
        "Which agent surfaces are logged for this deployment?",
        [{
            "key": "logged_surfaces",
            "type": "checklist",
            "options": ["prompt_in", "model_response", "tool_call", "tool_response", "user_id_or_session"],
            "required": True,
        }],
        {"logged_surfaces": "dims.P4_1.logged_surfaces[].surface"},
        evidence_label=None, evidence_schema_path=None,
        prefill_source="log_connector",
    ),
    _q(
        2, "P4_1", "P4.1.Q02",
        "For each logged surface, how many days are records retained?",
        [{
            "key": "retention_days_by_surface",
            "type": "int_map_by_selection",
            "source_question_number": 1,
            "source_field_key": "logged_surfaces",
            "min": 0, "max": 36500,
            "required": True,
        }],
        {"retention_days_by_surface": "dims.P4_1.logged_surfaces[].retention_days"},
        evidence_label="retention policy document",
        evidence_schema_path="dims.P4_1.evidence.retention_policy_doc",
        prefill_source="log_connector_and_policy",
    ),
    _q(
        3, "P4_1", "P4.1.Q03",
        "What storage mode protects the audit log after write?",
        [{
            "key": "storage_mode",
            "type": "enum",
            "options": ["append_only", "mutable_with_audit", "mutable_no_audit"],
            "required": True,
        }],
        {"storage_mode": "dims.P4_1.storage_mode"},
        evidence_label="log architecture note or vendor setting export",
        evidence_schema_path="dims.P4_1.evidence.storage_mode_doc",
        prefill_source="log_connector",
    ),
    _q(
        4, "P4_1", "P4.1.Q04",
        "What tamper-evidence mechanism is used for audit-log exports?",
        [{
            "key": "tamper_evidence",
            "type": "enum",
            "options": ["hash_chain", "wormstorage", "signed_export", "none"],
            "required": True,
        }],
        {"tamper_evidence": "dims.P4_1.tamper_evidence"},
        evidence_label="sample signed export or hash-chain manifest",
        evidence_schema_path="dims.P4_1.evidence.log_sample_file",
        prefill_source="log_connector",
    ),
    _q(
        5, "P4_1", "P4.1.Q05",
        "How is PII handled in logs before BackBond receives any sample?",
        [{
            "key": "pii_handling",
            "type": "enum",
            "options": ["redacted_mirror", "tokenized", "raw_with_access_control", "raw_no_control"],
            "required": True,
        }],
        {"pii_handling": "dims.P4_1.pii_handling"},
        evidence_label="scrubbed one-calendar-day log slice with at least 100 records",
        evidence_schema_path="dims.P4_1.evidence.log_sample_file",
        prefill_source="log_connector",
    ),

    # ---------- P4.2 Model and Prompt Version Control ----------
    _q(
        6, "P4_2", "P4.2.Q01",
        "Where is the production model and prompt version registry maintained?",
        [{
            "key": "version_registry",
            "type": "enum",
            "options": ["external_managed", "internal_git", "internal_other", "none"],
            "required": True,
        }, {
            "key": "registry_link",
            "type": "text",
            "required": False,
        }],
        {
            "version_registry": "dims.P4_2.version_registry",
            "registry_link": "dims.P4_2.evidence.change_log_link",
        },
        evidence_label="registry link",
        evidence_schema_path="dims.P4_2.evidence.change_log_link",
        prefill_source="source_connector",
    ),
    _q(
        7, "P4_2", "P4.2.Q02",
        "How are model and prompt versions pinned in production?",
        [{
            "key": "pin_policy",
            "type": "enum",
            "options": ["pinned_per_release", "pinned_per_call", "latest_floating", "no_policy"],
            "required": True,
        }],
        {"pin_policy": "dims.P4_2.pin_policy"},
        evidence_label="pinning policy document",
        evidence_schema_path="dims.P4_2.evidence.pin_policy_doc",
        prefill_source="source_connector",
    ),
    _q(
        8, "P4_2", "P4.2.Q03",
        "What approval is required before changing the system prompt?",
        [{
            "key": "prompt_change_control",
            "type": "enum",
            "options": ["pr_review_required", "single_approver", "no_control"],
            "required": True,
        }],
        {"prompt_change_control": "dims.P4_2.prompt_change_control"},
        evidence_label="change-control excerpt",
        evidence_schema_path="dims.P4_2.evidence.prompt_change_control_doc",
        prefill_source="source_connector",
    ),
    _q(
        9, "P4_2", "P4.2.Q04",
        "What approval is required before changing the primary model or model route?",
        [{
            "key": "model_change_control",
            "type": "enum",
            "options": ["pr_review_required", "single_approver", "no_control"],
            "required": True,
        }],
        {"model_change_control": "dims.P4_2.model_change_control"},
        evidence_label="change-control excerpt",
        evidence_schema_path="dims.P4_2.evidence.model_change_control_doc",
        prefill_source="source_connector",
    ),
    _q(
        10, "P4_2", "P4.2.Q05",
        "Can the deployed agent be rolled back to the prior known-good model and prompt?",
        [{
            "key": "rollback_to_prior_supported",
            "type": "boolean",
            "required": True,
        }, {
            "key": "last_3_releases",
            "type": "release_records",
            "max_rows": 3,
            "required": False,
        }],
        {
            "rollback_to_prior_supported": "dims.P4_2.rollback_to_prior_supported",
            "last_3_releases": "dims.P4_2.evidence.last_3_releases[]",
        },
        evidence_label="last 3 release records with release_id, deployed_at, model_id, and prompt_sha256",
        evidence_schema_path="dims.P4_2.evidence.last_3_releases_doc",
        prefill_source="source_and_deployment_connector",
    ),

    # ---------- P4.3 Kill Switch and Rollback ----------
    _q(
        11, "P4_3", "P4.3.Q01",
        "What mechanism disables the agent or removes production traffic?",
        [{
            "key": "kill_switch_mechanism",
            "type": "enum",
            "options": ["feature_flag", "config_toggle", "infra_circuit_breaker", "manual_redeploy", "none"],
            "required": True,
        }, {
            "key": "runbook_link",
            "type": "text",
            "required": False,
        }],
        {
            "kill_switch_mechanism": "dims.P4_3.kill_switch_mechanism",
            "runbook_link": "dims.P4_3.evidence.runbook_link",
        },
        evidence_label="kill-switch runbook",
        evidence_schema_path="dims.P4_3.evidence.runbook_doc",
        prefill_source="deployment_connector",
    ),
    _q(
        12, "P4_3", "P4.3.Q02",
        "After a disable decision, how many minutes until production traffic is off?",
        [{
            "key": "time_to_disable_minutes",
            "type": "integer",
            "min": 0, "max": 100000,
            "required": True,
        }],
        {"time_to_disable_minutes": "dims.P4_3.time_to_disable_minutes"},
        evidence_label="drill log if tested",
        evidence_schema_path="dims.P4_3.evidence.drill_log",
        prefill_source="deployment_connector",
    ),
    _q(
        13, "P4_3", "P4.3.Q03",
        "How many minutes are required to roll back to the prior known-good model and prompt?",
        [{
            "key": "rollback_time_minutes",
            "type": "integer",
            "min": 0, "max": 100000,
            "required": True,
        }],
        {"rollback_time_minutes": "dims.P4_3.rollback_time_minutes"},
        evidence_label="rollback procedure or deployment record",
        evidence_schema_path="dims.P4_3.evidence.rollback_procedure_doc",
        prefill_source="deployment_connector",
    ),
    _q(
        14, "P4_3", "P4.3.Q04",
        "When was the kill-switch or rollback procedure last drilled?",
        [{
            "key": "last_drill_at",
            "type": "date_or_never",
            "required": True,
        }, {
            "key": "drill_cadence_days",
            "type": "integer",
            "min": 0, "max": 36500,
            "required": False,
        }],
        {
            "last_drill_at": "dims.P4_3.last_drill_at",
            "drill_cadence_days": "dims.P4_3.drill_cadence_days",
        },
        evidence_label="drill log",
        evidence_schema_path="dims.P4_3.evidence.drill_log",
        prefill_source="incident_tooling",
    ),
    _q(
        15, "P4_3", "P4.3.Q05",
        "Who can invoke the kill switch during an active incident?",
        [{
            "key": "approval_required_to_invoke",
            "type": "enum",
            "options": ["on_call_unilateral", "single_approver", "dual_approver", "exec_approval", "no_policy"],
            "required": True,
        }],
        {"approval_required_to_invoke": "dims.P4_3.approval_required_to_invoke"},
        evidence_label="runbook approval section",
        evidence_schema_path="dims.P4_3.evidence.runbook_approval_section",
        prefill_source="incident_tooling",
    ),

    # ---------- P4.4 Incident-Response Playbook ----------
    _q(
        16, "P4_4", "P4.4.Q01",
        "Does the agent have an incident-response playbook that specifically covers AI-agent failure modes?",
        [{
            "key": "playbook_exists",
            "type": "boolean",
            "required": True,
        }],
        {"playbook_exists": "dims.P4_4.playbook_exists"},
        evidence_label="playbook document",
        evidence_schema_path="dims.P4_4.evidence.playbook_doc",
        prefill_source="policy_repository",
    ),
    _q(
        17, "P4_4", "P4.4.Q02",
        "Which incident types are covered by the playbook?",
        [{
            "key": "scope_covered",
            "type": "checklist",
            "options": [
                "prompt_injection", "hallucinated_action", "data_leak", "tool_misuse",
                "bias_complaint", "regulatory_inquiry", "runaway_loop", "wrong_recipient",
            ],
            "required": True,
        }],
        {"scope_covered": "dims.P4_4.scope_covered[]"},
        evidence_label="playbook section references",
        evidence_schema_path="dims.P4_4.evidence.playbook_section_refs",
        prefill_source="policy_repository",
    ),
    _q(
        18, "P4_4", "P4.4.Q03",
        "What is the escalation chain for a suspected AI-agent incident?",
        [{
            "key": "escalation_chain",
            "type": "escalation_chain",
            "max_rows": 10,
            "required": True,
        }],
        {"escalation_chain": "dims.P4_4.escalation_chain[]"},
        evidence_label="playbook escalation table",
        evidence_schema_path="dims.P4_4.evidence.escalation_table",
        prefill_source="incident_tooling",
    ),
    _q(
        19, "P4_4", "P4.4.Q04",
        "What external customer notification policy applies to this agent?",
        [{
            "key": "external_notification_policy",
            "type": "enum",
            "options": ["customer_within_24h", "customer_within_72h", "no_policy"],
            "required": True,
        }],
        {"external_notification_policy": "dims.P4_4.external_notification_policy"},
        evidence_label="notification policy excerpt",
        evidence_schema_path="dims.P4_4.evidence.notification_policy_doc",
        prefill_source="policy_repository",
    ),
    _q(
        20, "P4_4", "P4.4.Q05",
        "When was the playbook last revised and when was it last table-topped?",
        [{
            "key": "last_revision_at",
            "type": "date",
            "required": True,
        }, {
            "key": "last_tabletop_at",
            "type": "date_or_never",
            "required": True,
        }],
        {
            "last_revision_at": "dims.P4_4.evidence.last_revision_at",
            "last_tabletop_at": "dims.P4_4.evidence.last_tabletop_at",
        },
        evidence_label="tabletop summary if any",
        evidence_schema_path="dims.P4_4.evidence.tabletop_artifact",
        prefill_source="policy_and_incident_tooling",
    ),

    # ---------- P4.5 Eval Re-Run Cadence ----------
    _q(
        21, "P4_5", "P4.5.Q01",
        "Confirm the eval coverage ledger that applies to this agent.",
        [{
            "key": "eval_coverage_ledger_id",
            "type": "ledger_ref",
            "required": True,
        }, {
            "key": "submitted_at",
            "type": "date",
            "required": True,
        }, {
            "key": "customer_confirmed",
            "type": "boolean",
            "required": True,
        }],
        {
            "eval_coverage_ledger_id": "ledger_ref.eval_coverage_ledger_id",
            "submitted_at": "ledger_ref.submitted_at",
            "customer_confirmed": "ledger_ref.customer_confirmed",
        },
        evidence_label="ledger reference",
        evidence_schema_path="ledger_ref.evidence.ledger_artifact",
        prefill_source="scanner_submission",
    ),
    _q(
        22, "P4_5", "P4.5.Q02",
        "Is there a written policy stating how often evals are re-run after model, prompt, tool, or workflow changes?",
        [{
            "key": "policy_state",
            "type": "enum",
            "options": DEFAULT_EVIDENCE_STATES,
            "required": True,
        }, {
            "key": "policy_doc_link",
            "type": "text",
            "required": False,
        }],
        {
            "policy_state": "dims.P4_5.policy_state",
            "policy_doc_link": "dims.P4_5.policy_doc_link",
        },
        evidence_label="cadence policy document",
        evidence_schema_path="dims.P4_5.evidence.policy_doc",
        prefill_source="policy_repository",
    ),
    _q(
        23, "P4_5", "P4.5.Q03",
        "Which events force an out-of-cycle eval re-run?",
        [{
            "key": "forced_rerun_events",
            "type": "checklist",
            "options": [
                "model_change", "prompt_change", "tool_change", "workflow_change",
                "incident", "customer_complaint", "none",
            ],
            "required": True,
        }, {
            "key": "attestation_statement",
            "type": "longtext",
            "required": False,
        }],
        {
            "forced_rerun_events": "dims.P4_5.forced_rerun_events[]",
            "attestation_statement": "dims.P4_5.attestation_statement",
        },
        evidence_label="cadence policy section",
        evidence_schema_path="dims.P4_5.evidence.cadence_policy_section",
        prefill_source="policy_repository",
    ),
    _q(
        24, "P4_5", "P4.5.Q04",
        "Who reviews failed or materially degraded eval results before production release?",
        [{
            "key": "review_role",
            "type": "text",
            "required": True,
        }, {
            "key": "review_named_owner",
            "type": "text",
            "required": True,
        }, {
            "key": "attestation_statement",
            "type": "longtext",
            "required": False,
        }],
        {
            "review_role": "dims.P4_5.review_role",
            "review_named_owner": "dims.P4_5.review_named_owner",
            "attestation_statement": "dims.P4_5.review_attestation_statement",
        },
        evidence_label="release review record if available",
        evidence_schema_path="dims.P4_5.evidence.release_review_record",
        prefill_source="source_connector",
    ),

    # ---------- P4.6 Owner Accountability Matrix ----------
    _q(
        25, "P4_6", "P4.6.Q01",
        "Name the executive sponsor accountable for this agent in production.",
        [{
            "key": "person",
            "type": "text",
            "required": True,
        }, {
            "key": "accountable_decisions",
            "type": "longtext",
            "required": True,
        }],
        {
            "person": "dims.P4_6.roles.executive_sponsor.person",
            "accountable_decisions": "dims.P4_6.roles.executive_sponsor.accountable_decisions",
        },
        evidence_label="RACI document",
        evidence_schema_path="dims.P4_6.evidence.raci_doc",
        prefill_source="account_profile",
    ),
    _q(
        26, "P4_6", "P4.6.Q02",
        "Name the deployment owner accountable for release decisions.",
        [{
            "key": "person",
            "type": "text",
            "required": True,
        }, {
            "key": "accountable_decisions",
            "type": "longtext",
            "required": True,
        }],
        {
            "person": "dims.P4_6.roles.deployment_owner.person",
            "accountable_decisions": "dims.P4_6.roles.deployment_owner.accountable_decisions",
        },
        evidence_label="RACI document",
        evidence_schema_path="dims.P4_6.evidence.raci_doc",
        prefill_source="source_connector",
    ),
    _q(
        27, "P4_6", "P4.6.Q03",
        "Name the model owner accountable for model and prompt changes.",
        [{
            "key": "person",
            "type": "text",
            "required": True,
        }, {
            "key": "accountable_decisions",
            "type": "longtext",
            "required": True,
        }],
        {
            "person": "dims.P4_6.roles.model_owner.person",
            "accountable_decisions": "dims.P4_6.roles.model_owner.accountable_decisions",
        },
        evidence_label="RACI document",
        evidence_schema_path="dims.P4_6.evidence.raci_doc",
        prefill_source="source_connector",
    ),
    _q(
        28, "P4_6", "P4.6.Q04",
        "Name the on-call responder accountable for live incident containment.",
        [{
            "key": "person",
            "type": "text",
            "required": True,
        }, {
            "key": "accountable_decisions",
            "type": "longtext",
            "required": True,
        }],
        {
            "person": "dims.P4_6.roles.on_call_responder.person",
            "accountable_decisions": "dims.P4_6.roles.on_call_responder.accountable_decisions",
        },
        evidence_label="RACI document",
        evidence_schema_path="dims.P4_6.evidence.raci_doc",
        prefill_source="incident_tooling",
    ),
    _q(
        29, "P4_6", "P4.6.Q05",
        "Name the compliance owner accountable for privacy, regulatory response, and audit evidence.",
        [{
            "key": "person",
            "type": "text",
            "required": True,
        }, {
            "key": "accountable_decisions",
            "type": "longtext",
            "required": True,
        }],
        {
            "person": "dims.P4_6.roles.compliance_owner.person",
            "accountable_decisions": "dims.P4_6.roles.compliance_owner.accountable_decisions",
        },
        evidence_label="RACI document",
        evidence_schema_path="dims.P4_6.evidence.raci_doc",
        prefill_source="account_profile",
    ),
    _q(
        30, "P4_6", "P4.6.Q06",
        "How is the owner matrix kept current?",
        [{
            "key": "succession_policy",
            "type": "enum",
            "options": ["formal_succession", "informal", "none"],
            "required": True,
        }, {
            "key": "review_cadence_days",
            "type": "integer",
            "min": 0, "max": 36500,
            "required": True,
        }, {
            "key": "last_reviewed_at",
            "type": "date",
            "required": True,
        }],
        {
            "succession_policy": "dims.P4_6.succession_policy",
            "review_cadence_days": "dims.P4_6.review_cadence_days",
            "last_reviewed_at": "dims.P4_6.last_reviewed_at",
        },
        evidence_label="review log",
        evidence_schema_path="dims.P4_6.evidence.review_log",
        prefill_source="policy_repository",
    ),
]


def assert_thirty_questions() -> None:
    """Boot-time invariant: the v0.1 questionnaire is exactly 30 numbered Qs."""
    numbers = [q["number"] for q in QUESTIONS]
    if numbers != list(range(1, 31)):
        raise AssertionError(f"governance questionnaire v0.1 must have 30 numbered questions in order; got {numbers}")
    section_ids = {s["id"] for s in SECTIONS}
    for q in QUESTIONS:
        if q["section"] not in section_ids:
            raise AssertionError(f"question {q['number']} references unknown section {q['section']}")


def questions_by_section(section_id: str) -> list[dict[str, Any]]:
    return [q for q in QUESTIONS if q["section"] == section_id]


def find_question(number: int) -> dict[str, Any]:
    for q in QUESTIONS:
        if q["number"] == number:
            return q
    raise KeyError(f"no question number {number} in questionnaire v0.1")


assert_thirty_questions()
