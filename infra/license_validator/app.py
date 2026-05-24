"""FastAPI surface for the license-validation stub (BEAAA-569).

Two routes:

* ``POST /license/validate`` — public; consumed by the PyInstaller binary
  on every invocation (ADR-0011 §3.4).
* ``POST /license/revoke`` — admin; flips a key to revoked. Authenticates
  via the ``Authorization: Bearer <admin_token>`` header.

The actual *deployment* (TLS termination, host, KMS-backed signing key, log
sink) is production hardening filed as a follow-on. This module is the
service layer the production transport will wrap.
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .attestation import load_private_key
from .audit import AuditLog
from .registry import Registry
from .service import LicenseValidationRequest, validate

LICENSE_VALIDATOR_PRIVATE_KEY_ENV = "IFA_LICENSE_VALIDATOR_PRIVATE_KEY"
LICENSE_VALIDATOR_REGISTRY_ENV = "IFA_LICENSE_VALIDATOR_REGISTRY"
LICENSE_VALIDATOR_AUDIT_ENV = "IFA_LICENSE_VALIDATOR_AUDIT_LOG"
LICENSE_VALIDATOR_ADMIN_TOKEN_ENV = "IFA_LICENSE_VALIDATOR_ADMIN_TOKEN"


class ValidateRequest(BaseModel):
    partner_id: str = Field(min_length=1)
    binary_build_id: str = Field(min_length=1)
    scan_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    license_key: str = Field(min_length=1)


class ValidateResponse(BaseModel):
    outcome: str
    attestation: str | None
    expires_at: str | None


class RevokeRequest(BaseModel):
    license_key: str = Field(min_length=1)
    reason: str = Field(min_length=1)


def create_app(
    *,
    registry: Registry | None = None,
    audit_log: AuditLog | None = None,
    private_key_path: Path | None = None,
    admin_token: str | None = None,
) -> FastAPI:
    """Construct the FastAPI app.

    All four dependencies can be injected for tests; in production they
    come from the env vars named above.
    """
    if registry is None:
        registry_path = os.environ.get(LICENSE_VALIDATOR_REGISTRY_ENV)
        if not registry_path:
            raise RuntimeError(
                f"{LICENSE_VALIDATOR_REGISTRY_ENV} not set and no registry injected"
            )
        registry = Registry(Path(registry_path))

    if audit_log is None:
        audit_path = os.environ.get(LICENSE_VALIDATOR_AUDIT_ENV)
        if not audit_path:
            raise RuntimeError(
                f"{LICENSE_VALIDATOR_AUDIT_ENV} not set and no audit log injected"
            )
        audit_log = AuditLog(Path(audit_path))

    if private_key_path is None:
        key_path_env = os.environ.get(LICENSE_VALIDATOR_PRIVATE_KEY_ENV)
        if not key_path_env:
            raise RuntimeError(
                f"{LICENSE_VALIDATOR_PRIVATE_KEY_ENV} not set and no private key path injected"
            )
        private_key_path = Path(key_path_env)

    private_key = load_private_key(private_key_path)

    if admin_token is None:
        admin_token = os.environ.get(LICENSE_VALIDATOR_ADMIN_TOKEN_ENV)
        if not admin_token:
            raise RuntimeError(
                f"{LICENSE_VALIDATOR_ADMIN_TOKEN_ENV} not set and no admin token injected"
            )

    app = FastAPI(title="IFA License Validator (stub)", version="0.1.0")
    app.state.registry = registry
    app.state.audit_log = audit_log
    app.state.private_key = private_key
    app.state.admin_token = admin_token

    @app.post("/license/validate", response_model=ValidateResponse)
    async def post_validate(req: ValidateRequest, request: Request) -> dict[str, Any]:
        source_ip = _source_ip(request)
        response = validate(
            LicenseValidationRequest(**req.model_dump()),
            registry=app.state.registry,
            private_key=app.state.private_key,
            audit_log=app.state.audit_log,
            source_ip=source_ip,
        )
        return {
            "outcome": response.outcome,
            "attestation": response.attestation,
            "expires_at": response.expires_at,
        }

    @app.post("/license/revoke")
    async def post_revoke(
        req: RevokeRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        if not _admin_authenticated(authorization, app.state.admin_token):
            raise HTTPException(status_code=401, detail="admin auth required")
        try:
            record = app.state.registry.revoke(
                license_key=req.license_key, reason=req.reason
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="license_key not found")
        return {
            "license_key": record.license_key,
            "status": record.status,
            "revoked_at": record.revoked_at.isoformat().replace("+00:00", "Z")
            if record.revoked_at
            else None,
            "revocation_reason": record.revocation_reason,
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _admin_authenticated(authorization: str | None, expected: str) -> bool:
    if not authorization:
        return False
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1], expected)


def _source_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"
