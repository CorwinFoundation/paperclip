"""BEAAA-30934: declare the scheduled Vapi lane's architecture flags.

The scheduled lane submitted its job with no ``capability_manifest`` at all,
so :func:`evals.aiuc_path_b.applicability.resolve` saw ``None`` and returned
the *unattested policy default* for every gated family: ``applicable=False``,
``source="policy_default"``, ``attested=False``, reason
``"<flag>=absent_capability_manifest"``.

Forty-four rows of the BEAAA-30934 census ended there —
``human_escalation_flagging`` (20) and ``customer_defined_risk_pack`` (12),
which gate on ``has_structured_output``, and ``mcp_tool_integrity`` (12),
which gates on ``has_mcp_tool_integration``. That state is not a decline. It
records that nobody was asked, which is precisely what the card refuses as an
end state for a row whose target exists.

Two of the three flags are read off the authenticated assistant configuration
BEAAA-33199 captured in ``artifacts/BEAAA-33199/capability-manifest.json``.
That snapshot records zero ``model_tools``, zero ``model_tool_ids``, no
``server_url`` or ``legacy_server_url``, and no ``hooks``. An assistant with
no configured tool and no server or webhook surface cannot place a tool call
or host an MCP integration, so ``has_tool_calling`` and
``has_mcp_tool_integration`` are declared ``False`` against that evidence —
an attested decline carrying an ``evidence_ref``, not a policy default.

``has_structured_output`` is deliberately *not* in that read. Whether a target
can emit the lane-C decision object is not visible in a Vapi assistant's
configuration, and defaulting it either way is the fabricated-result class
BEAAA-24612 F6 closed. It is derived by observation through
``harness.structured_output_probe`` (BEAAA-29804) and merged in by the caller,
so the only route to ``True`` remains a response the lane-C reader admitted.

Nothing here declares anything about an assistant other than the one the
snapshot was taken from. :func:`declare_for_assistant` returns ``None`` for
any other id, which leaves the lane exactly as it is today rather than
attributing one target's configuration to another.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The BEAAA-33199 authenticated configuration snapshot.
MANIFEST_PATH = REPO_ROOT / "artifacts" / "BEAAA-33199" / "capability-manifest.json"

#: Flags this module is willing to read off a configuration snapshot.
CONFIG_DERIVED_FLAGS: tuple[str, ...] = ("has_tool_calling", "has_mcp_tool_integration")

#: The flag that must come from observation instead (BEAAA-29804).
OBSERVED_FLAG = "has_structured_output"


class CapabilityDeclarationError(ValueError):
    """The snapshot cannot support the declaration being asked of it."""


def load_snapshot(manifest_path: Path | str = MANIFEST_PATH) -> dict[str, Any]:
    path = Path(manifest_path)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CapabilityDeclarationError(f"capability snapshot unreadable: {path}") from exc
    except ValueError as exc:
        raise CapabilityDeclarationError(f"capability snapshot malformed: {path}") from exc
    if not isinstance(snapshot, Mapping):
        raise CapabilityDeclarationError(f"capability snapshot is not an object: {path}")
    return dict(snapshot)


def has_no_tool_surface(observed: Mapping[str, Any]) -> bool:
    """Whether the captured configuration shows no tool or webhook surface."""

    return (
        not observed.get("model_tools")
        and not observed.get("model_tool_ids")
        and observed.get("server_url") is None
        and observed.get("legacy_server_url") is None
        and not observed.get("hooks")
    )


def config_declared_flags(snapshot: Mapping[str, Any]) -> dict[str, bool]:
    """Read the tool-surface flags off one configuration snapshot.

    Fails closed in the direction that matters: this module knows how to
    declare the *absence* of a tool surface from an empty capture. A snapshot
    that shows tools, a server URL or hooks describes a surface whose reach
    this function cannot bound, so it refuses rather than guessing ``True``.
    """

    observed = snapshot.get("configuration_observed")
    if not isinstance(observed, Mapping):
        raise CapabilityDeclarationError(
            "snapshot has no configuration_observed object to read"
        )
    if not has_no_tool_surface(observed):
        raise CapabilityDeclarationError(
            "snapshot records a tool or webhook surface; this module only "
            "declares the absence of one"
        )
    return {flag: False for flag in CONFIG_DERIVED_FLAGS}


def declare_for_assistant(
    assistant_id: str,
    *,
    manifest_path: Path | str = MANIFEST_PATH,
) -> dict[str, bool] | None:
    """Config-derived flags for ``assistant_id``, or ``None`` if not captured.

    ``None`` is the honest answer for an assistant this snapshot says nothing
    about, and it restores the caller to the undeclared behaviour rather than
    lending one target's configuration to another.
    """

    snapshot = load_snapshot(manifest_path)
    if str(snapshot.get("assistant_id") or "") != str(assistant_id):
        return None
    return config_declared_flags(snapshot)


def declaration_evidence(
    assistant_id: str,
    declared: Mapping[str, bool],
    *,
    manifest_path: Path | str = MANIFEST_PATH,
) -> dict[str, Any]:
    """Why each config-derived flag reads the way it does, for the manifest."""

    snapshot = load_snapshot(manifest_path)
    return {
        "ticket": "BEAAA-30934",
        "assistant_id": str(assistant_id),
        "flags": dict(declared),
        "source": "captured_assistant_configuration",
        "snapshot": str(Path(manifest_path)),
        "snapshot_issue": snapshot.get("issue"),
        "configuration_evidence": snapshot.get("configuration_evidence"),
        "configuration_observed": snapshot.get("configuration_observed"),
        "decision_rule": (
            "has_tool_calling and has_mcp_tool_integration are false when the "
            "captured configuration records no tool, tool id, server or "
            "legacy server URL, and no hooks"
        ),
    }
