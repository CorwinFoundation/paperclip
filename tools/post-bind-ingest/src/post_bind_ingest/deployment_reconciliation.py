"""Resolve the deployment limb of an appliance reconciliation key.

The appliance receipt remains counts/hashes-only.  Deployment identity is
owned by the deployment/binding registry and is joined here, at the consumer
boundary, rather than being added to the appliance wire.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class DeploymentJoin:
    tenant_id_hash: str
    binding_id_hash: str
    agent_id_hash: str
    deployment_id: str | None
    resolved: bool
    reason: str


def resolve_deployment_limb(
    receipt: Mapping[str, object],
    registry_rows: Iterable[Mapping[str, object]],
) -> DeploymentJoin:
    """Join one projected receipt to exactly one registry deployment.

    Registry rows expose the same normalized hashed tenant/binding/agent
    context plus the opaque ``deployment_id`` already used by post-bind ingest.
    Missing or ambiguous matches remain explicitly unresolved; this function
    never guesses, hashes, or synthesizes a deployment identifier.
    """
    keys = ("tenant_id_hash", "binding_id_hash", "agent_id_hash")
    context = {key: receipt.get(key) for key in keys}
    if any(not isinstance(value, str) or not value for value in context.values()):
        return DeploymentJoin(
            *(str(context[key] or "") for key in keys), None, False,
            "incomplete_receipt_context",
        )

    matches = [row for row in registry_rows if all(row.get(key) == context[key] for key in keys)]
    deployments = {
        row.get("deployment_id") for row in matches
        if isinstance(row.get("deployment_id"), str) and row.get("deployment_id")
    }
    if len(matches) == 1 and len(deployments) == 1:
        return DeploymentJoin(
            *(context[key] for key in keys), deployments.pop(), True, "resolved",
        )
    reason = "deployment_registry_miss" if not matches else "deployment_registry_non_unique"
    return DeploymentJoin(*(context[key] for key in keys), None, False, reason)
