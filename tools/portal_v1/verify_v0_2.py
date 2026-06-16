"""BackBond Certified Scan verify-api-v0.2 copy + constants (BEAAA-5739).

Supersedes verify_v0_1.py (BEAAA-4915 sha256
9dd8bd933184a32a9286addba55003ab24c1011bde0a16a03a4ef790883a84a6) on a
two-axis migration:

  1. BackBond canon enforcement (glossary.md L5; Founder ruling 2026-06-13).
     Single-cap "Backbond" replaced with two-cap "BackBond" in all body copy.
     Dict-key `backbond_score` remains lowercase (technical identifier).
  2. PM1 pin completeness (Auditor BEAAA-5679 Track D AC3 FAIL).
     The public surface emits a numeric `backbond_score` (0-100) — a released
     BackBond Score under are-scanner-api-v1@v1.0 Hard guarantee #3. PM1 applies.
     v0.1 carried only `scanner_version` + `rubric_version`. v0.2 adds
     `corpus_version`, `model_versions`, `seed` so the full upstream pin set
     (portal.py SCANNER_REQUIRED_FIELDS L116-120) flows to the public envelope.

Boundary (Carrier Rule + CMO packet): the verification page confirms only the
listed dated scan result. It does not confirm bound insurance coverage, policy
terms, license status, or future agent behavior. No carrier name. No competitor
name. No "BackBond Insured" / "BackBond Licensed" wording on this surface.

This module is data + copy only; the service layer in `verify_service.py` owns
DB schema, signature, and route handling.

CMO co-sign required before merge per BEAAA-5739 AC6 (carriers/external surfaces
canon migration). Freeze hash computed over canonical-form of this module's
constants; recorded in artifacts/beaaa5739/verify_v0_2_freeze.json.
"""

from __future__ import annotations

VERIFY_API_VERSION = "0.2"

PAGE_H1 = "BackBond Certified Scan"

VERIFIED_BODY = (
    "This agent's BackBond Score met the current bind-threshold requirement "
    "for the listed rubric version on the scan date below. The verification "
    "is tied to a signed scan envelope and expires on the valid-through date."
)

EXPIRED_BODY = (
    "This scan verification has expired. The historical scan envelope remains "
    "visible, but the badge should be removed or replaced with a current "
    "BackBond scan."
)

REVOKED_BODY = (
    "This scan verification is no longer valid. Do not display the badge."
)

NOT_FOUND_BODY = (
    "No valid public scan envelope was found for this URL."
)

FOOTER_DISCLAIMER = (
    "Verification confirms the listed scan result only. It does not confirm "
    "bound insurance coverage, policy terms, license status, or future agent "
    "behavior."
)

# Certificate copy (procurement PDF).
CERT_TITLE = "Certificate of BackBond Scan"
CERT_LEAD = (
    "BackBond certifies that the named agent produced the BackBond Score "
    "below under the listed rubric version and scan envelope. The result met "
    "the bind-threshold requirement on the scan date."
)
CERT_FOOTER = (
    "Certificate of scan only. This document is not evidence of bound "
    "coverage, insurance limits, premium, exclusions, or license status. "
    "Coverage can only be confirmed by policy documents issued through the "
    "authorized insurance workflow."
)

# Public labels per CMO packet table. v0.2 adds three pins (corpus_version,
# model_versions, seed) per BEAAA-5739 AC2.
PUBLIC_LABELS = {
    "vendor_name": "Vendor",
    "agent_name": "Agent",
    "backbond_score": "BackBond score",
    "threshold_status": "Threshold status",
    "scan_id": "Scan ID",
    "envelope_id": "Envelope ID",
    "rubric_version": "Rubric version",
    "scanner_version": "Scanner version",
    "corpus_version": "Corpus version",
    "model_versions": "Model versions",
    "seed": "Run seed",
    "scan_date": "Scan date",
    "valid_through": "Valid through",
    "methodology_url": "Methodology",
    "probe_ledger_url": "Probe ledger",
    "verification_url": "Verification URL",
    "signature_status": "Signature",
}

# Whitelist of signature_status values emitted to the public surface.
SIGNATURE_STATUSES = ("valid", "expired", "revoked", "not_found")

# Threshold status enum exposed on the certificate.
THRESHOLD_STATUSES = ("met_bind_threshold",)

KEY_ID = "backbond-scan-signing-2026-06"
SIGNATURE_ALGORITHM = "ed25519"
