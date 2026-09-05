"""BEAAA-29804: establish a target's structured-response surface by observing it.

BEAAA-29743 gated ``human_escalation_flagging`` and ``customer_defined_risk_pack``
on ``has_structured_output`` and left the gate fail-closed: only an explicit
``True`` in the job's capability manifest makes them applicable. Nothing in the
tree set that flag, so lane C reported ``not_applicable`` against every target,
including the ones that speak JSON perfectly well.

The fix is not to default the flag to ``True`` — that is exactly the fabricated
result class BEAAA-24612 F6 closed. It is to *derive* the flag from an
observation. A declaration copied out of config is a promise; an admitted
decision object is a capability.

Admission runs through ``harness.lane_c_adapter.read_decision`` — the same
reader lane C grades with — so "this target has a structured-response surface"
means precisely "this target produced the object lane C reads", and cannot
drift away from it. The parser is not widened here; a target that declares the
surface and then answers in prose still fails closed downstream, which
``tests/test_lane_c_prose_target_beaaa29743.py`` pins.

Two evidence sources, one verdict:

* ``probe_live`` spends one bounded target call per family on that family's own
  canary case (the benign one) and admits the reply.
* ``probe_recorded`` / ``probe_capture_tree`` read responses the target already
  produced — e.g. the BEAAA-27092 Dify/qwen capture under
  ``runtime-evidence/lane-c/<candidate>/`` — so a target that has already been
  measured does not have to be re-dispatched to be declared.

There is deliberately no constructor that takes a boolean. The only way to
reach ``has_structured_output: True`` is to hand this module a response the
lane-C reader accepts.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from harness.lane_c_adapter import (
    LANE_C_FAMILIES,
    LaneCError,
    SPECS,
    build_instruction,
    read_decision,
)

#: The architecture flag the two lane-C decision families gate on.
FLAG = "has_structured_output"

#: Families whose applicability this probe can establish.
GATED_FAMILIES: tuple[str, ...] = tuple(
    sorted(
        name
        for name, spec in SPECS.items()
        if getattr(spec, "applicability_flag", None) == FLAG
    )
)

#: The seed the probe sends. Inside the frozen lane-C seed set, so the probe
#: send shape is byte-identical to a real lane-C send.
PROBE_SEED = 0


class ProbeError(ValueError):
    """Raised on a malformed probe request — never on a target that says no."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Observation:
    """One target response, and whether the lane-C reader admitted it."""

    family: str
    case_id: str
    seed: int
    admitted: bool
    reason: str
    response_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "case_id": self.case_id,
            "seed": self.seed,
            "admitted": self.admitted,
            "reason": self.reason,
            "response_sha256": self.response_sha256,
        }


@dataclass(frozen=True)
class ProbeResult:
    """The evidence behind a ``has_structured_output`` declaration."""

    target_identity: str
    source: str
    observations: tuple[Observation, ...]

    @property
    def families(self) -> tuple[str, ...]:
        return tuple(sorted({obs.family for obs in self.observations}))

    def admitted_families(self) -> tuple[str, ...]:
        """Families with at least one admitted response and no rejected one."""

        by_family: dict[str, list[Observation]] = {}
        for obs in self.observations:
            by_family.setdefault(obs.family, []).append(obs)
        return tuple(
            sorted(
                family
                for family, group in by_family.items()
                if group and all(obs.admitted for obs in group)
            )
        )

    def declares(self) -> bool:
        """Fail-closed: every gated family probed must have been admitted.

        Zero observations declares nothing. A single rejected response for a
        family withdraws the declaration for the whole target, because the two
        families share one flag and one reader — a surface that is only
        sometimes there is not a surface we can charge a verdict to.
        """

        if not self.observations:
            return False
        return set(self.admitted_families()) == set(self.families)

    def capability_manifest(self) -> dict[str, bool]:
        """The manifest fragment to hand a job's ``capability_manifest``."""

        return {FLAG: self.declares()}

    def evidence(self) -> dict[str, Any]:
        """Serializable record of why the flag reads the way it does."""

        return {
            "ticket": "BEAAA-29804",
            "flag": FLAG,
            "target_identity": self.target_identity,
            "source": self.source,
            "declared": self.declares(),
            "families_probed": list(self.families),
            "families_admitted": list(self.admitted_families()),
            "observations": [obs.as_dict() for obs in self.observations],
        }


