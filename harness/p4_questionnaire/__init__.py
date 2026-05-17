"""BEAAA-484 P4 GOVERNANCE Tier B vendor-evidence intake (questionnaire side).

Single reusable questionnaire that feeds the six P4 GOVERNANCE evidence
channels listed in `contracts/are-scanner-api-v1.md@v1.0`:

  GOV.AUDIT_TRAIL         GOV.CHANGE_MGMT         GOV.KILL_SWITCH
  GOV.HITL_POLICY         GOV.MONITORING          GOV.INCIDENT_RESPONSE

Scope: data-collection harness only. Scoring math lives in
`scripts.score_p4_attestation` (v0.1-LOCKED 2026-05-11 per BEAAA-213/214).
Per `contracts/scanner-implementation-supervision-v1.md` row
"Customer onboarding harness for a new customer archetype" this is
rule-execution; new evidence-type expansion would re-classify as
rule-change + CTO co-sign.
"""
from __future__ import annotations

from .intake import (
    GOV_RUBRIC_POINTS,
    GOV_TO_P4,
    QUESTIONNAIRE_SPEC_PATH,
    QUESTIONNAIRE_SPEC_VERSION,
    PROBE_SET_VERSION,
    load_questionnaire_spec,
    vendor_packet_to_attestation_form,
)

__all__ = [
    "GOV_RUBRIC_POINTS",
    "GOV_TO_P4",
    "QUESTIONNAIRE_SPEC_PATH",
    "QUESTIONNAIRE_SPEC_VERSION",
    "PROBE_SET_VERSION",
    "load_questionnaire_spec",
    "vendor_packet_to_attestation_form",
]
