"""Architectural applicability + N/A-emission gate — BEAAA-198 Track 2 Phase 2.

Loads `rubric/applicability_policy.yaml` and decides per-dim whether a probe
family fires (returns measured raw) or emits N/A with a byte-stable
justification string.

Decision-independent of the scoring math (Phase 3). This module ships in
Phase 2 so adapter authors can begin populating `architecture_flags` without
waiting for the UW co-sign on N/A scoring policy.

Source: rubric/applicability_policy.yaml.
Plan: BEAAA-198 #document-plan.

Usage from a scan adapter:

    from harness.applicability import load_policy, decide

    policy = load_policy()
    flags = {
        "has_tool_calling": False,
        "has_rag_retrieval": False,
        "has_document_attachments": False,
        "has_inline_html_rendering": False,
        "has_system_prompt_in_response_channel": True,
        "has_credentials_in_prompt": False,
        "has_honeytoken_canary_planted": False,
        "has_multi_tenant_context": False,
    }
    decisions = decide(policy, flags)
    # -> {"P3.1": {"fires": True, ...}, "P3.2": {"fires": False, "na_justification": "P3.2 (Indirect injection) N/A on this surface: ..."}, "P3.3": {"fires": True, ...}}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from rubric.scoring import ScoringError, ValidatedApplicability

REPO = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO / "rubric" / "applicability_policy.yaml"


class ApplicabilityError(ValueError):
    """Raised on policy schema or arch-flag input violations."""


@dataclass(frozen=True)
class Policy:
    version: str
    applies_to_rubric: str
    architecture_flag_names: tuple[str, ...]
    dim_applicability: dict[str, dict[str, Any]]
    na_justification_strings: dict[str, str]


def load_policy(path: str | Path | None = None) -> Policy:
    p = Path(path) if path else DEFAULT_POLICY_PATH
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict):
        raise ApplicabilityError(f"policy YAML root is not a mapping: {p}")

    arch = raw.get("architecture_flags") or {}
    dims = raw.get("dim_applicability") or {}
    nas = raw.get("na_justification_strings") or {}

    if not arch:
        raise ApplicabilityError("policy missing architecture_flags block")
    if not dims:
        raise ApplicabilityError("policy missing dim_applicability block")

    # Every na_justification_key referenced by dim_applicability must exist
    # in na_justification_strings — drift here is a release blocker.
    for did, spec in dims.items():
        key = spec.get("na_justification_key")
        if key and key not in nas:
            raise ApplicabilityError(
                f"dim {did} references na_justification_key='{key}' not in na_justification_strings"
            )

    return Policy(
        version=str(raw.get("version", "")),
        applies_to_rubric=str(raw.get("applies_to_rubric", "")),
        architecture_flag_names=tuple(sorted(arch.keys())),
        dim_applicability=dict(dims),
        na_justification_strings=dict(nas),
    )


def _validate_flags(policy: Policy, flags: dict[str, bool]) -> None:
    """Adapter contract: every declared flag MUST have a definite bool value.

    Missing flags = adapter-incomplete. Unknown flags = adapter-drift. Both
    raise so we never silently default to false (which would inflate N/A
    rates and hide architecture).
    """
    declared = set(policy.architecture_flag_names)
    provided = set(flags.keys())

    missing = declared - provided
    if missing:
        raise ApplicabilityError(
            f"adapter did not provide architecture_flags: {sorted(missing)}"
        )
    unknown = provided - declared
    if unknown:
        raise ApplicabilityError(
            f"adapter provided unknown architecture_flags: {sorted(unknown)}"
        )
    for name, val in flags.items():
        if not isinstance(val, bool):
            raise ApplicabilityError(
                f"architecture_flag '{name}' must be bool, got {type(val).__name__}"
            )


def _evaluate_gate(spec: dict[str, Any], flags: dict[str, bool]) -> bool:
    """Evaluate a single dim_applicability gate against arch flags.

    Supported gate forms (mirror rubric/applicability_policy.yaml):
      fires_when:        { always: true }      -> always fires
      fires_when_any:    [{flag_a: true}, {flag_b: true}, ...]  -> any-of
      fires_when_all:    [{flag_a: true}, {flag_b: true}, ...]  -> all-of
    """
    if "fires_when" in spec:
        when = spec["fires_when"] or {}
        if when.get("always") is True:
            return True
        # Single-flag gate: { has_x: true }
        for k, v in when.items():
            if k == "always":
                continue
            if flags.get(k) != v:
                return False
        return True

    if "fires_when_any" in spec:
        for clause in spec["fires_when_any"] or []:
            for k, v in clause.items():
                if flags.get(k) == v:
                    return True
        return False

    if "fires_when_all" in spec:
        for clause in spec["fires_when_all"] or []:
            for k, v in clause.items():
                if flags.get(k) != v:
                    return False
        return True

    raise ApplicabilityError(
        "dim spec must contain one of fires_when / fires_when_any / fires_when_all"
    )


@dataclass(frozen=True)
class DimDecision:
    dim_id: str
    fires: bool
    na_justification: str | None
    na_justification_key: str | None
    matched_clauses: tuple[str, ...] = field(default_factory=tuple)


def decide(policy: Policy, flags: dict[str, bool]) -> dict[str, DimDecision]:
    """Return per-dim decisions. Raises if flags violate adapter contract."""
    _validate_flags(policy, flags)

    out: dict[str, DimDecision] = {}
    for did, spec in policy.dim_applicability.items():
        fires = _evaluate_gate(spec, flags)
        if fires:
            out[did] = DimDecision(
                dim_id=did,
                fires=True,
                na_justification=None,
                na_justification_key=None,
            )
            continue
        key = spec.get("na_justification_key")
        if not key:
            raise ApplicabilityError(
                f"dim {did} did not fire and has no na_justification_key — "
                "policy schema requires a justification key for every gateable dim"
            )
        justification = policy.na_justification_strings[key].strip()
        out[did] = DimDecision(
            dim_id=did,
            fires=False,
            na_justification=justification,
            na_justification_key=key,
        )
    return out


def applicability_record(policy: Policy, flags: dict[str, bool]) -> dict[str, Any]:
    """Build the `applicability.json` evidence-bundle artifact required by
    the adapter contract (see applicability_policy.yaml §adapter_contract).

    Adapters MUST persist this dict at scans/<scan_id>/applicability.json so
    claims defense + auditors can reproduce the N/A vs measured decision."""
    decisions = decide(policy, flags)
    return {
        "policy_version": policy.version,
        "applies_to_rubric": policy.applies_to_rubric,
        "architecture_flags": dict(flags),
        "applicability_decisions": {
            did: {
                "fires": d.fires,
                "na_justification": d.na_justification,
                "na_justification_key": d.na_justification_key,
            }
            for did, d in decisions.items()
        },
        "na_dims": [did for did, d in decisions.items() if not d.fires],
        "measured_dims": [did for did, d in decisions.items() if d.fires],
    }


def validate_for_scoring(
    policy: Policy,
    trusted_flags: dict[str, bool],
    claimed_decisions: dict[str, dict[str, Any]],
    active_dim_ids: list[str],
) -> ValidatedApplicability:
    """Validate claimed waivers against trusted flags and issue an opaque token."""
    engine = decide(policy, trusted_flags)
    active = set(active_dim_ids)
    for did, claimed in claimed_decisions.items():
        if did not in active:
            raise ScoringError(
                f"applicability waiver for {did!r} rejected: not an active rubric dim"
            )
        if claimed.get("fires") is False:
            expected = engine.get(did)
            if expected is None or expected.fires:
                raise ScoringError(
                    f"applicability waiver for {did!r} rejected: policy engine "
                    "independently rules this dim FIRES"
                )
    canonical = {
        did: {
            "fires": decision.fires,
            "na_justification": decision.na_justification,
            "na_justification_key": decision.na_justification_key,
        }
        for did, decision in engine.items()
        if did in active
    }
    return ValidatedApplicability._from_policy_validation(canonical, policy.version)
