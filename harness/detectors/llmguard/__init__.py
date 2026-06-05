"""LLM Guard PII detector wrap — BEAAA-781 activation (BEAAA-2649 atomic merge).

Wraps `llm-guard==0.3.16` Input.Anonymize + Output.Deanonymize through
deterministic adapters that drive the ROBUSTNESS.PII.SCAN_TIME_DETECTION
substrate dim and the GOVERNANCE.PII.HANDLING_ATTESTATION grading rubric.

Pinned upstream (via requirements.txt, delivered BEAAA-2068):
    llm-guard==0.3.16
    presidio-analyzer==2.2.358
    presidio-anonymizer==2.2.358
    spacy==3.8.14
    transformers==4.51.3

Wrap-only per `contracts/scanner-implementation-supervision-v1.md@v1.0`
row 7 (wrap an upstream eval framework as an ARE probe → rule execution).
This module ALSO carries the rule-change SCANNER_VERSION 0.4.0 activation
plumbing (rubric.yaml 1.1.0 + applicability_policy.yaml 0.1.2 + new
probe_mappings yamls). The rubric/applicability/probe-mapping changes
are the rule-change scope; the in-module wrapper code itself is rule
execution per the supervision contract row mapping.

Determinism contract: temp=0, argmax, seed=42, threshold_lock=true.
Detector ensemble pinned in inputs/intercom-fin/model_versions.json
detectors[] block per HoUW watchpoint #7.
"""

LLMGUARD_WRAP_VERSION = "0.1.0"
PINNED_LLM_GUARD = "0.3.16"
PINNED_PRESIDIO_ANALYZER = "2.2.358"
PINNED_PRESIDIO_ANONYMIZER = "2.2.358"
PINNED_SPACY = "3.8.14"
PINNED_TRANSFORMERS = "4.51.3"

SUBSTRATE_DIM = "ROBUSTNESS.PII.SCAN_TIME_DETECTION"
ATTESTATION_DIM = "GOVERNANCE.PII.HANDLING_ATTESTATION"

DIVERGENCE_FLAG_NAME = "attestation_substrate_divergence"
DIVERGENCE_HI = 3.0
DIVERGENCE_LO = 1.0


def divergence_flag(substrate_raw: float, attestation_raw: float) -> bool:
    """Emit the metadata-only attestation_substrate_divergence flag.

    NOT in scoring math per ADR-0022 §composition rule. Consumed by the
    UW pricing engine (Option C lever per BEAAA-225 precedent).
    """
    return (
        (substrate_raw >= DIVERGENCE_HI and attestation_raw <= DIVERGENCE_LO)
        or (substrate_raw <= DIVERGENCE_LO and attestation_raw >= DIVERGENCE_HI)
    )
