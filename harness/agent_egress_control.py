"""Disabled-by-default, control-only agent-egress-bench harness.

This module evaluates customer security controls, never agent behavior. It has
no model clients and accepts only a local adapter implementing ``evaluate``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

FEATURE_FLAG = "BACKBOND_AGENT_EGRESS_CONTROL"
SOURCE_PROJECT = "luckyPipewrench/agent-egress-bench"
SOURCE_COMMIT = "e19efee4894994c887f6ae69bcdfbc05b93b56db"
CASES_TREE = "d1d50c8d3e5a920311c53686f0d2fc3fdbe33d33"
ORACLE_VERSION = "agent-egress-control-oracle-v1"
RECEIPT_VERSION = "agent-egress-control-receipt-v2"
SIGNATURE_ALGORITHM = "ed25519"
OUTCOMES = ("passed", "failed", "errored", "not_applicable")


@dataclass(frozen=True)
class ApplicabilityInputs:
    supported_transports: frozenset[str]
    capabilities: frozenset[str]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicabilityInputs":
        transports = value.get("supported_transports", ())
        capabilities = value.get("capabilities", ())
        if "control_type" in value:
            raise ValueError(
                "control_type is not an applicability input; declare transports "
                "and capabilities supported by the control"
            )
        if not isinstance(transports, Sequence) or isinstance(transports, (str, bytes)):
            raise ValueError("supported_transports must be a list")
        if not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes)):
            raise ValueError("capabilities must be a list")
        return cls(
            supported_transports=frozenset(map(str, transports)),
            capabilities=frozenset(map(str, capabilities)),
        )


@dataclass(frozen=True)
class ControlCase:
    local_control_case_id: str
    upstream_case_id: str
    category: str
    transport: str
    requires: tuple[str, ...]
    expected_verdict: str
    payload: Mapping[str, Any]
    source_path: str


def enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(FEATURE_FLAG, "").strip().lower() in {"1", "true", "yes"}


def load_cases(root: Path) -> tuple[ControlCase, ...]:
    """Resolve each logical case exactly once in stable ID order."""
    cases: list[ControlCase] = []
    paths = sorted(root.glob("*/*.json"))
    paths.extend(sorted(root.glob("mcp-drift/*/case.yaml")))
    for path in paths:
        raw = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.suffix == ".json"
            else yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        upstream_id = str(raw["id"])
        cases.append(
            ControlCase(
                local_control_case_id=f"oss.agent-egress.{upstream_id}",
                upstream_case_id=upstream_id,
                category=str(raw["category"]),
                transport=str(raw["transport"]),
                requires=tuple(map(str, raw.get("requires", ()))),
                expected_verdict=str(raw["expected_verdict"]),
                payload=raw,
                source_path=path.relative_to(root).as_posix(),
            )
        )
    ids = [case.upstream_case_id for case in cases]
    if len(ids) != 197 or len(set(ids)) != 197:
        raise ValueError(f"pinned corpus must resolve to 197 unique cases, got {len(ids)}")
    if len({case.category for case in cases}) != 18:
        raise ValueError("pinned corpus must resolve to 18 categories")
    return tuple(sorted(cases, key=lambda case: case.upstream_case_id))


def applicability(case: ControlCase, inputs: ApplicabilityInputs) -> bool:
    """Deterministic AND semantics for transport and declared requirements."""
    return (
        case.transport in inputs.supported_transports
        and set(case.requires).issubset(inputs.capabilities)
    )


class FixtureAdapter:
    """Sealed, declarative adapter for local N=1 control fixtures.

    ``run`` rejects every other type, including subclasses, before calling
    adapter code. This makes the no-model/no-side-effect boundary enforceable:
    callers supply inert verdict data rather than an executable callback.
    """

    __slots__ = ("_verdicts", "_default_to_expected", "_evaluation_count")

    def __init__(
        self,
        verdicts: Mapping[str, str] | None = None,
        *,
        default_to_expected: bool = True,
    ) -> None:
        normalized = dict(verdicts or {})
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in normalized.items()):
            raise ValueError("fixture verdicts must map string case IDs to string verdicts")
        if not isinstance(default_to_expected, bool):
            raise ValueError("default_to_expected must be bool")
        self._verdicts = normalized
        self._default_to_expected = default_to_expected
        self._evaluation_count = 0

    @property
    def call_counts(self) -> Mapping[str, int]:
        """Observed counters emitted by the sealed execution boundary."""
        return {
            "target": 0,
            "simulator": 0,
            "attacker": 0,
            "judge": 0,
            "local_evaluations": self._evaluation_count,
        }

    def evaluate(self, case: ControlCase) -> str:
        """Evaluate outside ``run`` for local inspection only.

        The receipt-producing path deliberately does not dispatch through this
        overridable method.
        """
        return _evaluate_fixture(self, case)


def _evaluate_fixture(adapter: FixtureAdapter, case: ControlCase) -> str:
    """Evaluate inert fixture data without invoking caller-supplied code."""
    if type(adapter) is not FixtureAdapter:
        raise TypeError("fixture boundary requires the exact sealed adapter type")
    adapter._evaluation_count += 1
    if case.upstream_case_id in adapter._verdicts:
        return adapter._verdicts[case.upstream_case_id]
    if adapter._default_to_expected:
        return case.expected_verdict
    raise RuntimeError("fixture verdict missing")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def run(
    cases: Sequence[ControlCase],
    inputs: ApplicabilityInputs,
    adapter: FixtureAdapter,
    *,
    evidence_uri: str,
    evidence_sha256: str,
    signing_key: Ed25519PrivateKey,
    signing_key_id: str,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not enabled(env):
        raise RuntimeError(f"{FEATURE_FLAG} is disabled")
    if type(adapter) is not FixtureAdapter:
        raise TypeError(
            "unverifiable adapter rejected: run requires the sealed, declarative "
            "FixtureAdapter execution boundary"
        )
    telemetry_before = dict(adapter.call_counts)
    if any(telemetry_before[role] != 0 for role in ("target", "simulator", "attacker", "judge")):
        raise RuntimeError("model-role telemetry must be zero before execution")
    results: list[dict[str, str]] = []
    for case in cases:
        applies = applicability(case, inputs)
        if not applies:
            outcome = "not_applicable"
            actual = "not_applicable"
        else:
            try:
                actual = _evaluate_fixture(adapter, case)
                if actual not in {"block", "allow", "warn"}:
                    raise ValueError("adapter returned an invalid verdict")
                outcome = "passed" if actual == case.expected_verdict else "failed"
            except Exception:
                actual = "error"
                outcome = "errored"
        results.append(
            {
                "local_control_case_id": case.local_control_case_id,
                "source_case_id": case.upstream_case_id,
                "source_path": case.source_path,
                "case_class": "customer_control",
                "applicability_predicate": "eligible_egress_control_v1",
                "applicability_result": "applicable" if applies else "not_applicable",
                "expected_verdict": case.expected_verdict,
                "actual_verdict": actual,
                "outcome": outcome,
                "oracle_version": ORACLE_VERSION,
                "execution_seed": "deterministic-no-randomness",
                "attempt": "1",
                "evidence_uri": evidence_uri,
            }
        )
    counts = Counter(result["outcome"] for result in results)
    summary = {name: counts[name] for name in OUTCOMES}
    summary["applicable"] = summary["passed"] + summary["failed"] + summary["errored"]
    telemetry_after = dict(adapter.call_counts)
    role_counts = {
        "target_calls": telemetry_after["target"] - telemetry_before["target"],
        "simulator_calls": telemetry_after["simulator"] - telemetry_before["simulator"],
        "attacker_calls": telemetry_after["attacker"] - telemetry_before["attacker"],
        "judge_calls": telemetry_after["judge"] - telemetry_before["judge"],
    }
    if any(value != 0 for value in role_counts.values()):
        raise RuntimeError("model-role call observed inside local execution boundary")
    receipt: dict[str, Any] = {
        "version": RECEIPT_VERSION,
        "namespace": "customer_controls.agent_egress",
        "source_project": SOURCE_PROJECT,
        "source_commit": SOURCE_COMMIT,
        "source_cases_tree": CASES_TREE,
        "oracle_version": ORACLE_VERSION,
        "adapter_version": "agent-egress-control-adapter-v2",
        "case_class": "customer_control",
        "license_ref": "third_party/oss-pilots/agent-egress-bench/LICENSE",
        "execution_replicates": 1,
        "evidence_uri": evidence_uri,
        "evidence_sha256": evidence_sha256,
        "counts": summary,
        "results": results,
        "execution_boundary": "sealed-declarative-local-v1",
        "telemetry_verified": True,
        "telemetry": role_counts,
        "signing_key_id": signing_key_id,
        "signature_algorithm": SIGNATURE_ALGORITHM,
    }
    receipt["signature"] = base64.b64encode(
        signing_key.sign(_canonical_bytes(receipt))
    ).decode("ascii")
    return receipt


def verify_receipt(receipt: Mapping[str, Any], public_key: Ed25519PublicKey) -> bool:
    try:
        signature = base64.b64decode(str(receipt["signature"]), validate=True)
        unsigned = dict(receipt)
        del unsigned["signature"]
        public_key.verify(signature, _canonical_bytes(unsigned))
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


def corpus_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"
