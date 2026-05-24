"""IFA license-validation endpoint (BEAAA-569).

Stub of the Path-3 design-partner license gate per ADR-0011 §3.4. The
:mod:`service` module exposes a transport-agnostic ``validate`` function;
:mod:`app` wraps it as a FastAPI app; :mod:`client` is the reference client
the PyInstaller binary (BEAAA-567) consumes.

Production hardening (TLS termination, KMS-backed signing keys, audit-log
retention rotation, on-call) is a follow-on child to be filed after BEAAA-567
clears its smoke against this stub.
"""

from .service import (
    LicenseValidationRequest,
    LicenseValidationResponse,
    ValidationOutcome,
    validate,
)

__all__ = [
    "LicenseValidationRequest",
    "LicenseValidationResponse",
    "ValidationOutcome",
    "validate",
]
