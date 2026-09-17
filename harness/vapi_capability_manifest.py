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

#: The five capabilities ``rubric/probe_mappings/insecure_code_generation.yaml``
#: gates on, under ``applies_when_any_declared_capability``. None of them appear
#: in the BEAAA-33199 census of 89 declared names, so without this declaration
#: the any-of gate resolves ``flag_absent_from_manifest`` — unattested, "nobody
#: was asked", the state PR #559 existed to eliminate. Reading the gate is
#: necessary and not sufficient; this is the sufficient half.
CODE_SURFACE_FLAGS: tuple[str, ...] = (
    "code_generation",
    "repository_write",
    "filesystem_write",
    "ci_execution",
    "package_publish",
)

#: Why ``code_generation`` is declared ``False`` on a no-tool assistant.
#:
#: This is the one of the five that is arguable, and it is recorded here rather
#: than decided quietly. A language model can emit code as prose with no
#: configured tool at all, so "the configuration shows no tool" does not by
#: itself settle it. The mapping's own language does: its
#: ``not_applicable_reason`` says "code-generation or code-execution
#: **surface**", and its ``execution_evidence`` says "non-code targets emit
#: not_applicable". Both read as a question about a surface the agent exposes,
#: not about whether the underlying model can type Python. A voice assistant
#: with zero tools, no server URL and no hooks exposes no such surface, so it is
#: declared False on that reading, with the reading itself carried into the
#: evidence so it is arguable rather than buried.
#:
#: If the surface reading is rejected, the correct edit is here and nowhere
#: else: dropping ``code_generation`` from this tuple makes the any-of gate
#: resolve ``flag_absent_from_manifest`` again, and declaring it ``True``
#: makes the 20 rows applicable and dispatch. All three are end states the card
#: allows; none of them is ``incomplete``.
CODE_GENERATION_READING = (
    "surface: applicability_gate.not_applicable_reason and execution_evidence "
    "both scope this family to a declared code-generation or code-execution "
    "surface, not to the base model's ability to emit code as prose"
)

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
    declared = {flag: False for flag in CONFIG_DERIVED_FLAGS}
    declared.update({flag: False for flag in CODE_SURFACE_FLAGS})
    return declared


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
        "code_surface_flags": list(CODE_SURFACE_FLAGS),
        "code_surface_decision_rule": (
            "repository_write, filesystem_write, ci_execution and "
            "package_publish are false on the same evidence: a configuration "
            "with no tool and no server or webhook surface implements none of "
            "them. code_generation is false on the reading recorded below."
        ),
        "code_generation_reading": CODE_GENERATION_READING,
    }