def admit(family: str, raw: Any) -> tuple[bool, str]:
    """Would the lane-C reader accept this response as a decision?

    Returns ``(False, reason)`` rather than raising: a target declining to
    speak JSON is an answer about the target, not an error in the probe.
    """

    if family not in LANE_C_FAMILIES:
        raise ProbeError(f"not a lane-C family: {family!r}")
    try:
        read_decision(family, raw)
    except LaneCError as exc:
        return False, str(exc)
    return True, "lane_c_decision_admitted"


def _canary_case(family: str):
    spec = SPECS[family]
    for case in spec.cases:
        if case.is_canary:
            return case
    return spec.cases[0]


def probe_live(
    target_identity: str,
    call: Callable[[str], str],
    *,
    families: Sequence[str] = GATED_FAMILIES,
    seed: int = PROBE_SEED,
) -> ProbeResult:
    """Spend one target call per family on that family's benign canary case.

    The send is ``build_instruction``'s frozen shape, so a target that passes
    this has demonstrably answered the exact question lane C will ask it.
    """

    if not families:
        raise ProbeError("probe_live requires at least one family")
    observations: list[Observation] = []
    for family in families:
        case = _canary_case(family)
        instruction = build_instruction(family, case, seed)
        raw = call(instruction)
        admitted, reason = admit(family, raw)
        observations.append(
            Observation(
                family=family,
                case_id=case.case_id,
                seed=seed,
                admitted=admitted,
                reason=reason,
                response_sha256=_sha256(raw if isinstance(raw, str) else repr(raw)),
            )
        )
    return ProbeResult(
        target_identity=target_identity,
        source="live_capability_probe",
        observations=tuple(observations),
    )


def probe_recorded(
    target_identity: str,
    records: Iterable[Mapping[str, Any]],
    *,
    families: Sequence[str] = GATED_FAMILIES,
    source: str = "recorded_lane_c_evidence",
) -> ProbeResult:
    """Derive the flag from responses the target already produced.

    ``records`` are ``backbond-lane-c-execution-evidence@1`` rows: each carries
    ``family``, ``stable_case_id``, ``seed`` and ``recorded_target_response``.
    Nothing is re-dispatched, and nothing is trusted except the recorded bytes.
    """

    wanted = set(families)
    observations: list[Observation] = []
    for record in records:
        family = str(record.get("family", ""))
        if family not in wanted:
            continue
        raw = record.get("recorded_target_response")
        admitted, reason = admit(family, raw)
        observations.append(
            Observation(
                family=family,
                case_id=str(record.get("stable_case_id", "")),
                seed=int(record.get("seed", -1)),
                admitted=admitted,
                reason=reason,
                response_sha256=_sha256(raw if isinstance(raw, str) else repr(raw)),
            )
        )
    return ProbeResult(
        target_identity=target_identity,
        source=source,
        observations=tuple(observations),
    )


def probe_capture_tree(
    root: str | Path,
    *,
    target_identity: str | None = None,
    families: Sequence[str] = GATED_FAMILIES,
) -> ProbeResult:
    """Read a ``harness/lane_c_capture`` evidence tree and derive the flag.

    Layout is ``<root>/<family>/<case_id>/seed-<n>.json``. Used to declare the
    BEAAA-27092 Dify/qwen target off its own BEAAA-21723 capture, which is the
    honest reading of "lane C demonstrably works against this target".
    """

    base = Path(root)
    if not base.is_dir():
        raise ProbeError(f"capture tree not found: {base}")
    records: list[Mapping[str, Any]] = []
    identity = target_identity
    for family in families:
        for path in sorted(base.glob(f"{family}/*/seed-*.json")):
            record = json.loads(path.read_text())
            if identity is None:
                identity = str(record.get("target_identity", "")) or None
            records.append(record)
    return probe_recorded(
        identity or str(base.name),
        records,
        families=families,
        source=f"capture_tree:{base.name}",
    )
