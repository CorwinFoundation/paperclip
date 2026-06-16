"""BackBond Certified Scan verify-api-v0.1 copy + constants (BEAAA-5016).

HISTORICAL — superseded by `verify_v0_2.py` per BEAAA-5739 (PM1 pin
completeness + BackBond canon migration). v0.1 was originally frozen at
sha256 9dd8bd933184a32a9286addba55003ab24c1011bde0a16a03a4ef790883a84a6;
the canon-only edit applied here (single-cap "Backbond" -> two-cap
"BackBond" per glossary.md L5 Founder ruling 2026-06-13) intentionally
invalidates that historical freeze. Active surface now imports v0_2.

Boundary (Carrier Rule + CMO packet): the verification page confirms only the
listed dated scan result. It does not confirm bound insurance coverage, policy
terms, license status, or future agent behavior. No carrier name. No competitor
name. No "BackBond Insured" / "BackBond Licensed" wording on this surface.

This module is data + copy only; the service layer in `verify_service.py` owns
DB schema, signature, and route handling.
"""

from __future__ import annotations

VERIFY_API_VERSION = "0.1"

PAGE_H1 = "BackBond Certified Scan"

VERIFIED_BODY = (
    "This agent's BackBond score met the current bind-threshold requirement "
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
    "BackBond certifies that the named agent produced the BackBond score "
    "below under the listed rubric version and scan envelope. The result met "
    "the bind-threshold requirement on the scan date."
)
CERT_FOOTER = (
    "Certificate of scan only. This document is not evidence of bound "
    "coverage, insurance limits, premium, exclusions, or license status. "
    "Coverage can only be confirmed by policy documents issued through the "
    "authorized insurance workflow."
)

# Public labels per CMO packet table.
PUBLIC_LABELS = {
    "vendor_name": "Vendor",
    "agent_name": "Agent",
    "backbond_score": "BackBond score",
    "threshold_status": "Threshold status",
    "scan_id": "Scan ID",
    "envelope_id": "Envelope ID",
    "rubric_version": "Rubric version",
    "scanner_version": "Scanner version",
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
