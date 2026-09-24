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


_PROCESS_ISSUER: HumanApprovalIssuer | None = None


def _process_issuer() -> HumanApprovalIssuer:
    """The one approval authority for this process.

    Resolved from process configuration once, then reused. Nothing in the
    execution path passes an issuer in, which is what makes an approval row it
    minted with an issuer of its own refuse to record (BEAAA-35331 SA7).

    What that does not mean (BEAAA-35354 SA10): in-process code can rebind
    ``_PROCESS_ISSUER`` to an authority of its own and read the secret at
    ``_process_issuer()._secret``. That holds for any in-process capability in
    Python and no in-process change closes it. The substitution this resists is
    substitution across a process boundary, which Stage A does not yet have:
    the human-side module and the execution path run in the same interpreter.
    Treat that as the demo's boundary limit, not as isolation it has.
    """
    global _PROCESS_ISSUER
    if _PROCESS_ISSUER is None:
        _PROCESS_ISSUER = HumanApprovalIssuer(load_approval_secret())
    return _PROCESS_ISSUER


def build_issuer() -> HumanApprovalIssuer:
    """Hand the process approval authority to start-up wiring. Start-up only."""
    return _process_issuer()


def approve_mandate(store, *, case_id: str, mandate: Mandate, issuer=None, origin: str = "approval-step") -> str:
    """The human approval step: mint one token and record one single-use grant.

    Returns the ``grant_id``, which is the only thing that crosses into the
    execution path. The token never leaves this module.
    """
    issuer = issuer or _process_issuer()
    grant_id = f"grant-{secrets.token_hex(8)}"
    approval_token = issuer.approve(mandate, grant_id)
    store._record_approval(
        grant_id=grant_id,
        case_id=case_id,
        mandate=mandate,
        approval_token=approval_token,
        origin=origin,
        issuer=issuer,
    )
    return grant_id
