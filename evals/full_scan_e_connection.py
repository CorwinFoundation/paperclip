"""Customer connection contract for Full Scan E (BEAAA-38989).

Customers expose a Standard Agent Interface ``AgentSession`` over an existing
runner adapter (for example ``HTTPAgentSession``) and declare exactly which
action surfaces the target supports.  The scanner owns and supplies the inert
sandbox tools; customer endpoints never receive production credentials.
"""
from __future__ import annotations

from typing import Mapping

from harness.agent_interface import AgentSession, HTTPAgentSession

SURFACES = ("orders", "payments", "email", "accounts")


def bind_capabilities(session: AgentSession, capability_manifest: Mapping[str, bool]) -> AgentSession:
    """Validate and bind a per-surface manifest to any SAI transport adapter."""
    unknown = set(capability_manifest) - set(SURFACES)
    malformed = {k: v for k, v in capability_manifest.items() if type(v) is not bool}
    if unknown or malformed:
        raise ValueError(f"invalid capability_manifest: unknown={sorted(unknown)} malformed={malformed}")
    session.capability_manifest = {surface: capability_manifest.get(surface, False) for surface in SURFACES}
    return session


def http_session(*, url: str, model: str, capability_manifest: Mapping[str, bool],
                 tools: tuple[dict, ...], **kwargs: object) -> AgentSession:
    """Construct the production HTTP transport and bind its declared surfaces.

    ``tools`` must be the scanner's sandbox-tool schemas for the declared
    surfaces.  Execution remains scanner-mediated by ``SessionProbeDriver``.
    """
    return bind_capabilities(
        HTTPAgentSession(url=url, model=model, tools=tools, **kwargs),
        capability_manifest,
    )
