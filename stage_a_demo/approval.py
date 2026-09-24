"""Start-up-only human-approval wiring.

This module is the single supported construction site for
``HumanApprovalIssuer``. Nothing in the execution path — no request handler, no
merchant adapter, no evidence store — may import it. The request path receives
``ApprovalGrant`` objects that were minted here before the server started
serving, and a grant carries no minting capability.
"""

from __future__ import annotations

import os
import secrets

from .core import HumanApprovalIssuer, Mandate

APPROVAL_SECRET_ENV = "STAGE_A_APPROVAL_SECRET"


def load_approval_secret() -> str:
    """Return the injected approval secret, or mint a fresh per-process one.

    There is no literal default anywhere in the tree. When the environment does
    not inject a secret, each process gets an unguessable random one, so a
    caller cannot reconstruct it by reading the source.
    """
    injected = os.environ.get(APPROVAL_SECRET_ENV)
    if injected:
        return injected
    return secrets.token_hex(32)


def build_issuer(secret: str | None = None) -> HumanApprovalIssuer:
    """Construct the human-side issuer. The only supported construction site."""
    return HumanApprovalIssuer(secret or load_approval_secret())
